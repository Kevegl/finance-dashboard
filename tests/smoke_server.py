#!/usr/bin/env python3
"""Kurztest des Server-Modus (wie auf Proxmox, ohne FD_LOCAL): Ersteinrichtung, Import aller Dateien aus
beispiel-csv/ und Plausibilität von Übersicht und Transaktionen. Nur Standardbibliothek.

  python3 tests/smoke_server.py
"""
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("SMOKE_PORT", "18098"))
BASE = f"http://127.0.0.1:{PORT}"


for _s in (sys.stdout, sys.stderr):   # Windows-Konsole in GitHub Actions: cp1252 -> UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def totp_at(secret, step):
    """TOTP (RFC 6238) mit der Standardbibliothek berechnen – unabhängig vom Servercode."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = mac[-1] & 15
    return f"{(struct.unpack('>I', mac[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def auth_flow(call, rc):
    """Anmeldeprotokoll, Zwei-Faktor und Wiederherstellungscode (insgesamt < 5 Fehlversuche pro IP)."""
    pw = "testtest123"
    base = int(time.time() // 30)
    call("/api/login", {"username": "test", "password": "falsch"}, expect=401)            # Fehlversuch 1
    st = call("/api/settings")
    assert st["totp"] is False and st["recovery"] is True, st
    call("/api/account/totp/start", {"password": "falsch"}, expect=403)                   # Fehlversuch 2 (Passwort)
    start = call("/api/account/totp/start", {"password": pw})
    secret = start["secret"]
    assert re.fullmatch(r"[A-Z2-7]{32}", secret), secret
    assert start["uri"].startswith("otpauth://totp/Finanz-Dashboard:test?secret=" + secret), start
    c0 = totp_at(secret, base)
    call("/api/account/totp/confirm", {"code": c0})
    assert call("/api/settings")["totp"] is True
    call("/api/logout", {})
    r = call("/api/login", {"username": "test", "password": pw}, expect=401)              # kein Fehlversuch
    assert r["error"] == "totp_required", r
    c1 = totp_at(secret, base + 1)                                                         # Fenster ±1
    call("/api/login", {"username": "test", "password": pw, "code": c1})
    call("/api/logout", {})
    call("/api/login", {"username": "test", "password": pw, "code": c1}, expect=401)      # Fehlversuch 3: gleicher Code erneut
    call("/api/recovery", {"code": "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA", "password": "neuespass99"}, expect=401)   # Fehlversuch 4
    r = call("/api/recovery", {"code": rc.lower().replace("-", " "), "password": "neuespass99"})
    rc2 = r["recovery_code"]
    assert rc2 != rc and call("/api/settings")["totp"] is False
    call("/api/logout", {})
    call("/api/login", {"username": "test", "password": "neuespass99"})
    rc3 = call("/api/account/recovery/new", {"password": "neuespass99"})["recovery_code"]
    assert rc3 not in (rc, rc2)
    call("/api/account/password", {"current": "neuespass99", "new": pw})
    log = call("/api/auth/log")
    events = {e["event"] for e in log["entries"]}
    need = {"setup", "login_ok", "login_fail", "password_fail", "totp_fail", "recovery_fail", "totp_on", "recovery_used",
            "recovery_new", "password_changed"}
    assert need <= events, need - events
    assert log["fails_30d"] == 4 and log["last_login"], log
    blob = json.dumps(log)
    assert pw not in blob and secret not in blob and rc not in blob and c1 not in blob
    backup = json.dumps(call("/api/backup"))
    assert '"auth_' not in backup and secret not in backup and "pbkdf2" not in backup
    call("/api/recovery", {"code": rc2, "password": "wieder12345"}, expect=401)   # alter Code ungültig (Fehlversuch 5, zuletzt)
    call("/api/login", {"username": "test", "password": pw}, expect=429)          # Sperre nach 5 Fehlversuchen greift
    call("/api/account/recovery/new", {"password": pw}, expect=429)               # auch für Passwortabfragen in den Einstellungen
    print("✓ Anmeldeprotokoll, Zwei-Faktor, Wiederherstellungscode, Sperre, Sicherung ohne auth_*")


def main():
    data = tempfile.mkdtemp(prefix="fd-server-")
    env = {k: v for k, v in os.environ.items() if k not in ("FD_LOCAL", "FD_DOCKER", "FD_REPO_DIR")}
    env.update(FD_DATA_DIR=data, FD_PORT=str(PORT), FD_HOST="127.0.0.1")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

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

    proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(ROOT / "app"), env=env)
    try:
        for _ in range(60):
            try:
                h = call("/api/health")
                break
            except (OSError, AssertionError):
                time.sleep(0.5)
        else:
            raise SystemExit("FEHLER: Server startet nicht")
        assert h == {"ok": True, "app": "finance-dashboard", "local": False}, h
        setup = call("/api/setup", {"username": "test", "password": "testtest123"})
        rc = setup["recovery_code"]
        assert re.fullmatch(r"([A-Z2-9]{5}-){4}[A-Z2-9]{5}", rc) and not set("01OIL") & set(rc), rc
        assert call("/api/settings")["local"] is None
        assert call("/api/update/status") == {"available": False}
        call("/api/local/quit", {}, expect=404)       # im Server-Modus darf sich nichts beenden lassen
        # Benutzername mit Umlauten (Anmeldung, Gross-/Kleinschreibung egal), danach zurück auf «test»
        call("/api/account/password", {"current": "testtest123", "username": "Jürg Müller"})
        call("/api/logout", {})
        call("/api/login", {"username": "jürg müller", "password": "testtest123"})
        call("/api/account/password", {"current": "testtest123", "username": "test"})

        total = 0
        for f in sorted((ROOT / "beispiel-csv").glob("*.csv")):
            body = {"text": f.read_text(encoding="utf-8-sig")}
            prev = call("/api/import/preview", body)
            if prev.get("needs_mapping"):
                body.update(provider="generic", mapping=prev.get("mapping") or prev.get("suggested_mapping"))
            r = call("/api/import/commit", body)
            print(f"  {f.name}: {r}")
            total += r["imported"]
        txs = call("/api/transactions")
        assert total > 0 and len(txs) == total, (total, len(txs))
        s = call("/api/summary")
        assert s.get("holdings"), "Übersicht ohne Positionen"
        auth_flow(call, rc)
        print(f"✓ Server-Modus: {total} Transaktionen importiert, {len(s['holdings'])} Positionen")
    finally:
        proc.terminate()
        proc.wait(10)
    print("SERVER-TEST OK")


if __name__ == "__main__":
    main()
