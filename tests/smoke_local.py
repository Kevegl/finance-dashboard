#!/usr/bin/env python3
"""Kurztest des lokalen Betriebs (läuft in GitHub Actions auf Windows, macOS und Linux).

  python3 tests/smoke_local.py                      -> startet python app/launcher.py
  python3 tests/smoke_local.py -- cmd /c start.bat  -> startet ein beliebiges Kommando (Startskript, .exe, App)

Prüft: Start, Ersteinrichtung, Anmeldung, Einstellungen im lokalen Modus, Erkennung einer bereits
laufenden Instanz und «Dashboard beenden». Nur Standardbibliothek, nutzt einen eigenen Datenordner.
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
PORT = int(os.environ.get("SMOKE_PORT", "18799"))
BASE = f"http://127.0.0.1:{PORT}"


def main():
    cmd = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [sys.executable, "app/launcher.py"]
    data = tempfile.mkdtemp(prefix="fd-smoke-")
    env = dict(os.environ, FD_DATA_DIR=data, FD_PORT=str(PORT), FD_NO_BROWSER="1",
               FD_UPDATE_API="http://127.0.0.1:9")   # keine Anfragen an GitHub während des Tests
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, body=None, timeout=10):
        req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    print("Starte:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
    try:
        for _ in range(120):
            try:
                h = call("/api/health", timeout=2)
                break
            except (OSError, ValueError):
                if proc.poll() is not None:
                    raise SystemExit(f"FEHLER: Startprogramm vorzeitig beendet (Code {proc.returncode})")
                time.sleep(0.5)
        else:
            raise SystemExit("FEHLER: Dashboard antwortet nicht innerhalb von 60 s")
        assert h == {"ok": True, "app": "finance-dashboard", "local": True}, h
        print("✓ läuft:", h, flush=True)

        assert call("/api/setup", {"username": "smoke", "password": "smoketest123"})["ok"]
        s = call("/api/settings")
        assert s["local"] and Path(s["local"]["data_dir"]).resolve() == Path(data).resolve(), s["local"]
        assert s["local"]["version"], s["local"]
        assert call("/api/summary") is not None
        st = call("/api/update/status")
        assert st["available"] and st["mode"] == "release", st
        print("✓ Einrichtung, Einstellungen, Version", s["local"]["version"], flush=True)

        second = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=60, stdin=subprocess.DEVNULL)
        assert second.returncode == 0, f"zweiter Start: Code {second.returncode}"
        assert proc.poll() is None, "erste Instanz wurde beendet"
        print("✓ zweiter Start erkennt die laufende Instanz", flush=True)

        assert call("/api/local/quit", {})["ok"]
        code = proc.wait(timeout=30)
        assert code == 0, f"Exit-Code nach «Beenden»: {code}"
        try:
            call("/api/health", timeout=2)
            raise SystemExit("FEHLER: Server läuft nach «Beenden» weiter")
        except (OSError, urllib.error.URLError):
            pass
        assert (Path(data) / "finance.db").is_file(), "Datenbank fehlt im Datenordner"
        print("✓ «Dashboard beenden» – alles beendet, Daten im Datenordner", flush=True)
    finally:
        if proc.poll() is None:
            proc.kill()
    print("SMOKE-TEST OK")


if __name__ == "__main__":
    main()
