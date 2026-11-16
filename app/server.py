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
import hashlib
import csv
import hmac
import io
import secrets
import ssl
import sys
from http.cookies import SimpleCookie
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import base64
import struct
import subprocess
import zipfile

import importers
import local_update

TX_TYPES = ("buy", "sell", "dividend", "tax", "interest", "fee", "deposit", "withdrawal", "transfer")
FLOW_TYPES = ("deposit", "withdrawal", "transfer")
PF_KINDS = ("standard", "3a")


def cash_effect(t, amount):
    """Wirkung einer Transaktion auf das Kontoguthaben (amount = Betrag in Basiswährung)."""
    if t["type"] == "buy":
        return -abs(amount)
    if t["type"] == "sell":
        return abs(amount)
    if t["type"] == "withdrawal":
        return -abs(amount)
    return amount            # deposit (+), transfer (±), dividend, interest (+), tax, fee (−)

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
DATA_DIR = Path(os.environ.get("FD_DATA_DIR", HERE.parent / "data"))
DB_PATH = DATA_DIR / "finance.db"
BASE = os.environ.get("FD_BASE_CCY", "CHF").upper()   # Startwert; in den Einstellungen änderbar
CURRENCIES = ["CHF", "EUR", "USD", "GBP", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN", "CZK", "HKD", "SGD"]
SESSION_DAYS = 30
COOKIE = "fd_session"
MIN_PW = 8
# Lokaler Betrieb (Startprogramm launcher.py): nur auf diesem Computer, Updates über GitHub Releases
LOCAL = os.environ.get("FD_LOCAL") == "1"
DOCKER = not LOCAL and os.environ.get("FD_DOCKER") == "1"
APP_VERSION = local_update.norm_version(os.environ.get("FD_APP_VERSION", "")) or local_update.app_version(HERE)
BUNDLED_VERSION = local_update.norm_version(os.environ.get("FD_BUNDLED_VERSION", ""))


def local_starter():
    """Womit der Benutzer das Dashboard wieder öffnet (Text nach «Dashboard beenden»)."""
    win, mac = sys.platform.startswith("win"), sys.platform == "darwin"
    if getattr(sys, "frozen", False):          # .exe/App: der Server läuft im selben Programm
        return "FinanzDashboard.exe" if win else "FinanzDashboard.app" if mac else "FinanzDashboard"
    return "start.bat" if win else "start.command" if mac else "start.sh"
TLS_CERT = None if LOCAL else os.environ.get("FD_CERT")
TLS_KEY = None if LOCAL else os.environ.get("FD_KEY")
FAILED = {}            # IP -> [Zeitpunkte fehlgeschlagener Anmeldungen]
FAILED_LOCK = threading.Lock()
BLOCK_LOGGED = {}      # IP -> Zeitpunkt des letzten Protokolleintrags «login_blocked»
MAX_FAILS, FAIL_WINDOW = 5, 900   # 5 Fehlversuche in 15 Min. -> 15 Min. gesperrt
PRICE_TTL = int(os.environ.get("FD_PRICE_TTL", "14400"))
# Updates über das Dashboard: der Server schreibt nur eine Auftragsdatei, ausgeführt wird sie vom
# root-Dienst finance-dashboard-update (systemd .path-Unit). Status liest er aus dessen Verzeichnis.
REPO_DIR = os.environ.get("FD_REPO_DIR", "")
UPDATE_STATUS = Path(os.environ.get("FD_UPDATE_STATUS", "/var/lib/finance-dashboard-update/status.json"))
UPDATE_ACTIONS = ("check", "update", "rollback")
MAX_BODY = 100 * 1024 * 1024

DB_LOCK = threading.RLock()
REFRESH_LOCK = threading.Lock()

EXCH_SUFFIX = {
    "LSE": ".L", "LSEETF": ".L", "LSEIOB1": ".L", "SWX": ".SW", "EBS": ".SW", "VIRTX": ".SW",
    "IBIS": ".DE", "IBIS2": ".DE", "XETRA": ".DE", "FWB": ".F", "FWB2": ".F", "AEB": ".AS",
    "SBF": ".PA", "BVME": ".MI", "BVME.ETF": ".MI", "ENEXT.BE": ".BR", "TSE": ".TO", "ASX": ".AX",
    "SEHK": ".HK", "NYSE": "", "NASDAQ": "", "ARCA": "", "AMEX": "", "BATS": "", "NYSEARCA": "",
}


# --------------------------------------------------------------------------
# Datenbank
# --------------------------------------------------------------------------

def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def get_setting(key, default=None):
    with DB_LOCK, db() as con:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(key, value):
    with DB_LOCK, db() as con:
        if value is None:
            con.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, value))


def json_setting(key, default):
    try:
        v = json.loads(get_setting(key, "") or "null")
    except ValueError:
        v = None
    return v if isinstance(v, type(default)) else default


