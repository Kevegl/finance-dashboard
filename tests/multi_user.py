#!/usr/bin/env python3
"""Mehrere Benutzer im Server-Modus: Registrierung auf der Anmeldeseite, getrennte Daten (eigene Datenbank pro
Benutzer), Rechte des Administrators, Wiederherstellung mit Benutzername, Löschen eines Benutzers. Nur Standardbibliothek.

  python3 tests/multi_user.py
"""
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("MULTI_PORT", "18097"))
BASE = f"http://127.0.0.1:{PORT}"

for _s in (sys.stdout, sys.stderr):   # Windows-Konsole in GitHub Actions: cp1252 -> UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def client():
    """Eigener Browser (Cookie-Speicher) pro Benutzer."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None, expect=200):
        req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Origin": BASE})
        try:
            with opener.open(req, timeout=20) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        assert code == expect, f"{path}: HTTP {code} statt {expect}: {raw[:300]!r}"
        return json.loads(raw.decode() or "null")
    return call


def import_csv(call, name):
    text = (ROOT / "beispiel-csv" / name).read_text(encoding="utf-8")
    return call("/api/import/commit", {"text": text, "provider": "auto"})["imported"]


def main():
    data = tempfile.mkdtemp(prefix="fd-multi-")
    env = {k: v for k, v in os.environ.items() if k not in ("FD_LOCAL", "FD_DOCKER", "FD_REPO_DIR")}
    env.update(FD_DATA_DIR=data, FD_PORT=str(PORT), FD_HOST="127.0.0.1", FD_PRICE_TTL="999999999")
    admin, anna, gast = client(), client(), client()
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(ROOT / "app"), env=env)
    try:
        for _ in range(60):
            try:
                gast("/api/health")
                break
            except (OSError, AssertionError):
                time.sleep(0.5)
        else:
            raise SystemExit("FEHLER: Server startet nicht")
        # Vor der Ersteinrichtung keine Registrierung
        assert gast("/api/auth/status")["registration"] is False
        gast("/api/register", {"username": "anna", "password": "annaanna1"}, expect=409)
        code = Path(data, "setup-code.txt").read_text(encoding="ascii").strip()
        admin("/api/setup", {"username": "Admin", "password": "adminadmin1", "setup_code": code})
        n_admin = import_csv(admin, "relai_beispiel.csv")
        assert n_admin > 0

        # Registrierung: eigenes, leeres Dashboard
        assert gast("/api/auth/status")["registration"] is True
        gast("/api/register", {"username": "ADMIN", "password": "annaanna1"}, expect=400)       # Name vergeben
        gast("/api/register", {"username": "anna", "password": "kurz"}, expect=400)             # Passwort zu kurz
        r = anna("/api/register", {"username": "Änna", "password": "annaanna1", "lang": "en"})
        assert r["user"] == "Änna" and r["recovery_code"], r
        anna_code = r["recovery_code"]
        assert Path(data, "users", "u1.db").is_file(), "eigene Datenbank fehlt"
        st = anna("/api/auth/status")
        assert st["authenticated"] and st["user"] == "Änna" and st["lang"] == "en", st
        s = anna("/api/settings")
        assert s["admin"] is False and s["transactions"] == 0 and s["registration"] is None, s
        assert anna("/api/transactions") == []
        assert [p["name"] for p in anna("/api/portfolios")] == ["Hauptportfolio"]

        # Getrennte Daten und Einstellungen
        n_anna = import_csv(anna, "ibkr_flex_query.csv")
        assert n_anna > 0
        anna("/api/settings", {"title": "Annas Depot", "base": "EUR", "lang": "en"})
        assert admin("/api/settings")["transactions"] == n_admin
        assert anna("/api/settings")["transactions"] == n_anna
        assert admin("/api/settings")["base"] == "CHF" and anna("/api/settings")["base"] == "EUR"
        assert admin("/api/summary")["title"] != "Annas Depot"
        assert anna("/api/summary")["base"] == "EUR" and admin("/api/summary")["base"] == "CHF"
        bk = anna("/api/backup")
        assert len(bk["transactions"]) == n_anna and '"auth_' not in json.dumps(bk)

        # Nur der Administrator: Benutzerverwaltung und Updates
        anna("/api/users", expect=403)
        anna("/api/users/delete", {"id": 1}, expect=403)
        anna("/api/update/request", {"action": "check"}, expect=403)
        anna("/api/update/status", expect=403)
        users = admin("/api/users")
        assert [(u["username"], u["admin"], u["transactions"]) for u in users["users"]] == \
            [("Admin", True, n_admin), ("Änna", False, n_anna)], users
        assert admin("/api/settings")["admin"] is True

        # Anmeldung: Gross-/Kleinschreibung egal (auch Umlaute), Abmelden, Passwort/Name ändern
        anna("/api/logout", {})
        anna("/api/settings", expect=401)
        anna("/api/login", {"username": "änna", "password": "adminadmin1"}, expect=401)   # Passwort des Admins
        r = anna("/api/login", {"username": "ÄNNA", "password": "annaanna1"})
        assert r["lang"] == "en", r
        anna("/api/account/password", {"current": "annaanna1", "username": "admin"}, expect=400)   # Name vergeben
        anna("/api/account/password", {"current": "annaanna1", "username": "Anna B."})
        assert anna("/api/auth/status")["user"] == "Anna B."
        assert anna("/api/settings")["sessions"] == 1 and admin("/api/settings")["sessions"] == 1

        # Wiederherstellung mit Benutzername
        anna("/api/logout", {})
        anna("/api/recovery", {"username": "anna b.", "code": "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA",
                               "password": "neuneuneu1"}, expect=401)
        rc = anna("/api/recovery", {"username": "anna b.", "code": anna_code, "password": "neuneuneu1"})
        assert rc["recovery_code"] and anna("/api/settings")["transactions"] == n_anna
        assert admin("/api/settings")["transactions"] == n_admin     # Admin unberührt

        # Registrierung ausschalten
        admin("/api/users/registration", {"enabled": False})
        assert gast("/api/auth/status")["registration"] is False
        gast("/api/register", {"username": "bert", "password": "bertbert1"}, expect=403)
        admin("/api/users/registration", {"enabled": True})
        gast("/api/register", {"username": "bert", "password": "bertbert1"})
        assert gast("/api/settings")["admin"] is False

        # Löschen: Daten und Sitzungen weg
        admin("/api/users/delete", {"id": 0}, expect=400)
        admin("/api/users/delete", {"id": 1})
        anna("/api/settings", expect=401)
        assert not Path(data, "users", "u1.db").exists()
        anna("/api/login", {"username": "anna b.", "password": "neuneuneu1"}, expect=401)
        assert [u["username"] for u in admin("/api/users")["users"]] == ["Admin", "bert"]
        assert admin("/api/settings")["transactions"] == n_admin
        print("✓ Mehrere Benutzer: Registrierung, getrennte Daten, Administrator-Rechte, Wiederherstellung, Löschen")
    finally:
        proc.terminate()
        proc.wait(10)
    print("MEHRBENUTZER-TEST OK")


if __name__ == "__main__":
    main()
