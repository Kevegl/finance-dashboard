#!/usr/bin/env python3
"""Kurztest des Server-Modus (wie auf Proxmox, ohne FD_LOCAL): Ersteinrichtung, Import aller Dateien aus
beispiel-csv/ und Plausibilität von Übersicht und Transaktionen. Nur Standardbibliothek.

  python3 tests/smoke_server.py
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
PORT = int(os.environ.get("SMOKE_PORT", "18098"))
BASE = f"http://127.0.0.1:{PORT}"


for _s in (sys.stdout, sys.stderr):   # Windows-Konsole in GitHub Actions: cp1252 -> UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


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
        call("/api/setup", {"username": "test", "password": "testtest123"})
        assert call("/api/settings")["local"] is None
        assert call("/api/update/status") == {"available": False}
        call("/api/local/quit", {}, expect=404)       # im Server-Modus darf sich nichts beenden lassen

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
        print(f"✓ Server-Modus: {total} Transaktionen importiert, {len(s['holdings'])} Positionen")
    finally:
        proc.terminate()
        proc.wait(10)
    print("SERVER-TEST OK")


if __name__ == "__main__":
    main()
