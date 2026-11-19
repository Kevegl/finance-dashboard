#!/usr/bin/env python3
"""Finanz-Dashboard – schlanker Server ohne externe Abhängigkeiten (nur Python 3.10+ stdlib).

Umgebungsvariablen:
  FD_HOST        (Standard 0.0.0.0)
  FD_PORT        (Standard 8080)
  FD_DATA_DIR    (Standard ./data)      – hier liegt die SQLite-Datenbank
  FD_BASE_CCY    (Standard CHF)         – Basiswährung des Dashboards
  Anmeldung: wird beim ersten Aufruf im Browser festgelegt (Passwort-Hash in der Datenbank).
  Passwort vergessen:  python3 server.py --reset-password
  FD_CERT/FD_KEY                        – Pfad zu TLS-Zertifikat/Schlüssel -> HTTPS
  FD_PRICE_TTL   (Standard 14400 s)     – wie oft Kurse automatisch aktualisiert werden
  FD_REPO_DIR                           – Git-Repository der Installation (setzt install.sh) -> Updates im Dashboard
  FD_DOCKER=1                           – Docker-Image: meldet neue Releases, aktualisiert wird per docker pull
  FD_LOCAL=1                            – lokaler Betrieb über app/launcher.py (nur 127.0.0.1, ohne TLS,
                                          Updates über GitHub Releases, «Dashboard beenden»)
"""
import csv
import hmac
import io
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.parse
from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import importers
import local_update
from config import (APP_VERSION, COOKIE, CURRENCIES, DATA_DIR, DOCKER, LANGS, LOCAL, MAX_BODY, PF_KINDS, PRICE_TTL,
    SESSION_DAYS, STATIC, TLS_CERT, TLS_KEY)
from validate import import_problem, normalize_tx
from store import (db_tx, ensure_asset, get_lang, get_setting, init_db, list_portfolios, make_backup, restore_backup,
    set_setting, with_hashes)
from muni import MUNI_SOURCES, muni_state, refresh_muni_rates, save_muni_rates
from auth import (SETUP_CODE_FILE, ServerBusy, TOTP_LOCK, _th, auth_configured, auth_log_summary, check_new_password,
    create_session, hash_pw, log_auth, recovery_check, recovery_new, revoke_sessions, session_user, setup_code,
    setup_code_clear, setup_code_ok, setup_code_pending, setup_code_required, totp_active, totp_match,
    totp_new_secret, totp_off, totp_uri, totp_verify_login, verify_pw)
from prices import refresh_prices
from news import portfolio_news
from portfolio import compute_summary
from uploads import parse_uploaded_files
from updates import HTTPD, local_update_checker, request_exit, request_update, update_status


def local_starter():
    """Womit der Benutzer das Dashboard wieder öffnet (Text nach «Dashboard beenden»)."""
    win, mac = sys.platform.startswith("win"), sys.platform == "darwin"
    if getattr(sys, "frozen", False):          # .exe/App: der Server läuft im selben Programm
        return "FinanzDashboard.exe" if win else "FinanzDashboard.app" if mac else "FinanzDashboard"
    return "start.bat" if win else "start.command" if mac else "start.sh"
FAILED = {}            # IP -> [Zeitpunkte fehlgeschlagener Anmeldungen]
FAILED_LOCK = threading.Lock()
BLOCK_LOGGED = {}      # IP -> Zeitpunkt des letzten Protokolleintrags «login_blocked»
SETUP_LOCK = threading.Lock()   # Ersteinrichtung: nur eine gleichzeitig
MAX_FAILS, FAIL_WINDOW = 5, 900   # 5 Fehlversuche in 15 Min. -> 15 Min. gesperrt