def save_muni_rates(body, src="upload", info=None):
    """Steuerfüsse eines Jahres speichern oder löschen. rates: {Gemeinde: [[ohne Kirche, ref., kath., christkath.], …]}
    (mehrere Einträge, wenn eine Gemeinde je nach Schulgemeinde/Kirchgemeinde verschiedene Steuerfüsse hat)."""
    if not isinstance(body, dict):
        raise ValueError("Ungültige Daten")
    canton = str(body.get("canton") or "")
    year = str(body.get("year") or "")
    if not re.fullmatch(r"[A-Z]{2}", canton) or not re.fullmatch(r"20\d\d", year):
        raise ValueError("Ungültige Daten")
    try:
        allr = json.loads(get_setting("muni_rates", "{}"))
    except ValueError:
        allr = {}
    if not isinstance(allr, dict):
        allr = {}
    meta = json_setting("muni_meta", {})
    years_meta = meta.setdefault(canton, {}).setdefault("years", {})
    if body.get("delete"):
        allr.get(canton, {}).pop(year, None)
        years_meta.pop(year, None)
    else:
        rates = body.get("rates")
        if not isinstance(rates, dict) or not 0 < len(rates) <= 400:
            raise ValueError("Keine Steuerfüsse gefunden")
        clean = {}
        for name, variants in rates.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 60 or not isinstance(variants, list):
                raise ValueError("Ungültige Daten")
            vs = []
            for v in variants[:3]:
                if not isinstance(v, list) or len(v) != 4:
                    raise ValueError("Ungültige Daten")
                vs.append([float(x) if isinstance(x, (int, float)) and 0 <= x <= 400 else None for x in v])
            if not vs or vs[0][0] is None:
                raise ValueError("Ungültige Daten")
            clean[name.strip()] = vs
        allr.setdefault(canton, {})[year] = clean
        years_meta[year] = {"src": src, **(info or {})}
        if len(allr[canton]) > 10:   # nur die letzten 10 Jahre behalten
            for y in sorted(allr[canton])[:-10]:
                del allr[canton][y]
                years_meta.pop(y, None)
    set_setting("muni_rates", json.dumps(allr, ensure_ascii=False, separators=(",", ":")))
    set_setting("muni_meta", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    return {"ok": True, "years": sorted(allr.get(canton, {}))}


# Offene Daten der Kantone: aktuelle Gemeindesteuerfüsse (natürliche Personen) als CSV
MUNI_SOURCES = {
    "ZH": "https://www.web.statistik.zh.ch/ogd/data/steuerfuesse/kanton_zuerich_stf_aktuell.csv",
}
MUNI_CHECK_EVERY = 24 * 3600
MUNI_LOCK = threading.Lock()


def parse_zh_muni_csv(text):
    """CSV des Statistischen Amts ZH -> {Jahr: {Gemeinde: Varianten}}, {Jahr: {flag, refresh}}."""
    groups = ["STF_O_KIRCHE", "STF_REF", "STF_KATH", "STF_CKRKATH"]
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or not {"YEAR", "STF_O_KIRCHE1"} <= set(reader.fieldnames):
        raise ValueError("Unerwartetes Format der Steuerfuss-Daten")

    def num(x):
        try:
            v = float(str(x).strip())
        except ValueError:
            return None
        return v if 0 < v <= 400 else None   # leer oder 0 = keine weitere Variante
    data, info = {}, {}
    for row in reader:
        year, name = str(row.get("YEAR") or "").strip(), (row.get("COMMUNITY") or row.get("GDE_NAME") or "").strip()
        if not re.fullmatch(r"20\d\d", year) or not name:
            continue
        vals = [[num(row.get(f"{g}{i}")) for i in (1, 2, 3)] for g in groups]
        if vals[0][0] is None:
            continue
        n = max(3 if v[2] is not None else 2 if v[1] is not None else 1 for v in vals)
        variants = []
        for j in range(n):
            v = [col[j] if col[j] is not None else col[0] for col in vals]
            if v not in variants:
                variants.append(v)
        data.setdefault(year, {})[name] = variants
        info.setdefault(year, {"flag": (row.get("FLAG") or "").strip()[:20], "refresh": (row.get("LAST_REFRESH") or "").strip()[:20]})
    data = {y: rows for y, rows in data.items() if len(rows) >= 50}
    if not data:
        raise ValueError("Keine Steuerfüsse gefunden")
    return data, info


def refresh_muni_rates(canton="ZH", force=False):
    """Steuerfüsse beim Kanton abholen (höchstens einmal täglich). Hochgeladene Jahre werden nur mit force ersetzt."""
    url = MUNI_SOURCES.get(canton)
    if not url or (not force and get_setting("muni_auto", "1") != "1"):
        return
    with MUNI_LOCK:
        meta = json_setting("muni_meta", {})
        m = meta.get(canton, {})
        if not force and time.time() - m.get("checked", 0) < MUNI_CHECK_EVERY:
            return
        err = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv"})
            with local_update.urlopen(req, timeout=20) as r:
                text = r.read(2_000_000).decode("utf-8-sig", "replace")
            data, info = parse_zh_muni_csv(text)
            for year, rows in data.items():
                if not force and m.get("years", {}).get(year, {}).get("src") == "upload":
                    continue
                save_muni_rates({"canton": canton, "year": year, "rates": rows}, src="auto", info=info.get(year))
        except Exception as e:  # noqa: BLE001
            err = str(e)[:200]
            print("Steuerfüsse laden fehlgeschlagen:", err)
        meta = json_setting("muni_meta", {})   # neu lesen: save_muni_rates hat die Jahre ergänzt
        meta.setdefault(canton, {}).update(checked=time.time(), error=err)
        set_setting("muni_meta", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    if err and force:
        raise ValueError(f"Steuerfüsse konnten nicht geladen werden: {err}")


def muni_state():
    return {"rates": json_setting("muni_rates", {}), "meta": json_setting("muni_meta", {}),
            "auto": get_setting("muni_auto", "1") == "1", "sources": sorted(MUNI_SOURCES)}


# --------------------------------------------------------------------------
# Anmeldung (Passwort-Hash PBKDF2-SHA256, Sitzungen per HttpOnly-Cookie)
# --------------------------------------------------------------------------

def hash_pw(pw, iterations=310_000):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${h.hex()}"


def verify_pw(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it)).hex()
        return hmac.compare_digest(calc, h)
    except Exception:  # noqa: BLE001
        return False


def auth_configured():
    return bool(get_setting("auth_hash"))


def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with DB_LOCK, db() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        con.execute("INSERT INTO sessions(token_hash,user,created,expires) VALUES (?,?,?,?)",
                    (_th(token), user, now, now + SESSION_DAYS * 86400))
    return token


def session_user(token):
    if not token:
        return None
    with DB_LOCK, db() as con:
        r = con.execute("SELECT user, expires FROM sessions WHERE token_hash=?", (_th(token),)).fetchone()
    if not r or r["expires"] < time.time():
        return None
    return get_setting("auth_user") or r["user"]


def revoke_sessions(keep_token=None):
    with DB_LOCK, db() as con:
        if keep_token:
            con.execute("DELETE FROM sessions WHERE token_hash != ?", (_th(keep_token),))
        else:
            con.execute("DELETE FROM sessions")


def check_new_password(pw):
    if len(pw or "") < MIN_PW:
        raise ValueError(f"Das Passwort muss mindestens {MIN_PW} Zeichen lang sein.")


# --- Anmeldeprotokoll
AUTH_LOG_KEEP = 500
FAIL_EVENTS = ("login_fail", "totp_fail", "recovery_fail", "password_fail")


def log_auth(event, ip, detail=""):
    """Schreibt ein Anmeldeereignis ins Protokoll (letzte 500) und als Zeile auf stdout (Journal).
    detail ist nur ein gekürzter User-Agent o.ä. – nie Passwörter oder Codes."""
    detail = (detail or "")[:120]
    try:
        with DB_LOCK, db() as con:
            con.execute("INSERT INTO auth_log(ts, event, ip, detail) VALUES (?,?,?,?)",
                        (time.time(), event, ip or "", detail))
            con.execute("DELETE FROM auth_log WHERE id <= (SELECT MAX(id) FROM auth_log) - ?", (AUTH_LOG_KEEP,))
    except sqlite3.Error:
        pass
    print(f"Anmeldung: {event} von {ip or '-'}", flush=True)


def auth_log_summary(session_created):
    since = time.time() - 30 * 86400
    with DB_LOCK, db() as con:
        rows = [dict(r) for r in con.execute("SELECT ts, event, ip, detail FROM auth_log ORDER BY id DESC LIMIT 30")]
        last = con.execute("SELECT ts, ip FROM auth_log WHERE event IN ('login_ok','recovery_used','setup') AND ts < ? "
                           "ORDER BY ts DESC LIMIT 1", (session_created or time.time(),)).fetchone()
        fails = con.execute(f"SELECT COUNT(*) FROM auth_log WHERE ts >= ? AND event IN ({','.join('?' * len(FAIL_EVENTS))})",
                            (since, *FAIL_EVENTS)).fetchone()[0]
    return {"entries": rows, "last_login": dict(last) if last else None, "fails_30d": fails}


# --- Zwei-Faktor (TOTP, RFC 6238: HMAC-SHA1, 30 s, 6 Stellen)
TOTP_LOCK = threading.Lock()


def totp_new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return f"{(struct.unpack('>I', mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_match(secret, code, last_step=-1):
    """Gibt den passenden Zeitschritt zurück (Fenster ±1, nur neuer als last_step) oder None."""
    code = re.sub(r"\s", "", str(code or ""))
    if not secret or not re.fullmatch(r"[0-9]{6}", code):   # nur ASCII-Ziffern (compare_digest)
        return None
    now, found = int(time.time() // 30), None
    for step in (now - 1, now, now + 1):
        if hmac.compare_digest(totp_code(secret, step), code) and step > last_step and (found is None or step > found):
            found = step
    return found


def totp_active():
    return bool(get_setting("auth_totp_secret"))


def totp_verify_login(code):
    """Prüft den Code gegen das aktive Geheimnis und verbraucht den Zeitschritt (keine Wiederverwendung)."""
    with TOTP_LOCK:
        step = totp_match(get_setting("auth_totp_secret"), code, int(get_setting("auth_totp_last", "-1") or -1))
        if step is None:
            return False
        set_setting("auth_totp_last", str(step))
        return True


def totp_uri(user, secret):
    label = "Finanz-Dashboard:" + urllib.parse.quote(user or "", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer=Finanz-Dashboard&digits=6&period=30"


def totp_off():
    for k in ("auth_totp_secret", "auth_totp_pending", "auth_totp_last"):
        set_setting(k, None)


# --- Wiederherstellungscode (nur als PBKDF2-Hash gespeichert)
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # ohne 0/O/1/I/L


def recovery_norm(code):
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def recovery_new():
    raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(25))
    set_setting("auth_recovery_hash", hash_pw(raw))
    return "-".join(raw[i:i + 5] for i in range(0, 25, 5))


def recovery_check(code):
    stored = get_setting("auth_recovery_hash")
    norm = recovery_norm(code)
    return bool(stored) and len(norm) == 25 and verify_pw(norm, stored)


def list_portfolios():
    with DB_LOCK, db() as con:
        return [dict(r) for r in con.execute(
            "SELECT p.id, p.name, COALESCE(p.kind,'standard') AS kind, "
            "(SELECT COUNT(*) FROM transactions t WHERE t.portfolio_id=p.id) AS count "
            "FROM portfolios p ORDER BY p.sort, p.id")]


BACKUP_TABLES = {
    "portfolios": ["id", "name", "sort", "kind"],
    "assets": ["symbol", "name", "asset_class", "ticker", "manual_price", "color_slot", "news_query"],
    "transactions": ["id", "portfolio_id", "date", "type", "asset", "quantity", "price", "currency", "fee",
                     "total", "source", "note", "hash", "created_at"],
    "price_meta": ["ticker", "currency", "fetched_at", "error"],
}


def make_backup():
    """Vollständige Sicherung aller Daten – ohne Anmeldedaten und Sitzungen."""
    with DB_LOCK, db() as con:
        data = {"app": "finance-dashboard", "version": 2,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "base": BASE,
                "settings": {r["key"]: r["value"] for r in con.execute("SELECT * FROM settings")
                             if not r["key"].startswith("auth_")}}
        for table, cols in BACKUP_TABLES.items():
            data[table] = [[r[c] for c in cols] for r in con.execute(f"SELECT {','.join(cols)} FROM {table}")]
            data[table + "_columns"] = cols
        data["prices"] = [list(r) for r in con.execute("SELECT ticker, date, close FROM prices ORDER BY ticker, date")]
    return data


def restore_backup(data):
    """Ersetzt alle Daten durch die Sicherung. Login und Sitzungen bleiben unverändert."""
    if not isinstance(data, dict) or data.get("app") != "finance-dashboard":
        raise ValueError("Das ist keine gültige Sicherungsdatei des Finanz-Dashboards.")
    if not data.get("portfolios"):
        raise ValueError("Die Sicherung enthält keine Portfolios.")
    counts = {}
    with DB_LOCK, db() as con:
        for table in ("transactions", "assets", "portfolios", "prices", "price_meta"):
            con.execute(f"DELETE FROM {table}")
        for table, cols in BACKUP_TABLES.items():
            src_cols = data.get(table + "_columns") or cols
            use = [c for c in src_cols if c in cols]
            idx = [src_cols.index(c) for c in use]
            rows = [[row[i] for i in idx] for row in data.get(table, [])]
            con.executemany(f"INSERT INTO {table} ({','.join(use)}) VALUES ({','.join('?' * len(use))})", rows)
            counts[table] = len(rows)
        con.executemany("INSERT OR REPLACE INTO prices(ticker, date, close) VALUES (?,?,?)",
                        [tuple(r[:3]) for r in data.get("prices", [])])
        counts["prices"] = len(data.get("prices", []))
        if "kind" not in (data.get("portfolios_columns") or []):   # ältere Sicherung ohne Portfolio-Typ
            con.execute("""UPDATE portfolios SET kind='3a' WHERE id IN (SELECT DISTINCT portfolio_id FROM transactions
                           WHERE type IN ('deposit','withdrawal','transfer'))""")
        for k, v in (data.get("settings") or {}).items():
            if not k.startswith("auth_"):
                con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (k, v))
    return counts


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with DB_LOCK, db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL DEFAULT 1,
            date TEXT NOT NULL, type TEXT NOT NULL, asset TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0, price REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL, fee REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0,
            source TEXT NOT NULL, note TEXT DEFAULT '', hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (portfolio_id, hash)
        );
        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, sort INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS assets (
            symbol TEXT PRIMARY KEY, name TEXT DEFAULT '', asset_class TEXT DEFAULT 'etf',
            ticker TEXT DEFAULT '', manual_price REAL, color_slot INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user TEXT, created REAL, expires REAL
        );
        CREATE TABLE IF NOT EXISTS auth_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, event TEXT, ip TEXT, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS price_meta (
            ticker TEXT PRIMARY KEY, currency TEXT, fetched_at REAL, error TEXT
        );
        """)
        # Migration: Mehrere Portfolios (portfolio_id + Eindeutigkeit des Import-Hashes pro Portfolio)
        cols = [r[1] for r in con.execute("PRAGMA table_info(transactions)")]
        if "portfolio_id" not in cols:
            con.executescript("""
            ALTER TABLE transactions RENAME TO transactions_old;
            CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL DEFAULT 1,
            date TEXT NOT NULL, type TEXT NOT NULL, asset TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0, price REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL, fee REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0,
            source TEXT NOT NULL, note TEXT DEFAULT '', hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (portfolio_id, hash)
        );
            INSERT INTO transactions (id, portfolio_id, date, type, asset, quantity, price, currency, fee, total,
                                      source, note, hash, created_at)
                SELECT id, 1, date, type, asset, quantity, price, currency, fee, total, source, note, hash, created_at
                FROM transactions_old;
            DROP TABLE transactions_old;
            """)
        con.execute("CREATE INDEX IF NOT EXISTS ix_tx_pf ON transactions(portfolio_id, date)")
        if "news_query" not in [r[1] for r in con.execute("PRAGMA table_info(assets)")]:
            con.execute("ALTER TABLE assets ADD COLUMN news_query TEXT DEFAULT ''")
        if "kind" not in [r[1] for r in con.execute("PRAGMA table_info(portfolios)")]:
            con.execute("ALTER TABLE portfolios ADD COLUMN kind TEXT DEFAULT 'standard'")
            # bestehende Portfolios mit erfassten Einzahlungen/Transfers sind 3a-Konten
            con.execute("""UPDATE portfolios SET kind='3a' WHERE id IN (SELECT DISTINCT portfolio_id FROM transactions
                           WHERE type IN ('deposit','withdrawal','transfer'))""")
        if not con.execute("SELECT 1 FROM portfolios").fetchone():
            con.execute("INSERT INTO portfolios(id, name, sort) VALUES (1, 'Hauptportfolio', 0)")
        r = con.execute("SELECT value FROM settings WHERE key='base_ccy'").fetchone()
        if r and r[0] in CURRENCIES:
            global BASE
            BASE = r[0]
        # Migration: früher als Dividende mit Notiz "Quellensteuer: …" gespeichert -> eigener Typ "tax"
        old = con.execute("SELECT * FROM transactions WHERE type='dividend' AND note LIKE 'Quellensteuer:%'").fetchall()
        for r in old:
            tx = dict(r)
            tx.update(type="tax", total=-abs(tx["total"]), note=tx["note"].replace("Quellensteuer: ", "", 1))
            h = tx_hash(tx, 0)
            if con.execute("SELECT 1 FROM transactions WHERE hash=?", (h,)).fetchone():
                h = tx["hash"]
            con.execute("UPDATE transactions SET type='tax', total=?, note=?, hash=? WHERE id=?",
                        (tx["total"], tx["note"], h, tx["id"]))


def ensure_asset(con, tx):
    sym = tx["asset"]
    row = con.execute("SELECT * FROM assets WHERE symbol=?", (sym,)).fetchone()
    if row:
        if not row["name"] and tx.get("name"):
            con.execute("UPDATE assets SET name=? WHERE symbol=?", (tx["name"], sym))
        exch = (tx.get("listing_exchange") or "").upper()
        if not row["ticker"] and exch in EXCH_SUFFIX:
            con.execute("UPDATE assets SET ticker=? WHERE symbol=?", (sym.replace(" ", "-") + EXCH_SUFFIX[exch], sym))
        return
    used = {r[0] for r in con.execute("SELECT color_slot FROM assets")}
    free = [i for i in range(8) if i not in used]
    # Bitcoin bekommt bevorzugt Orange (Slot 2), sonst nächster freier Slot
    slot = 1 if (sym == "BTC" and 1 not in used) else next((i for i in free if not (i == 1 and sym != "BTC" and len(free) > 1)), 8)
    ticker = ""
    if sym == "BTC":
        ticker = f"BTC-{BASE}"
    elif tx.get("asset_class") == "crypto":
        ticker = f"{sym}-{BASE}"
    else:
        exch = (tx.get("listing_exchange") or "").upper()
        if exch in EXCH_SUFFIX:
            ticker = sym.replace(" ", "-") + EXCH_SUFFIX[exch]
    con.execute("INSERT INTO assets(symbol,name,asset_class,ticker,color_slot) VALUES (?,?,?,?,?)",
                (sym, tx.get("name") or ("Bitcoin" if sym == "BTC" else ""), tx.get("asset_class") or "etf",
                 ticker, slot))


def tx_hash(tx, occurrence):
    key = "|".join(str(x) for x in (tx["source"], tx["date"], tx["type"], tx["asset"],
                                     round(tx["quantity"], 8), round(tx["price"], 6),
                                     round(tx["total"], 4), occurrence))
    return hashlib.sha1(key.encode()).hexdigest()


def with_hashes(txs):
    seen = {}
    for tx in txs:
        base = tx_hash(tx, 0)
        n = seen.get(base, 0)
        seen[base] = n + 1
        tx["hash"] = tx_hash(tx, n)
    return txs


# --------------------------------------------------------------------------
# Kurse (Yahoo Finance, ohne API-Key)
# --------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with local_update.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_yahoo(ticker, since=None):
    period1 = int(datetime.combine(since, datetime.min.time()).timestamp()) if since else 0
    path = ("/v8/finance/chart/" + urllib.parse.quote(ticker) +
            f"?period1={period1}&period2={int(time.time()) + 86400}&interval=1d")
    last_err = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            data = http_json("https://" + host + path)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    else:
        raise RuntimeError(f"Yahoo: {last_err}")
    res = (data.get("chart") or {}).get("result")
    if not res:
        err = ((data.get("chart") or {}).get("error") or {}).get("description", "keine Daten")
        raise RuntimeError(f"Yahoo: {err}")
    res = res[0]
    cur = res["meta"].get("currency") or ""
    factor = 1.0
    if cur in ("GBp", "GBX"):
        cur, factor = "GBP", 0.01
    if cur == "ZAc":
        cur, factor = "ZAR", 0.01
    ts = res.get("timestamp") or []
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    out = []
    for t, c in zip(ts, closes):
        if c is not None:
            out.append((datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c * factor))
    live = res["meta"].get("regularMarketPrice")
    if live:
        out.append((date.today().isoformat(), live * factor))
    return cur.upper(), out


COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "ADA": "cardano", "XRP": "ripple",
                 "DOT": "polkadot", "LTC": "litecoin", "DOGE": "dogecoin", "LINK": "chainlink"}


def fetch_crypto(coin, ccy, since=None):
    """Krypto-Kurse über Kraken (Tageskerzen, bis ~2 Jahre) + CoinGecko als Live-Fallback."""
    out, errs = [], []
    kcoin = "XBT" if coin == "BTC" else coin
    try:
        since_ts = int(datetime.combine(since, datetime.min.time()).timestamp()) if since else 0
        data = http_json(f"https://api.kraken.com/0/public/OHLC?pair={kcoin}{ccy}&interval=1440&since={since_ts}")
        if data.get("error"):
            raise RuntimeError(", ".join(data["error"]))
        key = next(k for k in data["result"] if k != "last")
        for row in data["result"][key]:
            out.append((datetime.fromtimestamp(int(row[0]), timezone.utc).strftime("%Y-%m-%d"), float(row[4])))
    except Exception as e:  # noqa: BLE001
        errs.append(f"Kraken: {e}")
    try:
        cid = COINGECKO_IDS.get(coin, coin.lower())
        data = http_json(f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies={ccy.lower()}")
        live = data.get(cid, {}).get(ccy.lower())
        if live:
            out.append((date.today().isoformat(), float(live)))
    except Exception as e:  # noqa: BLE001
        errs.append(f"CoinGecko: {e}")
    # Lücke vor dem Kraken-Zeitraum (max. ~720 Tage) einmalig über Bitstamp auffüllen
    first = min((d for d, _ in out), default=None)
    if since and (first is None or first > (since + timedelta(days=3)).isoformat()):
        try:
            end = datetime.strptime(first, "%Y-%m-%d").date() if first else date.today()
            out += backfill_bitstamp(coin, ccy, since, end)
        except Exception as e:  # noqa: BLE001
            errs.append(f"Bitstamp: {e}")
    if not out:
        raise RuntimeError("; ".join(errs))
    return ccy, out


def backfill_bitstamp(coin, ccy, start, end):
    """Historische Tageskurse ab 2011 (Bitstamp: USD/EUR/GBP), bei Bedarf per EZB-Kurs umgerechnet."""
    quote = ccy if ccy in ("USD", "EUR", "GBP") else "USD"
    pair = f"{coin}{quote}".lower()
    rows = []
    t = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    while t < end_ts:
        data = http_json(f"https://www.bitstamp.net/api/v2/ohlc/{pair}/?step=86400&limit=1000&start={t}")
        candles = (data.get("data") or {}).get("ohlc") or []
        if not candles:
            break
        for c in candles:
            ts = int(c["timestamp"])
            if ts < end_ts:
                rows.append((datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"), float(c["close"])))
        t = int(candles[-1]["timestamp"]) + 86400
        if len(candles) < 1000:
            break
    if quote != ccy and rows:
        _, fx_rows = fetch_fx(quote, ccy, start)
        fx_s = Series(fx_rows)
        rows = [(d, v * fx_s.lookup(d)) for d, v in rows if fx_s.lookup(d)]
    return rows


def fetch_fx(frm, to, since=None):
    """Wechselkurse der EZB über frankfurter (kostenlos, ohne Key)."""
    start = (since or date.today() - timedelta(days=30)).isoformat()
    last_err = None
    for base in ("https://api.frankfurter.dev/v1", "https://api.frankfurter.app"):
        try:
            data = http_json(f"{base}/{start}..?base={frm}&symbols={to}")
            out = sorted((d, v[to]) for d, v in (data.get("rates") or {}).items() if to in v)
            if out:
                return to, out
            last_err = "keine Daten"
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"Frankfurter: {last_err}")


def fetch_prices(ticker, since=None):
    """Yahoo zuerst; bei Fehlern Ersatzquellen für Krypto (BTC-CHF) und Devisen (USDCHF=X)."""
    errors = []
    try:
        return fetch_yahoo(ticker, since)
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
    m = re.match(r"^([A-Z0-9]{2,6})-([A-Z]{3})$", ticker)
    if m and m.group(2) in importers.FIAT:
        try:
            return fetch_crypto(m.group(1), m.group(2), since)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    m = re.match(r"^([A-Z]{3})([A-Z]{3})=X$", ticker)
    if m:
        try:
            return fetch_fx(m.group(1), m.group(2), since)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    raise RuntimeError(" | ".join(errors))


# --- Fonds über die ISIN finden (z.B. 3a-Vorsorgefonds): Yahoo-Suche wie im Suchfeld von finance.yahoo.com
ISIN_RX = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
ISIN_RETRY = 7 * 86400


def yahoo_search(query):
    path = "/v1/finance/search?" + urllib.parse.urlencode({"q": query, "quotesCount": 8, "newsCount": 0,
                                                              "listsCount": 0, "enableFuzzyQuery": "false"})
    last = None
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        try:
            return http_json("https://" + host + path).get("quotes") or []
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"Yahoo-Suche: {last}")


def _rank(q):
    sym = q.get("symbol", "")
    return (0 if sym.endswith(".SW") or q.get("exchange") in ("EBS", "VTX", "SWX") else 1,
            0 if q.get("quoteType") in ("MUTUALFUND", "ETF") else 1)


def resolve_isin(isin, ref_date, ref_price, ref_ccy):
    """Yahoo-Symbol zur ISIN suchen und prüfen, ob dessen Kurs am Referenzdatum zum eigenen Kaufkurs passt."""
    quotes = [q for q in yahoo_search(isin) if q.get("symbol") and
              q.get("quoteType") in ("MUTUALFUND", "ETF", "EQUITY", None)]
    for q in sorted(quotes, key=_rank)[:4]:
        sym = q["symbol"]
        try:
            since = datetime.strptime(ref_date, "%Y-%m-%d").date() - timedelta(days=10)
            cur, rows = fetch_yahoo(sym, since)
        except Exception:  # noqa: BLE001
            continue
        if not rows:
            continue
        if not ref_price or cur != ref_ccy:
            return sym                      # ohne Vergleichskurs bzw. andere Währung: Treffer der ISIN-Suche nehmen
        near = [c for d, c in rows if d <= ref_date] or [rows[0][1]]
        px = near[-1]
        if px and abs(px - ref_price) / ref_price <= 0.15:   # falsche Anteilsklasse ausschliessen
            return sym
    return ""


def resolve_isin_tickers():
    """Für Positionen mit ISIN als Symbol und ohne Ticker automatisch den Yahoo-Ticker suchen."""
    try:
        done = json.loads(get_setting("isin_lookup", "{}") or "{}")
    except ValueError:
        done = {}
    with DB_LOCK, db() as con:
        todo = []
        for a in con.execute("SELECT symbol FROM assets WHERE COALESCE(ticker,'')='' AND manual_price IS NULL"):
            isin = a[0]
            if not ISIN_RX.match(isin):
                continue
            prev = done.get(isin)
            if prev and (prev.get("symbol") or time.time() - prev.get("at", 0) < ISIN_RETRY):
                continue        # schon gefunden (ggf. vom Benutzer wieder entfernt) oder kürzlich erfolglos
            ref = con.execute("""SELECT date, price, currency FROM transactions WHERE asset=? AND type IN ('buy','sell')
                                 AND price > 0 ORDER BY date DESC LIMIT 1""", (isin,)).fetchone()
            todo.append((isin, ref))
    changed = False
    for isin, ref in todo:
        try:
            sym = resolve_isin(isin, *(ref if ref else (date.today().isoformat(), None, None)))
        except Exception as e:  # noqa: BLE001
            print(f"ISIN-Suche {isin}: {e}")
            continue            # Netzwerkfehler: beim nächsten Lauf erneut versuchen
        done[isin] = {"symbol": sym, "at": time.time()}
        changed = True
        if sym:
            with DB_LOCK, db() as con:
                con.execute("UPDATE assets SET ticker=? WHERE symbol=? AND COALESCE(ticker,'')=''", (sym, isin))
    if changed:
        set_setting("isin_lookup", json.dumps(done))


def needed_tickers(con):
    """Alle Kurs- und Devisen-Ticker, die für das Portfolio gebraucht werden."""
    tickers = {}
    first = con.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
    if not first:
        return {}
    start = datetime.strptime(first, "%Y-%m-%d").date() - timedelta(days=7)
    for a in con.execute("SELECT * FROM assets WHERE ticker != ''"):
        tickers[a["ticker"]] = start
    curs = {r[0] for r in con.execute("SELECT DISTINCT currency FROM transactions")}
    curs |= {r[0] for r in con.execute("SELECT DISTINCT currency FROM price_meta WHERE currency IS NOT NULL")}
    for c in curs:
        if c and c != BASE:
            tickers[f"{c}{BASE}=X"] = start
    return tickers


def refresh_prices(force=False):
    if not REFRESH_LOCK.acquire(blocking=False):
        return {"status": "läuft bereits"}
    errors = {}
    try:
        try:
            resolve_isin_tickers()
        except Exception as e:  # noqa: BLE001
            print("ISIN-Suche fehlgeschlagen:", e)
        for _ in range(2):  # 2. Durchlauf: neu entdeckte Kurswährungen -> FX nachladen
            with DB_LOCK, db() as con:
                tickers = needed_tickers(con)
                meta = {r["ticker"]: r for r in con.execute("SELECT * FROM price_meta")}
            for t, start in tickers.items():
                m = meta.get(t)
                if m and not force and m["fetched_at"] and time.time() - m["fetched_at"] < PRICE_TTL:
                    continue
                with DB_LOCK, db() as con:
                    last = con.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (t,)).fetchone()[0]
                    first = con.execute("SELECT MIN(date) FROM prices WHERE ticker=?", (t,)).fetchone()[0]
                since = start
                # Nur neue Tage nachladen – ausser es fehlt noch Historie vor dem ersten gespeicherten Kurs
                has_gap = not first or first > (start + timedelta(days=5)).isoformat()
                if last and not has_gap:
                    since = max(start, datetime.strptime(last, "%Y-%m-%d").date() - timedelta(days=5))
                try:
                    cur, rows = fetch_prices(t, since)
                    with DB_LOCK, db() as con:
                        con.executemany("INSERT OR REPLACE INTO prices(ticker,date,close) VALUES (?,?,?)",
                                        [(t, d, c) for d, c in rows])
                        con.execute("INSERT OR REPLACE INTO price_meta(ticker,currency,fetched_at,error) VALUES (?,?,?,NULL)",
                                    (t, cur, time.time()))
                except Exception as e:  # noqa: BLE001
                    errors[t] = str(e)[:300]
                    with DB_LOCK, db() as con:
                        con.execute("""INSERT INTO price_meta(ticker,currency,fetched_at,error) VALUES (?,NULL,?,?)
                                       ON CONFLICT(ticker) DO UPDATE SET fetched_at=excluded.fetched_at, error=excluded.error""",
                                    (t, time.time(), str(e)[:300]))
    finally:
        REFRESH_LOCK.release()
    return {"status": "ok", "errors": errors}


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


# --------------------------------------------------------------------------
# Portfolio-Berechnung
# --------------------------------------------------------------------------

class Series:
    """Zeitreihe mit Forward-Fill-Lookup (für monoton steigende Abfragen)."""

    def __init__(self, rows):
        self.d = [r[0] for r in rows]
        self.v = [r[1] for r in rows]
        self.i = -1

    def at(self, day):
        while self.i + 1 < len(self.d) and self.d[self.i + 1] <= day:
            self.i += 1
        return self.v[self.i] if self.i >= 0 else (self.v[0] if self.v else None)

    def last(self):
        return self.v[-1] if self.v else None

    def lookup(self, day):  # zufälliger Zugriff
        import bisect
        k = bisect.bisect_right(self.d, day) - 1
        return self.v[k] if k >= 0 else (self.v[0] if self.v else None)



# --- News (Google News RSS, serverseitig abgerufen und zwischengespeichert)
LANGS = ("de", "en")
NEWS_TTL = 30 * 60
NEWS_CACHE = {}          # (query, lang) -> (fetched_at, items)
NEWS_LOCK = threading.Lock()
NEWS_REGION = {"de": "hl=de&gl=CH&ceid=CH:de", "en": "hl=en-US&gl=US&ceid=US:en"}
CRYPTO_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP Ripple", "ADA": "Cardano",
                "DOT": "Polkadot", "LTC": "Litecoin", "DOGE": "Dogecoin", "LINK": "Chainlink", "AVAX": "Avalanche"}
_NAME_NOISE = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|LTD|PLC|AG|SA|NV|SE|HOLDINGS?|GROUP|CL(ASS)?\s*[A-C]|-?CL\s*[A-C]|"
    r"UCITS|ETF|ACC|DIST|USD|EUR|CHF|SHS|REG|ORD|COM|NEW)\b\.?", re.I)


_ABBR = {"TOT": "Total", "STK": "Stock", "WRLD": "World", "WLD": "World", "INTL": "International", "MKT": "Market",
         "GRO": "", "GRP": "", "IDX": "Index", "EM": "Emerging Markets", "DEV": "Developed", "SM": "Small", "CAP": "Cap"}


def get_lang():
    v = get_setting("lang", "de")
    return v if v in LANGS else "de"


def news_query_for(a):
    q = (a.get("news_query") or "").strip()
    if q:
        return q
    sym = a["symbol"].upper()
    if a.get("asset_class") == "crypto":
        return CRYPTO_NAMES.get(sym, a.get("name") or sym)
    name = a.get("name") or ""
    if not name or name.upper() == sym:
        return sym
    shouting = name.isupper()
    words = [_ABBR.get(w.upper(), w) for w in re.sub(r"[()\[\],]", " ", name).replace("-", " ").split()]
    clean = re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", " ".join(words))).strip(" .,-")
    if shouting:   # "VANGUARD TOTAL WORLD" -> "Vanguard Total World", Abkürzungen wie MSCI bleiben
        clean = " ".join(w if len(w) <= 4 and w.isupper() and w not in ("CORE",) else w.title() for w in clean.split())
    return clean or sym


def parse_rss(xml_bytes, limit=6):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    items = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else ""
        if source and title.endswith(" - " + source):   # Google hängt die Quelle an den Titel an
            title = title[: -len(source) - 3].rstrip()
        ts = 0.0
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            pass
        if title and link.startswith(("https://", "http://")):
            items.append({"title": title, "link": link, "source": source, "published": ts})
        if len(items) >= limit:
            break
    return items


def fetch_news(query, lang, force=False):
    key = (query.lower(), lang)
    with NEWS_LOCK:
        hit = NEWS_CACHE.get(key)
    if hit and not force and time.time() - hit[0] < NEWS_TTL:
        return hit[1]
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:14d")
           + "&" + NEWS_REGION[lang])
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml"})
        with local_update.urlopen(req, timeout=10) as r:
            items = parse_rss(r.read(2_000_000), limit=20)
    except Exception as e:  # noqa: BLE001
        print(f"News für '{query}' ({lang}) nicht abrufbar: {e}")
        return hit[1] if hit else []
    with NEWS_LOCK:
        NEWS_CACHE[key] = (time.time(), items)
    return items


_GENERIC = {"total", "world", "stock", "stocks", "core", "index", "fund", "trust", "market", "markets", "international",
            "global", "all", "shares", "share", "the", "and", "group", "emerging", "developed", "small", "cap", "bond",
            "bonds", "dividend", "equity", "growth", "value", "high", "yield", "msci", "ftse", "s&p", "etf"}


def news_keywords(query, symbol):
    """Begriffe, von denen mindestens einer im Titel stehen muss, damit die Meldung wirklich zur Position passt."""
    words = [w for w in re.findall(r"[\w&.]+", query) if len(w) >= 4 and w.lower() not in _GENERIC]
    kw = {w.lower() for w in words}
    if len(symbol) >= 3 and symbol.isalpha():
        kw.add(symbol.lower())
    if not kw:                       # z.B. nur "MSCI World" -> ganze Phrase verlangen
        kw.add(query.lower())
    return kw


def relevant(items, kw):
    def hit(title):
        t = title.lower()
        return any(re.search(r"(?<![\w])" + re.escape(k) + r"(?![\w])", t) for k in kw)
    return [it for it in items if hit(it["title"])]


def asset_news(query, symbol, lang, force=False, want=4):
    """Erst gezielt (Begriff im Titel), dann breiter – aber immer nur Meldungen, die das Asset im Titel nennen."""
    kw = news_keywords(query, symbol)
    phrase = f'"{query}"' if " " in query else query
    strict = f"intitle:{phrase}"
    if len(symbol) >= 3 and symbol.isalpha() and symbol.lower() != query.lower():
        strict = f"({strict} OR intitle:{symbol})"
    items = relevant(fetch_news(strict, lang, force), kw)
    if len(items) < want:
        seen = {it["title"].lower() for it in items}
        items += [it for it in relevant(fetch_news(phrase, lang, force), kw) if it["title"].lower() not in seen]
    items.sort(key=lambda x: -x["published"])
    return items[:want]


def topic_news(queries, kw, lang, force=False, want=3):
    """Meldungen zu einem Thema: Suchanfragen der Reihe nach, bis genug passende Titel beisammen sind."""
    items, seen = [], set()
    for q in queries:
        for it in relevant(fetch_news(q, lang, force), kw):
            if it["title"].lower() not in seen:
                seen.add(it["title"].lower())
                items.append(it)
        if len(items) >= want:
            break
    items.sort(key=lambda x: -x["published"])
    return items[:want]


# Säule 3a: Vorsorgefonds (Symbol = ISIN) tauchen nie in Schlagzeilen auf -> Fondsname dem Markt zuordnen,
# in den er investiert. Label (Anzeige im Ticker), Muster im Fondsnamen, Suchanfragen und Pflichtbegriffe je Sprache.
PILLAR3A_TOPICS = [
    ("SMI", r"\b(SPI|SMI|SLI|Switzerland|Schweiz|Swiss|Suisse)\b",
     {"de": (["intitle:SMI", "Schweizer Börse"], {"smi", "spi", "schweizer börse", "schweizer aktien"}),
      "en": (["intitle:SMI", "Swiss stocks"], {"smi", "spi", "swiss stocks", "swiss shares", "swiss market"})}),
    ("World", r"\b(World|Welt|Global|ACWI|Developed|All Country)\b",
     {"de": (['"MSCI World"', "Weltbörsen", "Aktienmärkte"], {"msci world", "weltbörsen", "aktienmärkte", "aktienmarkt", "börsen"}),
      "en": (['"global stocks"', '"world stocks"', '"MSCI World"'], {"global stocks", "world stocks", "msci world", "global markets", "global equities"})}),
    ("USA", r"\b(USA?|America|Amerika|S&P|North America|Nordamerika)\b",
     {"de": (["Wall Street", '"S&P 500"'], {"wall street", "s&p 500", "dow jones", "nasdaq"}),
      "en": (["Wall Street", '"S&P 500"'], {"wall street", "s&p 500", "dow", "nasdaq"})}),
    ("Europe", r"\b(Europe|Europa|EMU|Eurozone|Stoxx)\b",
     {"de": (['"Euro Stoxx"', "Europas Börsen"], {"stoxx", "dax", "europas börsen", "europäische aktien"}),
      "en": (['"European stocks"', '"Stoxx 600"'], {"stoxx", "european stocks", "european shares", "dax"})}),
    ("EM", r"\b(Emerging|Schwellenl\w*|EM)\b",
     {"de": (["Schwellenländer Aktien", "Schwellenländer"], {"schwellenländer", "emerging markets"}),
      "en": (['"emerging markets" stocks', '"emerging markets"'], {"emerging markets", "emerging-market"})}),
    ("Asia", r"\b(Japan|Pacific|Pazifik|Asia|Asien|China)\b",
     {"de": (["Nikkei", "Asiens Börsen"], {"nikkei", "japan", "asiens börsen", "china"}),
      "en": (["Nikkei", '"Asian stocks"'], {"nikkei", "japan", "asian stocks", "asian markets"})}),
    ("Immo", r"\b(Real Estate|Immobilien\w*|Immo\w*|REIT\w*|Property|Properties)\b",
     {"de": (["Immobilienfonds Schweiz", "Immobilienmarkt Schweiz"], {"immobilienfonds", "immobilienmarkt", "immobilien"}),
      "en": (['"Swiss real estate"', '"real estate" Switzerland'], {"real estate", "property market", "reit"})}),
    ("SNB", r"\b(Bond\w*|Oblig\w*|Fixed Income|Anleihe\w*|Government|Corporate|Money Market|Geldmarkt)\b",
     {"de": (["SNB Leitzins", "Nationalbank Zinsen"], {"snb", "nationalbank", "leitzins", "zinsen"}),
      "en": (['"Swiss National Bank"', "SNB rates"], {"snb", "swiss national bank", "interest rate", "rates"})}),
    ("Gold", r"\b(Gold|Edelmetall\w*|Precious)\b",
     {"de": (["Goldpreis"], {"gold", "goldpreis"}), "en": (['"gold price"'], {"gold"})}),
]
PILLAR3A_GENERAL = {"de": (['"Säule 3a"', "intitle:3a Vorsorge"], {"säule 3a", "3a", "vorsorge"}),
                    "en": (['"pillar 3a"', '"third pillar" Switzerland'], {"pillar 3a", "3a", "third pillar"})}


def pillar3a_jobs(assets, sources, lang):
    """3a-Portfolio -> Liste (Label, Suchanfragen, Pflichtbegriffe): Märkte der Fonds, Anbieter, Säule 3a allgemein."""
    jobs, labels = [], set()

    def add(label, queries, kw):
        if label not in labels:
            labels.add(label)
            jobs.append((label, queries, kw))

    for a in assets:                      # nach Gewicht sortiert -> wichtigste Märkte zuerst
        if (a.get("news_query") or "").strip():          # eigener Suchbegriff hat Vorrang
            q = news_query_for(a)
            add(a["symbol"], None, (q, a["symbol"]))
            continue
        name = a.get("name") or ""
        hits = [(label, per_lang[lang]) for label, pattern, per_lang in PILLAR3A_TOPICS if re.search(pattern, name, re.I)]
        if any(label in ("Immo", "SNB", "Gold") for label, _ in hits):   # "Bond Switzerland" ist kein SMI-Fonds
            hits = [h for h in hits if h[0] in ("Immo", "SNB", "Gold")]
        for label, (queries, kw) in hits:
            add(label, queries, kw)
        if not hits and name and not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", a["symbol"]):
            add(a["symbol"], None, (news_query_for(a), a["symbol"]))   # z.B. Einzelaktie im 3a-Depot
    for src in sources:                   # Anbieter, z.B. "VIAC 3a" -> VIAC
        prov = src.replace("3a", "").strip()
        if prov:
            add(prov, [f"intitle:{prov}", prov], {prov.lower()})
    add("3a", *PILLAR3A_GENERAL[lang])
    return jobs


def pillar3a_news(pid, assets, lang, force=False, per_topic=3):
    with DB_LOCK, db() as con:
        sources = [r[0] for r in con.execute(
            "SELECT DISTINCT source FROM transactions WHERE portfolio_id=? AND source LIKE '%3a%'", (pid,))]
    jobs = pillar3a_jobs(assets, sources, lang)[:10]
    results = {}

    def run(label, queries, kw):
        if queries is None:               # normale Positions-Suche
            results[label] = asset_news(kw[0], kw[1], lang, force, per_topic)
        else:
            results[label] = topic_news(queries, kw, lang, force, per_topic)

    threads = [threading.Thread(target=run, args=j, daemon=True) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    out, seen = [], set()
    for label, _q, _kw in jobs:
        for it in results.get(label) or []:
            k = it["title"].lower()
            if k not in seen:
                seen.add(k)
                out.append(dict(it, asset=label))
    out.sort(key=lambda x: -x["published"])
    return out[:30]


def portfolio_news(pid, lang, force=False, max_assets=8, per_asset=4):
    with DB_LOCK, db() as con:
        kind = (con.execute("SELECT COALESCE(kind,'standard') FROM portfolios WHERE id=?", (pid,)).fetchone()
                or ["standard"])[0]
        rows = con.execute("""
            SELECT a.*, SUM(CASE WHEN t.type='buy' THEN t.quantity WHEN t.type='sell' THEN -t.quantity ELSE 0 END) AS q,
                   SUM(CASE WHEN t.type='buy' THEN ABS(t.total) ELSE 0 END) AS w
            FROM assets a JOIN transactions t ON t.asset = a.symbol AND t.portfolio_id = ?
            GROUP BY a.symbol HAVING q > 1e-9 ORDER BY w DESC""", (pid,)).fetchall()
    assets = [dict(r) for r in rows if r["asset_class"] != "cash"][:max_assets]
    if kind == "3a":
        return pillar3a_news(pid, assets, lang, force)
    queries = {}
    for a in assets:
        queries.setdefault(news_query_for(a), a["symbol"])
    out, seen = [], set()
    results = {}
    threads = [threading.Thread(target=lambda q=q, s=s: results.__setitem__(q, asset_news(q, s, lang, force, per_asset)),
                                daemon=True) for q, s in queries.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    for q, sym in queries.items():
        for it in (results.get(q) or [])[:per_asset]:
            k = it["title"].lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(dict(it, asset=sym))
    out.sort(key=lambda x: -x["published"])
    return out[:30]

def compute_summary(pid):
    with DB_LOCK, db() as con:
        txs = [dict(r) for r in con.execute("SELECT * FROM transactions WHERE portfolio_id=? ORDER BY date, id",
                                            (pid,))]
        row = con.execute("SELECT COALESCE(kind,'standard') FROM portfolios WHERE id=?", (pid,)).fetchone()
        kind = row[0] if row else "standard"
        assets = {r["symbol"]: dict(r) for r in con.execute("SELECT * FROM assets")}
        meta = {r["ticker"]: dict(r) for r in con.execute("SELECT * FROM price_meta")}

        def series(t):
            return Series([(r[0], r[1]) for r in con.execute(
                "SELECT date, close FROM prices WHERE ticker=? ORDER BY date", (t,))])

        price_series = {}
        price_ccy = {}
        for sym, a in assets.items():
            t = a["ticker"]
            if t and meta.get(t, {}).get("currency"):
                s = series(t)
                if s.v:
                    price_series[sym] = s
                    price_ccy[sym] = meta[t]["currency"]
        all_ccy = {t["currency"] for t in txs} | set(price_ccy.values())
        fx = {c: series(f"{c}{BASE}=X") for c in all_ccy if c != BASE}

    def fx_rate(cur, day=None):
        if cur == BASE:
            return 1.0
        s = fx.get(cur)
        if not s or not s.v:
            return None
        return s.lookup(day) if day else s.last()

    fx_missing = sorted(c for c in all_ccy if c != BASE and not (fx.get(c) and fx[c].v))

    # --- Bestände und Kostenbasis je Asset (Durchschnittskostenmethode, in Basiswährung)
    pos = {}
    events_by_day = {}
    for t in txs:
        rate = fx_rate(t["currency"], t["date"])
        if rate is None:
            rate = 1.0
        t["total_base"] = t["total"] * rate
        t["fee_base"] = (t["fee"] or 0) * rate
        events_by_day.setdefault(t["date"], []).append(t)

    def blank(sym):
        return dict(symbol=sym, qty=0.0, cost=0.0, cost_ccy=0.0, realized=0.0, dividends=0.0, taxes=0.0,
                    interest=0.0, acct_fees=0.0,
                    last_tx_price=None, last_tx_ccy=None, sources=set())

    for t in txs:
        p = pos.setdefault(t["asset"], blank(t["asset"]))
        p["sources"].add(t["source"])
        if t["type"] == "buy":
            p["qty"] += t["quantity"]
            p["cost"] += t["total_base"]
            p["cost_ccy"] += t["total"]
            if t["price"]:
                p["last_tx_price"], p["last_tx_ccy"] = t["price"], t["currency"]
        elif t["type"] == "sell":
            if p["qty"] > 0:
                share = min(1.0, t["quantity"] / p["qty"])
                removed = p["cost"] * share
                p["cost_ccy"] -= p["cost_ccy"] * share
                p["cost"] -= removed
                p["realized"] += t["total_base"] - removed
            rest = p["qty"] - t["quantity"]
            # Rundungsreste (Anbieter runden Anteile auf 2–3 Stellen) gelten als geschlossen
            p["qty"] = rest if rest > max(1e-8, p["qty"] * 1e-3) else 0.0
            if not p["qty"]:
                p["cost"] = p["cost_ccy"] = 0.0
            if t["price"]:
                p["last_tx_price"], p["last_tx_ccy"] = t["price"], t["currency"]
        elif t["type"] == "dividend":
            p["dividends"] += t["total_base"]
        elif t["type"] == "tax":
            p["taxes"] += t["total_base"]
        elif t["type"] == "interest":
            p["interest"] += t["total_base"]
        elif t["type"] == "fee":
            p["acct_fees"] += abs(t["total_base"])

    holdings = []
    for sym, p in pos.items():
        a = assets.get(sym, {})
        price, ccy, price_src = None, None, "—"
        if a.get("manual_price"):
            price, ccy, price_src = a["manual_price"], p["last_tx_ccy"] or BASE, "manuell"
        elif sym in price_series:
            price, ccy, price_src = price_series[sym].last(), price_ccy[sym], "live"
        elif p["last_tx_price"]:
            price, ccy, price_src = p["last_tx_price"], p["last_tx_ccy"], "letzter Kauf"
        rate = fx_rate(ccy) if ccy else None
        value = p["qty"] * price * (rate if rate is not None else 1.0) if price else 0.0
        holdings.append(dict(
            symbol=sym, name=a.get("name") or sym, asset_class=a.get("asset_class", "etf"),
            ticker=a.get("ticker", ""), manual_price=a.get("manual_price"), color_slot=a.get("color_slot", 0),
            news_query=a.get("news_query") or "",
            quantity=p["qty"], cost=p["cost"], avg_price=(p["cost_ccy"] / p["qty"]) if p["qty"] else None,
            avg_ccy=p["last_tx_ccy"], price=price, price_ccy=ccy, price_source=price_src,
            price_error=meta.get(a.get("ticker", ""), {}).get("error"),
            value=value, gain=value - p["cost"] if p["qty"] else 0.0,
            gain_pct=((value - p["cost"]) / p["cost"] * 100) if p["cost"] > 0 and p["qty"] else None,
            realized=p["realized"], dividends=p["dividends"], taxes=p["taxes"], sources=sorted(p["sources"]),
            interest=p["interest"], acct_fees=p["acct_fees"],
        ))
    # Portfolios mit erfassten Ein-/Auszahlungen (z.B. Säule 3a): Kontoguthaben und eigene Einlagen
    flow_txs = any(t["type"] in FLOW_TYPES for t in txs)
    has_flows = kind == "3a" and flow_txs      # nur 3a-Portfolios rechnen mit Einzahlungen und Kontoguthaben
    flows = None
    if has_flows:
        cash = sum(cash_effect(t, t["total_base"]) for t in txs)
        if -1.0 < cash < 0:     # Rundungsreste aus Belegen (z.B. −0.11 nach Saldierung) – kein echtes Minus
            cash = 0.0
        dep = sum(t["total_base"] for t in txs if t["type"] == "deposit")
        wd = sum(abs(t["total_base"]) for t in txs if t["type"] == "withdrawal")
        t_in = sum(t["total_base"] for t in txs if t["type"] == "transfer" and t["total_base"] > 0)
        t_out = -sum(t["total_base"] for t in txs if t["type"] == "transfer" and t["total_base"] < 0)
        net = dep - wd + t_in - t_out
        flows = dict(cash=cash, deposits=dep, withdrawals=wd, transfer_in=t_in, transfer_out=t_out,
                     own=dep - wd, net=net)
        h = next((x for x in holdings if x["symbol"] == "CASH"), None)
        if h is None:
            a = assets.get("CASH", {})
            h = dict(symbol="CASH", name=a.get("name") or "Konto", asset_class="cash", ticker="", manual_price=None,
                     color_slot=a.get("color_slot", 7), news_query="", cost=0.0, avg_price=None, avg_ccy=BASE,
                     price_error=None, realized=0.0, dividends=0.0, taxes=0.0, interest=0.0, acct_fees=0.0,
                     sources=[])
            holdings.append(h)
        h.update(name="Kontoguthaben", asset_class="cash", quantity=cash if abs(cash) >= 0.005 else 0.0,
                 price=1.0, price_ccy=BASE, price_source="Konto", value=cash, cost=cash, gain=0.0, gain_pct=None,
                 avg_price=1.0, avg_ccy=BASE, is_cash=True)
    holdings.sort(key=lambda h: -h["value"])
    total_value = sum(h["value"] for h in holdings)
    total_cost = sum(h["cost"] for h in holdings if h["quantity"] > 0 and not h.get("is_cash"))
    for h in holdings:
        h["weight"] = (h["value"] / total_value * 100) if total_value and h["value"] > 0 else 0

    # --- Tägliche Historie: Wert vs. investiertes Kapital
    history = []
    if txs:
        start = datetime.strptime(txs[0]["date"], "%Y-%m-%d").date()
        today = date.today()
        qty = {}
        cost = {}
        cost_ccy = {}
        last_px = {}
        fx_seq = {c: Series(list(zip(s.d, s.v))) for c, s in fx.items()}
        px_seq = {k: Series(list(zip(s.d, s.v))) for k, s in price_series.items()}
        manual = {s: a["manual_price"] for s, a in assets.items() if a.get("manual_price")}
        d = start
        cash_h = net_h = 0.0
        while d <= today:
            ds = d.isoformat()
            fx_today = {c: s.at(ds) for c, s in fx_seq.items()}
            for t in events_by_day.get(ds, []):
                s = t["asset"]
                if has_flows:
                    cash_h += cash_effect(t, t["total_base"])
                    if t["type"] in FLOW_TYPES:
                        net_h += t["total_base"] if t["type"] != "withdrawal" else -abs(t["total_base"])
                if t["type"] == "buy":
                    qty[s] = qty.get(s, 0) + t["quantity"]
                    cost[s] = cost.get(s, 0) + t["total_base"]
                    if t["price"]:
                        last_px[s] = (t["price"], t["currency"])
                elif t["type"] == "sell":
                    q = qty.get(s, 0)
                    if q > 0:
                        cost[s] = cost.get(s, 0) * (1 - min(1.0, t["quantity"] / q))
                    rest = q - t["quantity"]
                    qty[s] = rest if rest > max(1e-8, q * 1e-3) else 0.0
                    if t["price"]:
                        last_px[s] = (t["price"], t["currency"])
            value = 0.0
            for s, q in qty.items():
                if q <= 0:
                    continue
                if s in px_seq and (ds >= px_seq[s].d[0] or s not in last_px):
                    px, c = px_seq[s].at(ds), price_ccy[s]
                elif d == today and s in manual:
                    px, c = manual[s], last_px.get(s, (0, BASE))[1]
                else:
                    px, c = last_px.get(s, (0, BASE))
                r = 1.0 if c == BASE else (fx_today.get(c) or 1.0)
                value += q * (px or 0) * r
            if has_flows:   # Wert inkl. Konto, Linie "Investiert" = eingezahltes Geld (netto)
                history.append([ds, round(value + (0.0 if -1.0 < cash_h < 0 else cash_h), 2), round(net_h, 2)])
            else:
                history.append([ds, round(value, 2), round(sum(cost.values()), 2)])
            d += timedelta(days=1)
        if history and holdings:  # letzter Punkt = aktuelle Bewertung
            history[-1][1] = round(total_value, 2)

    pos_value = total_value - (flows["cash"] if flows else 0.0)
    stale = [m["ticker"] for m in meta.values() if m.get("error")]
    last_ok = max([m["fetched_at"] for m in meta.values() if m.get("fetched_at") and not m.get("error")] or [0])
    return dict(
        base=BASE,
        title=get_setting("title", "Portfolio"),
        kind=kind, flow_txs=flow_txs,
        kpis=dict(value=total_value, cost=total_cost, gain=pos_value - total_cost,
                  gain_pct=((pos_value - total_cost) / total_cost * 100) if total_cost else None,
                  cash=flows["cash"] if flows else None,
                  flows=flows,
                  gain_total=(total_value - flows["net"]) if flows else None,
                  gain_total_pct=((total_value - flows["net"]) / flows["net"] * 100) if flows and flows["net"] > 0 else None,
                  realized=sum(h["realized"] for h in holdings),
                  dividends=sum(h["dividends"] for h in holdings), taxes=sum(h["taxes"] for h in holdings),
                  count=len(txs),
                  interest=sum(h["interest"] for h in holdings),
                  account_fees=sum(h["acct_fees"] for h in holdings),
                  trade_fees=sum(t["fee_base"] for t in txs if t["type"] in ("buy", "sell")),
                  fees=sum(t["fee_base"] for t in txs if t["type"] in ("buy", "sell"))
                  + sum(h["acct_fees"] for h in holdings)),
        holdings=holdings, history=history,
        sources=sorted({t["source"] for t in txs}),
        price_errors=stale, fx_missing=fx_missing,
        last_refresh=last_ok or None,
    )




# --------------------------------------------------------------------------
# PDF-/ZIP-Import (z.B. VIAC-Belege): Text via pdftotext (Paket poppler-utils)
# --------------------------------------------------------------------------
MAX_DOCS = 3000


def pdf_text(data):
    try:
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"], input=data,
                           capture_output=True, timeout=30)
    except FileNotFoundError:
        raise ValueError("PDF-Import nicht verfügbar: pdftotext fehlt (install.sh erneut ausführen)")
    except subprocess.TimeoutExpired:
        return ""
    return r.stdout.decode("utf-8", "replace")


def parse_uploaded_files(files):
    """files: [{name, data(base64)}] mit PDFs, ZIPs (mit PDFs/CSVs) oder CSVs."""
    pdfs, csvs, seen = [], [], set()

    def add(name, raw):
        digest = hashlib.sha1(raw).hexdigest()
        if digest in seen:          # gleiche Datei doppelt ausgewählt
            return
        seen.add(digest)
        low = name.lower()
        if raw[:5] == b"%PDF-" or low.endswith(".pdf"):
            pdfs.append((name, raw))
        elif low.endswith((".csv", ".txt")):
            csvs.append((name, raw))

    for f in files:
        name = os.path.basename(str(f.get("name") or "datei"))
        try:
            raw = base64.b64decode(f.get("data") or "", validate=False)
        except ValueError:
            raise ValueError(f"{name}: Datei unlesbar")
        if raw[:4] == b"PK\x03\x04" or name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for info in z.infolist():
                        if info.is_dir() or info.file_size > 20_000_000 or "__MACOSX" in info.filename:
                            continue
                        if info.filename.lower().endswith((".pdf", ".csv", ".txt")):
                            add(os.path.basename(info.filename), z.read(info))
            except zipfile.BadZipFile:
                raise ValueError(f"{name}: ZIP-Datei beschädigt")
        else:
            add(name, raw)
        if len(pdfs) + len(csvs) > MAX_DOCS:
            raise ValueError(f"Zu viele Dateien (max. {MAX_DOCS})")
    if not pdfs and not csvs:
        raise ValueError("Keine PDF- oder CSV-Dateien gefunden")

    txs, skipped, providers = [], [], []
    if pdfs:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(2, min(8, os.cpu_count() or 2))) as ex:
            texts = list(ex.map(lambda p: pdf_text(p[1]), pdfs))
        docs = [(n, txt) for (n, _), txt in zip(pdfs, texts)]
        empty = [n for n, t in docs if not t.strip()]
        skipped += [f"{n}: kein Text im PDF (gescannt?)" for n in empty]
        t2, s2, prov = importers.parse_documents([d for d in docs if d[0] not in empty])
        txs += t2; skipped += s2; providers.append(prov)
    for n, raw in csvs:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:       # z.B. Excel-Export unter Windows
            text = raw.decode("cp1252", "replace")
        r = importers.parse(text)
        if r["needs_mapping"]:
            skipped.append(f"{n}: Format nicht erkannt – CSV einzeln importieren und Spalten zuordnen")
            continue
        txs += r["transactions"]; skipped += r["skipped"]; providers.append(r["provider"])
    txs.sort(key=lambda t: t["date"])
    prov = providers[0] if len(set(providers)) == 1 else "mixed"
    return dict(provider=prov, needs_mapping=False, transactions=txs, skipped=skipped, headers=[], sample=[],
                files=len(pdfs) + len(csvs))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

LOCAL_UPD = {"thread": None}
HTTPD = {"server": None, "exit": 0}


def request_exit(code):
    """Beendet den Server nach der laufenden Antwort (nur lokaler Betrieb)."""
    HTTPD["exit"] = code
    if HTTPD["server"]:
        threading.Timer(0.5, HTTPD["server"].shutdown).start()


def local_update_busy():
    t = LOCAL_UPD["thread"]
    return bool(t and t.is_alive())


def local_update_status():
    st = local_update.read_status(DATA_DIR)
    busy = st.get("state") in ("checking", "updating", "rolling_back")
    if busy and not local_update_busy():
        # Nach einem Neustart läuft hier kein Vorgang mehr -> unterbrochen
        busy = False
        st.update(state="error", message="Der letzte Lauf wurde unterbrochen.")
    cur = st.get("current") or {}
    if cur.get("commit") != APP_VERSION:
        st["current"] = local_update.version_info(APP_VERSION)
    mine = local_update.parse_version(APP_VERSION) or (0,)
    st["commits"] = [c for c in st.get("commits") or [] if (local_update.parse_version(c.get("short")) or (0,)) > mine]
    st["behind"] = len(st["commits"])
    prev = (st.get("previous") or {}).get("commit")
    if prev and prev == APP_VERSION:
        st["previous"] = None
    st.update(available=True, mode="docker" if DOCKER else "release", branch="Docker" if DOCKER else "Releases",
              pending=False, busy=busy, ahead=0, dirty=False)
    if DOCKER:
        st["previous"] = None
    return st


def local_request_update(action):
    if local_update_busy():
        raise ValueError("Es läuft bereits ein Update-Vorgang.")
    rid = secrets.token_hex(8)

    def work():
        u = local_update.LocalUpdater(DATA_DIR, APP_VERSION, BUNDLED_VERSION)
        if u.run(action, rid):
            request_exit(local_update.RESTART_CODE)

    LOCAL_UPD["thread"] = threading.Thread(target=work, daemon=True)
    LOCAL_UPD["thread"].start()
    return rid


def local_update_checker():
    """Prüft im lokalen Betrieb einmal täglich, ob es ein neues Release gibt (installiert nichts)."""
    time.sleep(30)
    while True:
        try:
            last = local_update.read_status(DATA_DIR).get("last_check")
            age = time.time() - datetime.fromisoformat(last).timestamp() if last else 1e9
            if age > 20 * 3600 and not local_update_busy():
                local_request_update("check")
        except Exception as e:  # noqa: BLE001
            print(f"Update-Prüfung: {e}", flush=True)
        time.sleep(3600)


def update_status():
    """Stand des Update-Dienstes für die Einstellungen (ohne Log, das gibt es separat)."""
    if LOCAL or DOCKER:
        return local_update_status()
    if not REPO_DIR:
        return {"available": False}
    try:
        st = json.loads(UPDATE_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st["available"] = True
    st["pending"] = (DATA_DIR / "update-request").exists()
    st["busy"] = st.get("state") in ("checking", "updating", "rolling_back")
    if st.get("updated_at") and st["busy"]:
        # Ein Lauf, der seit über 40 Minuten "läuft", ist abgebrochen (z.B. Container-Neustart)
        try:
            age = time.time() - datetime.fromisoformat(st["updated_at"]).timestamp()
            if age > 2400:
                st["busy"] = False
                st["state"] = "error"
                st["message"] = "Der letzte Lauf wurde unterbrochen."
        except ValueError:
            pass
    return st


def request_update(action):
    if action not in UPDATE_ACTIONS:
        raise ValueError("Unbekannte Aktion")
    if DOCKER and action != "check":
        raise ValueError("Im Docker-Betrieb wird über ein neues Image aktualisiert (docker compose pull).")
    if LOCAL or DOCKER:
        return local_request_update(action)
    if not REPO_DIR:
        raise ValueError("Updates über das Dashboard sind bei dieser Installation nicht verfügbar.")
    rid = secrets.token_hex(8)
    tmp = DATA_DIR / f".update-request-{rid}"
    tmp.write_text(f"{action} {rid}\n", encoding="ascii")
    os.replace(tmp, DATA_DIR / "update-request")
    return rid


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
        with DB_LOCK, db() as con:
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

    def _blocked(self):
        ip, now = self.client_address[0], time.time()
        with FAILED_LOCK:
            hits = [t for t in FAILED.get(ip, []) if now - t < FAIL_WINDOW]
            FAILED[ip] = hits
            return len(hits) >= MAX_FAILS

    def _register_fail(self):
        with FAILED_LOCK:
            FAILED.setdefault(self.client_address[0], []).append(time.time())

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=list).encode()
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

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Datei zu gross (max. 100 MB)")
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

    def _fail(self, status, msg, event=None):
        """Fehlversuch: zählt zur Sperre (5 in 15 Min.), optional ins Protokoll, kurze Verzögerung."""
        self._register_fail()
        if event:
            self._log(event)
        time.sleep(1)
        return self._send(status, {"error": msg})

    def _new_session_reply(self, user, extra_body=None):
        token = create_session(user)
        return self._send(200, {"ok": True, **(extra_body or {})},
                          extra={"Set-Cookie": self._cookie(token, SESSION_DAYS * 86400)})

    def _guard(self, path):
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
            self._send(401, {"error": "setup_required"})
            return False
        self.user = session_user(self._token())
        if not self.user:
            self._send(401, {"error": "login_required"})
            return False
        return True

    def _auth_api(self, path, body):
        """Öffentliche Endpunkte für Ersteinrichtung und Anmeldung."""
        if path == "/api/setup":
            if auth_configured():
                return self._send(409, {"error": "Das Dashboard ist bereits eingerichtet."})
            user = (body.get("username") or "").strip()[:40] or "admin"
            check_new_password(body.get("password"))
            set_setting("auth_user", user)
            set_setting("auth_hash", hash_pw(body["password"]))
            code = recovery_new()
            if body.get("lang") in LANGS:
                set_setting("lang", body["lang"])
            token = create_session(user)
            self._log("setup")
            return self._send(200, {"ok": True, "user": user, "recovery_code": code},
                              extra={"Set-Cookie": self._cookie(token, SESSION_DAYS * 86400)})
        if path == "/api/login":
            if self._blocked():
                self._log("login_blocked")
                return self._send(429, {"error": "Zu viele Fehlversuche – bitte 15 Minuten warten."})
            user = (body.get("username") or "").strip()
            ok = auth_configured() and hmac.compare_digest(user.lower().encode(), (get_setting("auth_user") or "").lower().encode()) \
                and verify_pw(body.get("password") or "", get_setting("auth_hash"))
            if not ok:
                self._register_fail()
                self._log("login_fail")
                time.sleep(1)
                return self._send(401, {"error": "Benutzername oder Passwort falsch."})
            if totp_active():
                if not str(body.get("code") or "").strip():
                    return self._send(401, {"error": "totp_required"})   # zählt nicht als Fehlversuch
                if not totp_verify_login(body.get("code")):
                    self._register_fail()
                    self._log("totp_fail")
                    time.sleep(1)
                    return self._send(401, {"error": "Der Code aus der Authenticator-App ist falsch."})
            token = create_session(get_setting("auth_user"))
            self._log("login_ok")
            return self._send(200, {"ok": True}, extra={"Set-Cookie": self._cookie(token, SESSION_DAYS * 86400)})
        if path == "/api/recovery":
            if self._blocked():
                self._log("login_blocked")
                return self._send(429, {"error": "Zu viele Fehlversuche – bitte 15 Minuten warten."})
            if not auth_configured() or not recovery_check(body.get("code")):
                self._register_fail()
                self._log("recovery_fail")
                time.sleep(1)
                return self._send(401, {"error": "Der Wiederherstellungscode ist falsch."})
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
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._guard(path):
            return
        try:
            if path == "/api/summary":
                return self._send(200, compute_summary(self._pid()))
            if path == "/api/transactions":
                with DB_LOCK, db() as con:
                    rows = [dict(r) for r in con.execute(
                        "SELECT id,date,type,asset,quantity,price,currency,fee,total,source,note "
                        "FROM transactions WHERE portfolio_id=? ORDER BY date DESC, id DESC", (self._pid(),))]
                return self._send(200, rows)
            if path == "/api/health":
                return self._send(200, {"ok": True, "app": "finance-dashboard", "local": LOCAL})
            if path == "/api/auth/status":
                user = session_user(self._token()) if auth_configured() else None
                return self._send(200, {"setup_required": not auth_configured(),
                                        "authenticated": bool(user), "user": user, "lang": get_lang(),
                                        "news": get_setting("news", "1") == "1"})
            if path == "/api/settings":
                with DB_LOCK, db() as con:
                    n_sess = con.execute("SELECT COUNT(*) FROM sessions WHERE expires > ?", (time.time(),)).fetchone()[0]
                    n_tx = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
                return self._send(200, {"title": get_setting("title", "Portfolio"), "user": self.user, "base": BASE,
                                        "currencies": CURRENCIES, "sessions": n_sess, "transactions": n_tx, "totp": totp_active(),
                                        "totp_pending": bool(get_setting("auth_totp_pending")),
                                        "recovery": bool(get_setting("auth_recovery_hash")), "https": bool(TLS_CERT and TLS_KEY),
                                        "price_ttl_h": round(PRICE_TTL / 3600, 1),
                                        "lang": get_lang(), "news": get_setting("news", "1") == "1",
                                        "local": {"data_dir": str(DATA_DIR), "version": APP_VERSION,
                                                  "starter": local_starter()} if LOCAL else None})
            if path == "/api/auth/log":
                with DB_LOCK, db() as con:
                    r = con.execute("SELECT created FROM sessions WHERE token_hash=?", (_th(self._token() or ""),)).fetchone()
                return self._send(200, auth_log_summary(r["created"] if r else None))
            if path == "/api/news":
                if get_setting("news", "1") != "1":
                    return self._send(200, {"items": [], "enabled": False})
                force = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("force") == ["1"]
                return self._send(200, {"items": portfolio_news(self._pid(), get_lang(), force), "enabled": True})
            if path == "/api/update/status":
                st = update_status()
                if urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("log") != ["1"]:
                    st.pop("log", None)
                return self._send(200, st)
            if path == "/api/export.csv":
                return self._export_csv(self._pid())
            if path == "/api/plan":   # Profil der Ansicht «Spartipps & Vorsorge»
                try:
                    plan = json.loads(get_setting("plan", "{}"))
                except ValueError:
                    plan = {}
                return self._send(200, plan if isinstance(plan, dict) else {})
            if path == "/api/muni-rates":   # Gemeindesteuerfüsse {Kanton: {Jahr: {Gemeinde: [...]}}} + Herkunft
                return self._send(200, muni_state())
            if path == "/api/portfolios":
                return self._send(200, list_portfolios())
            if path == "/api/backup":
                data = json.dumps(make_backup(), ensure_ascii=False, separators=(",", ":"))
                fname = f"finanz-dashboard-sicherung_{date.today().isoformat()}.json"
                return self._send(200, data, "application/json; charset=utf-8",
                                  extra={"Content-Disposition": f'attachment; filename="{fname}"'})
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._guard(path):
            return
        try:
            body = self._json()
            res = self._auth_api(path, body)
            if res is not None or path in self.PUBLIC:
                return res
            if path == "/api/logout":
                tok = self._token()
                if tok:
                    with DB_LOCK, db() as con:
                        con.execute("DELETE FROM sessions WHERE token_hash=?", (_th(tok),))
                return self._send(200, {"ok": True}, extra={"Set-Cookie": self._cookie("", 0)})
            if path in ("/api/account/password", "/api/account/totp/start", "/api/account/totp/confirm",
                        "/api/account/totp/disable", "/api/account/recovery/new") and self._blocked():
                return self._send(429, {"error": "Zu viele Fehlversuche – bitte 15 Minuten warten."})
            if path in ("/api/account/password", "/api/account/totp/start", "/api/account/totp/disable", "/api/account/recovery/new"):
                pw = body.get("current") if path == "/api/account/password" else body.get("password")
                if not verify_pw(pw or "", get_setting("auth_hash")):
                    return self._fail(403, "Das aktuelle Passwort ist falsch.", "password_fail")
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
                        return self._fail(400, "Der Code aus der Authenticator-App ist falsch.")
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
            if path in ("/api/import/preview", "/api/import/commit"):
                return self._import(body, commit=path.endswith("commit"))
            if path == "/api/transactions":
                return self._add_manual(body)
            if path == "/api/transactions/delete":
                ids = [int(i) for i in body.get("ids", [])]
                if not ids:
                    raise ValueError("Keine Transaktionen ausgewählt")
                with DB_LOCK, db() as con:
                    n = 0
                    for i in range(0, len(ids), 500):
                        chunk = ids[i:i + 500]
                        n += con.execute(f"DELETE FROM transactions WHERE id IN ({','.join('?' * len(chunk))})",
                                         chunk).rowcount
                    self._cleanup_assets(con)
                return self._send(200, {"deleted": n})
            if path == "/api/plan":
                if not isinstance(body, dict):
                    raise ValueError("Ungültige Daten")
                data = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
                if len(data) > 20000:
                    raise ValueError("Daten zu gross")
                set_setting("plan", data)
                return self._send(200, {"ok": True})
            if path == "/api/muni-rates":
                save_muni_rates(body)
                return self._send(200, muni_state())
            if path == "/api/muni-rates/refresh":   # jetzt beim Kanton prüfen
                refresh_muni_rates(str(body.get("canton") or "ZH"), force=bool(body.get("force")))
                return self._send(200, muni_state())
            if path == "/api/muni-rates/auto":
                set_setting("muni_auto", "1" if body.get("enabled") else "0")
                return self._send(200, muni_state())
            if path == "/api/settings":
                global BASE
                title = (body.get("title") or "").strip()[:60] or "Portfolio"
                base = (body.get("base") or BASE).upper()
                if base not in CURRENCIES:
                    raise ValueError("Unbekannte Währung")
                changed = base != BASE
                lang = body.get("lang") if body.get("lang") in LANGS else get_lang()
                news = "1" if body.get("news", get_setting("news", "1") == "1") else "0"
                with DB_LOCK, db() as con:
                    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('title',?)", (title,))
                    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('base_ccy',?)", (base,))
                    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('lang',?)", (lang,))
                    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('news',?)", (news,))
                BASE = base
                if changed:   # Wechselkurse für die neue Basiswährung laden
                    threading.Thread(target=refresh_prices, daemon=True).start()
                return self._send(200, {"title": title, "base": base, "base_changed": changed,
                                        "lang": lang, "news": news == "1"})
            if path == "/api/portfolios":
                name = (body.get("name") or "").strip()[:40] or "Neues Portfolio"
                kind = body.get("kind") if body.get("kind") in PF_KINDS else "standard"
                with DB_LOCK, db() as con:
                    sort = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM portfolios").fetchone()[0]
                    cur = con.execute("INSERT INTO portfolios(name, sort, kind) VALUES (?,?,?)", (name, sort, kind))
                return self._send(200, {"id": cur.lastrowid, "name": name, "kind": kind})
            if path == "/api/portfolios/kind":
                kind = body.get("kind")
                if kind not in PF_KINDS:
                    raise ValueError("Ungültiger Portfolio-Typ")
                with DB_LOCK, db() as con:
                    con.execute("UPDATE portfolios SET kind=? WHERE id=?", (kind, int(body["id"])))
                return self._send(200, {"ok": True, "kind": kind})
            if path == "/api/portfolios/rename":
                name = (body.get("name") or "").strip()[:40]
                if not name:
                    raise ValueError("Name fehlt")
                with DB_LOCK, db() as con:
                    con.execute("UPDATE portfolios SET name=? WHERE id=?", (name, int(body["id"])))
                return self._send(200, {"ok": True})
            if path == "/api/portfolios/delete":
                pid = int(body["id"])
                with DB_LOCK, db() as con:
                    last = con.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] <= 1
                    con.execute("DELETE FROM transactions WHERE portfolio_id=?", (pid,))
                    con.execute("DELETE FROM portfolios WHERE id=?", (pid,))
                    if last:   # Es braucht immer ein Portfolio -> leeres Hauptportfolio neu anlegen
                        con.execute("INSERT INTO portfolios(name, sort) VALUES ('Hauptportfolio', 0)")
                    self._cleanup_assets(con)
                return self._send(200, {"ok": True, "reset": last})
            if path == "/api/restore":
                counts = restore_backup(body.get("backup"))
                threading.Thread(target=refresh_prices, daemon=True).start()
                return self._send(200, counts)
            if path == "/api/transactions/delete-source":
                with DB_LOCK, db() as con:
                    n = con.execute("DELETE FROM transactions WHERE source=? AND portfolio_id=?",
                                    (body.get("source"), self._pid(body))).rowcount
                    self._cleanup_assets(con)
                return self._send(200, {"deleted": n})
            if path == "/api/refresh":
                return self._send(200, refresh_prices(force=True))
            if path == "/api/update/request":
                return self._send(200, {"ok": True, "request_id": request_update(body.get("action"))})
            if path == "/api/local/quit" and LOCAL:
                request_exit(0)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "nicht gefunden"})
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
        m = re.match(r"^/api/assets/(.+)$", path)
        if not m:
            return self._send(404, {"error": "nicht gefunden"})
        try:
            sym = urllib.parse.unquote(m.group(1))
            b = self._json()
            mp = b.get("manual_price")
            mp = float(mp) if mp not in (None, "") else None
            with DB_LOCK, db() as con:
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
        with DB_LOCK, db() as con:
            con.execute("DELETE FROM transactions WHERE id=?", (int(m.group(1)),))
            self._cleanup_assets(con)
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
        txs = with_hashes(res["transactions"])
        pid = self._pid(body)
        with DB_LOCK, db() as con:
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
        typ = b.get("type", "buy")
        if typ not in TX_TYPES:
            raise ValueError("Ungültiger Typ")
        qty = float(b.get("quantity") or 0)
        price = float(b.get("price") or 0)
        fee = float(b.get("fee") or 0)
        total = b.get("total")
        total = float(total) if total not in (None, "") else (qty * price + (fee if typ == "buy" else -fee))
        if typ in ("tax", "fee", "withdrawal"):
            total = -abs(total)
        elif typ in ("interest", "deposit"):
            total = abs(total)
        tx = dict(date=b["date"], type=typ, asset=b["asset"].strip().upper(), quantity=qty, price=price,
                  currency=(b.get("currency") or BASE).upper(), fee=fee, total=total,
                  source=b.get("source") or "Manuell", note=b.get("note", ""),
                  asset_class=b.get("asset_class", "etf"), name=b.get("name", ""))
        if not tx["asset"]:
            raise ValueError("Asset fehlt")
        with_hashes([tx])
        tx["hash"] += f"-{time.time()}"
        with DB_LOCK, db() as con:
            ensure_asset(con, tx)
            con.execute("""INSERT INTO transactions
                           (portfolio_id,date,type,asset,quantity,price,currency,fee,total,source,note,hash)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (self._pid(b), tx["date"], typ, tx["asset"], qty, price, tx["currency"], fee, total, tx["source"],
                         tx["note"], tx["hash"]))
        threading.Thread(target=refresh_prices, daemon=True).start()
        return self._send(200, {"ok": True})

    def _edit_tx(self, tx_id, b):
        typ = b.get("type", "buy")
        if typ not in TX_TYPES:
            raise ValueError("Ungültiger Typ")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", b.get("date", "")):
            raise ValueError("Ungültiges Datum")
        asset = (b.get("asset") or "").strip().upper()
        if not asset:
            raise ValueError("Asset fehlt")
        qty = float(b.get("quantity") or 0)
        price = float(b.get("price") or 0)
        fee = float(b.get("fee") or 0)
        total = b.get("total")
        total = float(total) if total not in (None, "") else (qty * price + (fee if typ == "buy" else -fee))
        if typ in ("tax", "fee", "withdrawal"):
            total = -abs(total)
        elif typ in ("interest", "deposit"):
            total = abs(total)
        with DB_LOCK, db() as con:
            if not con.execute("SELECT 1 FROM transactions WHERE id=?", (tx_id,)).fetchone():
                return self._send(404, {"error": "Transaktion nicht gefunden"})
            ensure_asset(con, dict(asset=asset, asset_class=b.get("asset_class", "etf"), name=""))
            con.execute("""UPDATE transactions SET date=?, type=?, asset=?, quantity=?, price=?, currency=?,
                           fee=?, total=?, source=?, note=? WHERE id=?""",
                        (b["date"], typ, asset, qty, price, (b.get("currency") or BASE).upper()[:3], fee, total,
                         (b.get("source") or "Manuell").strip(), b.get("note", ""), tx_id))
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
        with DB_LOCK, db() as con:
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
        if not str(f).startswith(str(STATIC)) or not f.is_file():
            f = STATIC / "index.html"
        return self._send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
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
        log_auth("reset_cli", "lokal", "")
        print("Anmeldung zurückgesetzt. Beim nächsten Aufruf kann ein neues Passwort festgelegt werden.")
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
    ver = f"Version {APP_VERSION}, " if APP_VERSION else ""
    print(f"Finanz-Dashboard läuft auf {scheme}://{host}:{port}  ({ver}Basiswährung {BASE}, Daten in {DATA_DIR})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    sys.exit(HTTPD["exit"])


if __name__ == "__main__":
    main()