def background_refresher():
    while True:
        try:
            refresh_prices()
        except Exception as e:  # noqa: BLE001
            print("Kurs-Aktualisierung fehlgeschlagen:", e)
        for canton in MUNI_SOURCES:   # Steuerfüsse höchstens einmal täglich prüfen
            try:
                refresh_muni_rates(canton)
            except Exception as e:  # noqa: BLE001
                print("Steuerfüsse laden fehlgeschlagen:", e)
        time.sleep(max(300, PRICE_TTL // 2))


MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".ico": "image/x-icon", ".json": "application/json", ".webmanifest": "application/manifest+json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "FinanceDashboard/1.0"
    timeout = 30

    def log_message(self, fmt, *args):
        if os.environ.get("FD_DEBUG"):
            super().log_message(fmt, *args)

    # --- Helfer
    def _pid(self, body=None):
        """Portfolio-ID aus Body (?) oder Query-String ?p=…; Fallback: erstes Portfolio."""
        raw = (body or {}).get("portfolio")
        if raw in (None, ""):
            raw = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("p") or [None])[0]
        with db_tx() as con:
            if raw not in (None, ""):
                try:
                    r = con.execute("SELECT id FROM portfolios WHERE id=?", (int(raw),)).fetchone()
                    if r:
                        return r[0]
                except ValueError:
                    pass
            return con.execute("SELECT id FROM portfolios ORDER BY sort, id LIMIT 1").fetchone()[0]

    def _token(self):
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001
            return None
        return c[COOKIE].value if COOKIE in c else None

    def _cookie(self, token, max_age):
        secure = "; Secure" if TLS_CERT and TLS_KEY else ""
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    def _reserve_attempt(self):
        """Prüft die Sperre und zählt den Versuch im selben Schritt vorab als Fehlversuch (atomar, damit parallele
        Anfragen die Grenze von 5 Versuchen nicht umgehen). Gibt eine Marke zurück oder None, wenn gesperrt.
        Bei Erfolg gibt _release_attempt(marke) den Versuch wieder frei."""
        ip, now = self.client_address[0], time.time()
        with FAILED_LOCK:
            for k in [k for k, v in FAILED.items() if not v or now - v[-1] >= FAIL_WINDOW]:
                del FAILED[k]                      # abgelaufene Einträge anderer IPs aufräumen
            hits = [t for t in FAILED.get(ip, []) if now - t < FAIL_WINDOW]
            if len(hits) >= MAX_FAILS:
                FAILED[ip] = hits
                return None
            hits.append(now)
            FAILED[ip] = hits
            return now

    def _release_attempt(self, stamp):
        if stamp is None:
            return
        with FAILED_LOCK:
            hits = FAILED.get(self.client_address[0], [])
            if stamp in hits:
                hits.remove(stamp)

    def _register_fail(self):
        with FAILED_LOCK:
            FAILED.setdefault(self.client_address[0], []).append(time.time())

    def _blocked_reply(self):
        self._log("login_blocked")
        return self._send(429, {"error": "Zu viele Fehlversuche – bitte 15 Minuten warten."})

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=list, allow_nan=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, limit=MAX_BODY):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            raise ValueError("Ungültige Anfrage")
        if n > limit:
            raise ValueError("Datei zu gross (max. 100 MB)" if limit == MAX_BODY else "Anfrage zu gross")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    PUBLIC = {"/api/auth/status", "/api/login", "/api/setup", "/api/health", "/api/recovery"}

    def _log(self, event):
        if event == "login_blocked":     # während einer Sperre nur ein Eintrag pro Sperrfenster (kein Verdrängen)
            ip, now = self.client_address[0], time.time()
            with FAILED_LOCK:
                if now - BLOCK_LOGGED.get(ip, 0) < FAIL_WINDOW:
                    return
                BLOCK_LOGGED[ip] = now
        log_auth(event, self.client_address[0], self.headers.get("User-Agent", ""))

    def _fail(self, status, msg, event=None, counted=False):
        """Fehlversuch: zählt zur Sperre (5 in 15 Min.), optional ins Protokoll, kurze Verzögerung.
        counted=True: der Versuch wurde schon per _reserve_attempt gezählt."""
        if not counted:
            self._register_fail()
        if event:
            self._log(event)
        time.sleep(1)
        return self._send(status, {"error": msg})

    def _new_session_reply(self, user, extra_body=None):
        token = create_session(user)
        return self._send(200, {"ok": True, **(extra_body or {})},
                          extra={"Set-Cookie": self._cookie(token, SESSION_DAYS * 86400)})

    def _local_host_ok(self):
        """Lokaler Betrieb: nur Aufrufe über 127.0.0.1/localhost annehmen. Schützt vor DNS-Rebinding (eine fremde
        Webseite lässt ihren Namen auf 127.0.0.1 zeigen und spricht so das Dashboard an, z.B. vor der Ersteinrichtung,
        die lokal keinen Einrichtungscode braucht)."""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        return host in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def _guard(self, path):
        if LOCAL and not self._local_host_ok():
            self._send(421, {"error": "Unbekannter Hostname – bitte über http://127.0.0.1 öffnen."})
            return False
        if self.command in ("POST", "PUT", "DELETE"):
            # Schutz vor Cross-Site-Requests: nur JSON von der eigenen Seite akzeptieren
            if not (self.headers.get("Content-Type") or "").startswith("application/json") and self.command != "DELETE":
                self._send(415, {"error": "Nur JSON erlaubt"})
                return False
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
                self._send(403, {"error": "Fremde Herkunft"})
                return False
        self.user = None
        if not path.startswith("/api/") or path in self.PUBLIC:
            return True
        if not auth_configured():
            self._send(401, {"error": "setup_required", "setup_code": setup_code_pending()})
            return False
        self.user = session_user(self._token())
        if not self.user:
            self._send(401, {"error": "login_required"})
            return False
        return True

    def _auth_api(self, path, body):
        """Öffentliche Endpunkte für Ersteinrichtung und Anmeldung."""
        if path == "/api/setup":
            with SETUP_LOCK:          # zwei gleichzeitige Einrichtungen: nur die erste gewinnt
                if auth_configured():
                    return self._send(409, {"error": "Das Dashboard ist bereits eingerichtet."})
                user = str(body.get("username") or "").strip()[:40] or "admin"
                check_new_password(body.get("password"))
                stamp = None
                if setup_code_required():
                    stamp = self._reserve_attempt()
                    if stamp is None:
                        return self._blocked_reply()
                    if not setup_code_ok(body.get("setup_code")):
                        return self._fail(403, "Der Einrichtungscode ist falsch.", "setup_fail", counted=True)
                set_setting("auth_user", user)
                set_setting("auth_hash", hash_pw(body["password"]))
                self._release_attempt(stamp)
                setup_code_clear()
                code = recovery_new()
                if body.get("lang") in LANGS:
                    set_setting("lang", body["lang"])
                token = create_session(user)
            self._log("setup")
            return self._send(200, {"ok": True, "user": user, "recovery_code": code},
                              extra={"Set-Cookie": self._cookie(token, SESSION_DAYS * 86400)})
        if path == "/api/login":
            stamp = self._reserve_attempt()
            if stamp is None:
                return self._blocked_reply()
            user = str(body.get("username") or "").strip()
            ok = auth_configured() and hmac.compare_digest(user.lower().encode(), (get_setting("auth_user") or "").lower().encode()) \
                and verify_pw(body.get("password") or "", get_setting("auth_hash"))
            if not ok:
                return self._fail(401, "Benutzername oder Passwort falsch.", "login_fail", counted=True)
            if totp_active():
                if not str(body.get("code") or "").strip():
                    self._release_attempt(stamp)                        # zählt nicht als Fehlversuch
                    return self._send(401, {"error": "totp_required"})
                if not totp_verify_login(body.get("code")):
                    return self._fail(401, "Der Code aus der Authenticator-App ist falsch.", "totp_fail", counted=True)
            self._release_attempt(stamp)
            token = create_session(get_setting("auth_user"))
            self._log("login_ok")
            return self._send(200, {"ok": True}, extra={"Set-Cookie": self._cookie(token, SESSION_DAYS * 86400)})
        if path == "/api/recovery":
            stamp = self._reserve_attempt()
            if stamp is None:
                return self._blocked_reply()
            if not auth_configured() or not recovery_check(body.get("code")):
                return self._fail(401, "Der Wiederherstellungscode ist falsch.", "recovery_fail", counted=True)
            self._release_attempt(stamp)
            check_new_password(body.get("password"))
            set_setting("auth_hash", hash_pw(body["password"]))
            revoke_sessions()
            totp_off()
            code = recovery_new()
            token = create_session(get_setting("auth_user"))
            self._log("recovery_used")
            return self._send(200, {"ok": True, "recovery_code": code},
                              extra={"Set-Cookie": self._cookie(token, SESSION_DAYS * 86400)})
        return None

    # --- Routing
    GET_ROUTES = {
        "/api/summary": "_get_summary",
        "/api/transactions": "_get_transactions",
        "/api/health": "_get_health",
        "/api/auth/status": "_get_auth_status",
        "/api/settings": "_get_settings",
        "/api/auth/log": "_get_auth_log",
        "/api/news": "_get_news",
        "/api/update/status": "_get_update_status",
        "/api/export.csv": "_get_export_csv",
        "/api/plan": "_get_plan",
        "/api/muni-rates": "_get_muni_rates",
        "/api/portfolios": "_get_portfolios",
        "/api/backup": "_get_backup",
    }

    POST_ROUTES = {
        "/api/logout": "_post_logout",
        "/api/import/preview": "_post_import",
        "/api/import/commit": "_post_import",
        "/api/transactions": "_post_transactions",
        "/api/transactions/delete": "_post_transactions_delete",
        "/api/plan": "_post_plan",
        "/api/muni-rates": "_post_muni_rates",
        "/api/muni-rates/refresh": "_post_muni_rates_refresh",
        "/api/muni-rates/auto": "_post_muni_rates_auto",
        "/api/settings": "_post_settings",
        "/api/portfolios": "_post_portfolios",
        "/api/portfolios/kind": "_post_portfolios_kind",
        "/api/portfolios/rename": "_post_portfolios_rename",
        "/api/portfolios/delete": "_post_portfolios_delete",
        "/api/restore": "_post_restore",
        "/api/transactions/delete-source": "_post_transactions_delete_source",
        "/api/refresh": "_post_refresh",
        "/api/update/request": "_post_update_request",
        "/api/local/quit": "_post_local_quit",
        "/api/account/password": "_post_account",
        "/api/account/totp/start": "_post_account",
        "/api/account/totp/confirm": "_post_account",
        "/api/account/totp/disable": "_post_account",
        "/api/account/recovery/new": "_post_account",
        "/api/account/logout-others": "_post_account",
    }

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._guard(path):
            return
        try:
            fn = self.GET_ROUTES.get(path)
            if fn:
                return getattr(self, fn)(path)
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._guard(path):
            return
        try:
            # ohne Anmeldung (Login, Einrichtung) nur kleine Anfragen annehmen
            body = self._json(64 * 1024 if path in self.PUBLIC else MAX_BODY)
            res = self._auth_api(path, body)
            if res is not None or path in self.PUBLIC:
                return res
            fn = self.POST_ROUTES.get(path)
            if fn:
                return getattr(self, fn)(path, body)
            return self._send(404, {"error": "nicht gefunden"})
        except ServerBusy:
            return self._send(503, {"error": "Zu viele gleichzeitige Anmeldeversuche – bitte kurz warten."})
        except importers.ImportErrorDetail as e:
            return self._send(422, {"error": str(e)})
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._guard(path):
            return
        mt = re.match(r"^/api/transactions/(\d+)$", path)
        if mt:
            try:
                return self._edit_tx(int(mt.group(1)), self._json())
            except (ValueError, KeyError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})
        m = re.match(r"^/api/assets/(.+)$", path)
        if not m:
            return self._send(404, {"error": "nicht gefunden"})
        try:
            sym = urllib.parse.unquote(m.group(1))
            b = self._json()
            mp = b.get("manual_price")
            mp = float(mp) if mp not in (None, "") else None
            with db_tx() as con:
                con.execute("UPDATE assets SET name=?, ticker=?, manual_price=?, asset_class=?, news_query=? WHERE symbol=?",
                            (b.get("name", ""), (b.get("ticker") or "").strip(), mp,
                             b.get("asset_class", "etf"), (b.get("news_query") or "").strip()[:80], sym))
            threading.Thread(target=refresh_prices, daemon=True).start()
            return self._send(200, {"ok": True})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._guard(path):
            return
        m = re.match(r"^/api/transactions/(\d+)$", path)
        if not m:
            return self._send(404, {"error": "nicht gefunden"})
        with db_tx() as con:
            con.execute("DELETE FROM transactions WHERE id=?", (int(m.group(1)),))
            self._cleanup_assets(con)
        return self._send(200, {"ok": True})

    # --- GET-/POST-Endpunkte (über GET_ROUTES/POST_ROUTES aufgerufen)
    def _get_summary(self, path):
        return self._send(200, compute_summary(self._pid()))

    def _get_transactions(self, path):
        with db_tx() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT id,date,type,asset,quantity,price,currency,fee,total,source,note "
                "FROM transactions WHERE portfolio_id=? ORDER BY date DESC, id DESC", (self._pid(),))]
        return self._send(200, rows)

    def _get_health(self, path):
        return self._send(200, {"ok": True, "app": "finance-dashboard", "local": LOCAL})

    def _get_auth_status(self, path):
        user = session_user(self._token()) if auth_configured() else None
        return self._send(200, {"setup_required": not auth_configured(), "setup_code": setup_code_pending(),
                                "authenticated": bool(user), "user": user, "lang": get_lang(),
                                "news": get_setting("news", "1") == "1"})

    def _get_settings(self, path):
        with db_tx() as con:
            n_sess = con.execute("SELECT COUNT(*) FROM sessions WHERE expires > ?", (time.time(),)).fetchone()[0]
            n_tx = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        return self._send(200, {"title": get_setting("title", "Portfolio"), "user": self.user, "base": config.BASE,
                                "currencies": CURRENCIES, "sessions": n_sess, "transactions": n_tx, "totp": totp_active(),
                                "totp_pending": bool(get_setting("auth_totp_pending")),
                                "recovery": bool(get_setting("auth_recovery_hash")), "https": bool(TLS_CERT and TLS_KEY),
                                "price_ttl_h": round(PRICE_TTL / 3600, 1),
                                "lang": get_lang(), "news": get_setting("news", "1") == "1",
                                "local": {"data_dir": str(DATA_DIR), "version": APP_VERSION,
                                          "starter": local_starter()} if LOCAL else None})

    def _get_auth_log(self, path):
        with db_tx() as con:
            r = con.execute("SELECT created FROM sessions WHERE token_hash=?", (_th(self._token() or ""),)).fetchone()
        return self._send(200, auth_log_summary(r["created"] if r else None))

    def _get_news(self, path):
        if get_setting("news", "1") != "1":
            return self._send(200, {"items": [], "enabled": False})
        force = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("force") == ["1"]
        return self._send(200, {"items": portfolio_news(self._pid(), get_lang(), force), "enabled": True})

    def _get_update_status(self, path):
        st = update_status()
        if urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("log") != ["1"]:
            st.pop("log", None)
        return self._send(200, st)

    def _get_export_csv(self, path):
        return self._export_csv(self._pid())

    def _get_plan(self, path):
        # Profil der Ansicht «Spartipps & Vorsorge»
        try:
            plan = json.loads(get_setting("plan", "{}"))
        except ValueError:
            plan = {}
        return self._send(200, plan if isinstance(plan, dict) else {})

    def _get_muni_rates(self, path):
        # Gemeindesteuerfüsse {Kanton: {Jahr: {Gemeinde: [...]}}} + Herkunft
        return self._send(200, muni_state())

    def _get_portfolios(self, path):
        return self._send(200, list_portfolios())

    def _get_backup(self, path):
        data = json.dumps(make_backup(), ensure_ascii=False, separators=(",", ":"))
        fname = f"finanz-dashboard-sicherung_{date.today().isoformat()}.json"
        return self._send(200, data, "application/json; charset=utf-8",
                          extra={"Content-Disposition": f'attachment; filename="{fname}"'})

    def _post_account(self, path, body):
        """Konto: Passwort, Zwei-Faktor, Wiederherstellungscode, andere Sitzungen abmelden."""
        stamp = None
        if path in ("/api/account/password", "/api/account/totp/start", "/api/account/totp/confirm",
                    "/api/account/totp/disable", "/api/account/recovery/new"):
            stamp = self._reserve_attempt()
            if stamp is None:
                return self._send(429, {"error": "Zu viele Fehlversuche – bitte 15 Minuten warten."})
        if path in ("/api/account/password", "/api/account/totp/start", "/api/account/totp/disable", "/api/account/recovery/new"):
            pw = body.get("current") if path == "/api/account/password" else body.get("password")
            if not verify_pw(pw or "", get_setting("auth_hash")):
                return self._fail(403, "Das aktuelle Passwort ist falsch.", "password_fail", counted=True)
            self._release_attempt(stamp)
            stamp = None
        if path == "/api/account/totp/start":
            if totp_active():
                return self._send(409, {"error": "Die Zwei-Faktor-Anmeldung ist bereits eingeschaltet."})
            secret = totp_new_secret()
            set_setting("auth_totp_pending", secret)
            return self._send(200, {"secret": secret, "uri": totp_uri(get_setting("auth_user"), secret)})
        if path == "/api/account/totp/confirm":
            pending = get_setting("auth_totp_pending")
            with TOTP_LOCK:
                step = totp_match(pending, body.get("code"))
                if not pending or step is None:
                    return self._fail(400, "Der Code aus der Authenticator-App ist falsch.", counted=True)
                self._release_attempt(stamp)
                set_setting("auth_totp_secret", pending)
                set_setting("auth_totp_last", str(step))
                set_setting("auth_totp_pending", None)
            self._log("totp_on")
            return self._send(200, {"ok": True})
        if path == "/api/account/totp/disable":
            if not totp_active() or not totp_verify_login(body.get("code")):
                return self._fail(403, "Der Code aus der Authenticator-App ist falsch.", "totp_fail")
            totp_off()
            self._log("totp_off")
            return self._send(200, {"ok": True})
        if path == "/api/account/recovery/new":
            code = recovery_new()
            self._log("recovery_new")
            return self._send(200, {"recovery_code": code})
        if path == "/api/account/password":
            if body.get("new"):
                check_new_password(body["new"])
            user = (body.get("username") or "").strip()[:40]
            if user:
                set_setting("auth_user", user)
            if body.get("new"):
                set_setting("auth_hash", hash_pw(body["new"]))
                revoke_sessions(keep_token=self._token())
                self._log("password_changed")
            return self._send(200, {"ok": True, "user": get_setting("auth_user")})
        if path == "/api/account/logout-others":
            revoke_sessions(keep_token=self._token())
            self._log("logout_others")
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "nicht gefunden"})

    def _post_logout(self, path, body):
        tok = self._token()
        if tok:
            with db_tx() as con:
                con.execute("DELETE FROM sessions WHERE token_hash=?", (_th(tok),))
        return self._send(200, {"ok": True}, extra={"Set-Cookie": self._cookie("", 0)})

    def _post_import(self, path, body):
        return self._import(body, commit=path.endswith("commit"))

    def _post_transactions(self, path, body):
        return self._add_manual(body)

    def _post_transactions_delete(self, path, body):
        ids = [int(i) for i in body.get("ids", [])]
        if not ids:
            raise ValueError("Keine Transaktionen ausgewählt")
        with db_tx() as con:
            n = 0
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                n += con.execute(f"DELETE FROM transactions WHERE id IN ({','.join('?' * len(chunk))})",
                                 chunk).rowcount
            self._cleanup_assets(con)
        return self._send(200, {"deleted": n})

    def _post_plan(self, path, body):
        if not isinstance(body, dict):
            raise ValueError("Ungültige Daten")
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if len(data) > 20000:
            raise ValueError("Daten zu gross")
        set_setting("plan", data)
        return self._send(200, {"ok": True})

    def _post_muni_rates(self, path, body):
        save_muni_rates(body)
        return self._send(200, muni_state())

    def _post_muni_rates_refresh(self, path, body):
        # jetzt beim Kanton prüfen
        refresh_muni_rates(str(body.get("canton") or "ZH"), force=bool(body.get("force")))
        return self._send(200, muni_state())

    def _post_muni_rates_auto(self, path, body):
        set_setting("muni_auto", "1" if body.get("enabled") else "0")
        return self._send(200, muni_state())

    def _post_settings(self, path, body):
        title = (body.get("title") or "").strip()[:60] or "Portfolio"
        base = (body.get("base") or config.BASE).upper()
        if base not in CURRENCIES:
            raise ValueError("Unbekannte Währung")
        changed = base != config.BASE
        lang = body.get("lang") if body.get("lang") in LANGS else get_lang()
        news = "1" if body.get("news", get_setting("news", "1") == "1") else "0"
        with db_tx() as con:
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('title',?)", (title,))
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('base_ccy',?)", (base,))
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('lang',?)", (lang,))
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('news',?)", (news,))
        config.BASE = base
        if changed:   # Wechselkurse für die neue Basiswährung laden
            threading.Thread(target=refresh_prices, daemon=True).start()
        return self._send(200, {"title": title, "base": base, "base_changed": changed,
                                "lang": lang, "news": news == "1"})

    def _post_portfolios(self, path, body):
        name = (body.get("name") or "").strip()[:40] or "Neues Portfolio"
        kind = body.get("kind") if body.get("kind") in PF_KINDS else "standard"
        with db_tx() as con:
            sort = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM portfolios").fetchone()[0]
            cur = con.execute("INSERT INTO portfolios(name, sort, kind) VALUES (?,?,?)", (name, sort, kind))
        return self._send(200, {"id": cur.lastrowid, "name": name, "kind": kind})

    def _post_portfolios_kind(self, path, body):
        kind = body.get("kind")
        if kind not in PF_KINDS:
            raise ValueError("Ungültiger Portfolio-Typ")
        with db_tx() as con:
            con.execute("UPDATE portfolios SET kind=? WHERE id=?", (kind, int(body["id"])))
        return self._send(200, {"ok": True, "kind": kind})

    def _post_portfolios_rename(self, path, body):
        name = (body.get("name") or "").strip()[:40]
        if not name:
            raise ValueError("Name fehlt")
        with db_tx() as con:
            con.execute("UPDATE portfolios SET name=? WHERE id=?", (name, int(body["id"])))
        return self._send(200, {"ok": True})

    def _post_portfolios_delete(self, path, body):
        pid = int(body["id"])
        with db_tx() as con:
            last = con.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] <= 1
            con.execute("DELETE FROM transactions WHERE portfolio_id=?", (pid,))
            con.execute("DELETE FROM portfolios WHERE id=?", (pid,))
            if last:   # Es braucht immer ein Portfolio -> leeres Hauptportfolio neu anlegen
                con.execute("INSERT INTO portfolios(name, sort) VALUES ('Hauptportfolio', 0)")
            self._cleanup_assets(con)
        return self._send(200, {"ok": True, "reset": last})

    def _post_restore(self, path, body):
        counts = restore_backup(body.get("backup"))
        threading.Thread(target=refresh_prices, daemon=True).start()
        return self._send(200, counts)

    def _post_transactions_delete_source(self, path, body):
        with db_tx() as con:
            n = con.execute("DELETE FROM transactions WHERE source=? AND portfolio_id=?",
                            (body.get("source"), self._pid(body))).rowcount
            self._cleanup_assets(con)
        return self._send(200, {"deleted": n})

    def _post_refresh(self, path, body):
        return self._send(200, refresh_prices(force=True))

    def _post_update_request(self, path, body):
        return self._send(200, {"ok": True, "request_id": request_update(body.get("action"))})

    def _post_local_quit(self, path, body):
        if not LOCAL:
            return self._send(404, {"error": "nicht gefunden"})
        request_exit(0)
        return self._send(200, {"ok": True})

    # --- Aktionen
    @staticmethod
    def _cleanup_assets(con):
        con.execute("DELETE FROM assets WHERE symbol NOT IN (SELECT DISTINCT asset FROM transactions)")

    def _import(self, body, commit):
        if body.get("files"):
            res = parse_uploaded_files(body["files"])
        else:
            text = body.get("text") or ""
            if not text.strip():
                raise ValueError("Leere Datei")
            res = importers.parse(text, body.get("provider") or "auto", body.get("mapping"), body.get("defaults"))
        valid = []
        for t in res["transactions"]:
            why = import_problem(t)
            if why:
                res["skipped"].append(f"{t.get('date') or '?'} {t.get('asset') or ''}: {why}".strip())
            else:
                valid.append(t)
        txs = with_hashes(valid)
        pid = self._pid(body)
        with db_tx() as con:
            existing = {r[0] for r in con.execute("SELECT hash FROM transactions WHERE portfolio_id=?", (pid,))}
            for t in txs:
                t["duplicate"] = t["hash"] in existing
            if commit:
                new = [t for t in txs if not t["duplicate"]]
                for t in new:
                    ensure_asset(con, t)
                    con.execute("""INSERT OR IGNORE INTO transactions
                        (portfolio_id,date,type,asset,quantity,price,currency,fee,total,source,note,hash)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (pid, t["date"], t["type"], t["asset"], t["quantity"], t["price"], t["currency"],
                                 t["fee"], t["total"], t["source"], t.get("note", ""), t["hash"]))
                # Assets für Dividenden ohne Kauf nicht anlegen; neue Kurse laden
                threading.Thread(target=refresh_prices, daemon=True).start()
                return self._send(200, {"imported": len(new), "duplicates": len(txs) - len(new),
                                        "skipped": len(res["skipped"]), "provider": res["provider"]})
        res["transactions"] = txs
        res["count_new"] = sum(1 for t in txs if not t["duplicate"])
        res["count_dup"] = len(txs) - res["count_new"]
        return self._send(200, res)

    def _add_manual(self, b):
        tx = normalize_tx(b, config.BASE)
        tx.update(source=str(b.get("source") or "Manuell"), asset_class=b.get("asset_class", "etf"),
                  name=b.get("name", ""))
        with_hashes([tx])
        tx["hash"] += f"-{time.time()}"
        with db_tx() as con:
            ensure_asset(con, tx)
            con.execute("""INSERT INTO transactions
                           (portfolio_id,date,type,asset,quantity,price,currency,fee,total,source,note,hash)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (self._pid(b), tx["date"], tx["type"], tx["asset"], tx["quantity"], tx["price"],
                         tx["currency"], tx["fee"], tx["total"], tx["source"], tx["note"], tx["hash"]))
        threading.Thread(target=refresh_prices, daemon=True).start()
        return self._send(200, {"ok": True})

    def _edit_tx(self, tx_id, b):
        tx = normalize_tx(b, config.BASE)
        with db_tx() as con:
            if not con.execute("SELECT 1 FROM transactions WHERE id=?", (tx_id,)).fetchone():
                return self._send(404, {"error": "Transaktion nicht gefunden"})
            ensure_asset(con, dict(asset=tx["asset"], asset_class=b.get("asset_class", "etf"), name=""))
            con.execute("""UPDATE transactions SET date=?, type=?, asset=?, quantity=?, price=?, currency=?,
                           fee=?, total=?, source=?, note=? WHERE id=?""",
                        (tx["date"], tx["type"], tx["asset"], tx["quantity"], tx["price"], tx["currency"], tx["fee"],
                         tx["total"], str(b.get("source") or "Manuell").strip(), tx["note"], tx_id))
            self._cleanup_assets(con)
        threading.Thread(target=refresh_prices, daemon=True).start()
        return self._send(200, {"ok": True})

    def _export_csv(self, pid):
        names = {"buy": "Kauf", "sell": "Verkauf", "dividend": "Dividende", "tax": "Quellensteuer",
                 "interest": "Zins", "fee": "Gebühr", "deposit": "Einzahlung", "withdrawal": "Auszahlung",
                 "transfer": "Transfer"}
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Datum", "Typ", "Asset", "Menge", "Kurs", "Gebühr", "Betrag", "Währung", "Quelle", "Notiz"])
        with db_tx() as con:
            for r in con.execute("SELECT * FROM transactions WHERE portfolio_id=? ORDER BY date, id", (pid,)):
                w.writerow([r["date"], names.get(r["type"], r["type"]), r["asset"], f'{r["quantity"]:.10g}',
                            f'{r["price"]:.10g}', f'{r["fee"]:.10g}', f'{r["total"]:.10g}', r["currency"],
                            r["source"], r["note"] or ""])
            pname = (con.execute("SELECT name FROM portfolios WHERE id=?", (pid,)).fetchone() or ["portfolio"])[0]
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", pname).strip("-") or "portfolio"
        fname = f"transaktionen_{safe}_{date.today().isoformat()}.csv"
        return self._send(200, "\ufeff" + buf.getvalue(), "text/csv; charset=utf-8",
                          extra={"Content-Disposition": f'attachment; filename="{fname}"'})

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        f = (STATIC / path.lstrip("/")).resolve()
        if not f.is_relative_to(STATIC.resolve()) or not f.is_file():
            f = STATIC / "index.html"
        return self._send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        err = sys.exc_info()[1]
        if isinstance(err, (ssl.SSLError, ConnectionError, TimeoutError)):
            return  # z.B. Aufruf per http:// auf dem HTTPS-Port oder abgebrochene Verbindung
        super().handle_error(request, client_address)


def main():
    init_db()
    if "--reset-password" in sys.argv:
        set_setting("auth_hash", None)
        set_setting("auth_user", None)
        totp_off()
        revoke_sessions()
        setup_code_clear()        # beim nächsten Start gibt es einen neuen Einrichtungscode
        log_auth("reset_cli", "lokal", "")
        print("Anmeldung zurückgesetzt. Beim nächsten Aufruf kann ein neues Passwort festgelegt werden.")
        if not LOCAL:
            print("Dafür braucht es den Einrichtungscode: Er erscheint im Protokoll des Dashboards und in "
                  f"{SETUP_CODE_FILE}, sobald das Dashboard läuft.")
        return
    threading.Thread(target=background_refresher, daemon=True).start()
    if LOCAL or DOCKER:
        threading.Thread(target=local_update_checker, daemon=True).start()
    host = os.environ.get("FD_HOST", "127.0.0.1" if LOCAL else "0.0.0.0")
    port = int(os.environ.get("FD_PORT", str(local_update.DEFAULT_PORT) if LOCAL else "8080"))
    httpd = Server((host, port), Handler)
    HTTPD["server"] = httpd
    scheme = "http"
    if TLS_CERT and TLS_KEY:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(TLS_CERT, TLS_KEY)
        # Handshake erst im Handler-Thread, damit ein langsamer Client den Server nicht blockiert
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
        scheme = "https"
    if not auth_configured():
        print("Ersteinrichtung: Dashboard im Browser öffnen und Benutzer + Passwort festlegen.")
        if setup_code_required():
            setup_code(announce=True)   # zeigt den Einrichtungscode im Protokoll an (legt ihn bei Bedarf an)
    ver = f"Version {APP_VERSION}, " if APP_VERSION else ""
    print(f"Finanz-Dashboard läuft auf {scheme}://{host}:{port}  ({ver}Basiswährung {config.BASE}, Daten in {DATA_DIR})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    sys.exit(HTTPD["exit"])


if __name__ == "__main__":
    main()
