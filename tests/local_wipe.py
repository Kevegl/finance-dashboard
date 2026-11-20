#!/usr/bin/env python3
"""Lokaler Betrieb: «Passwort vergessen» im Statusfenster (ohne Fenster getestet, läuft auch unter Windows/macOS).

  - «Mit Wiederherstellungscode» öffnet im Browser das Formular (#/passwort-vergessen), ändert selbst nichts.
  - «Neu beginnen» beendet den Server, löscht die Datenbank und startet ein leeres Dashboard (Ersteinrichtung).
"""
import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("SMOKE_PORT", "18797"))


def main():
    data = tempfile.mkdtemp(prefix="fd-wipe-")
    os.environ.update(FD_DATA_DIR=data, FD_PORT=str(PORT), FD_NO_BROWSER="1", FD_NO_GUI="1",
                      FD_UPDATE_API="http://127.0.0.1:9")
    sys.path.insert(0, str(ROOT / "app"))
    sys.argv = ["launcher.py"]
    import launcher as L
    opened = []
    L.webbrowser.open = lambda url: opened.append(url)
    lau = L.Launcher()
    for word in ("ENTFERNEN", "entfernen", " Entfernen ", "DELETE", "loeschen", "LÖSCHEN"):
        assert lau.wipe_confirmed(word), word
    for word in (None, "", "ja", "OK", "entferne", "LOSCHEN"):
        assert not lau.wipe_confirmed(word), word
    print("✓ Bestätigung «Neu beginnen» mit ENTFERNEN (ohne Umlaut) bzw. DELETE/LÖSCHEN", flush=True)
    assert lau.prepare(False) is None
    result = []
    worker = threading.Thread(target=lambda: result.append(lau.supervise(False)), daemon=True)
    worker.start()

    base = f"http://127.0.0.1:{PORT}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def wait_up():
        for _ in range(120):
            try:
                return call("/api/auth/status")
            except OSError:
                time.sleep(0.5)
        raise SystemExit("FEHLER: Dashboard antwortet nicht")

    try:
        wait_up()
        call("/api/setup", {"username": "test", "password": "testtest123"})
        call("/api/transactions", {"date": "2025-01-10", "type": "buy", "asset": "TEST", "quantity": 1, "price": 100,
                                   "currency": "CHF"})
        assert len(call("/api/transactions")) == 1
        assert (Path(data) / "finance.db").is_file()

        lau.open_recovery()
        assert opened == [base + "/#/passwort-vergessen"], opened
        assert wait_up()["setup_required"] is False, "Wiederherstellungs-Link darf nichts zurücksetzen"
        print("✓ «Mit Wiederherstellungscode» öffnet nur das Formular", flush=True)

        for _ in range(240):            # «Neu beginnen» gibt es erst, wenn das Startprogramm den Server als laufend führt
            if lau.status == "running":
                break
            time.sleep(0.25)
        assert lau.start_fresh(), f"«Neu beginnen» abgelehnt (Status {lau.status})"
        for _ in range(120):            # warten, bis der neue (leere) Server läuft
            time.sleep(0.5)
            try:
                st = call("/api/auth/status")
                if st["setup_required"]:
                    break
            except OSError:
                pass
        else:
            raise SystemExit("FEHLER: Dashboard nach «Neu beginnen» nicht leer gestartet")
        assert worker.is_alive(), result
        for _ in range(40):                                  # Startprogramm öffnet die Einrichtung im Browser
            if len(opened) >= 2:
                break
            time.sleep(0.25)
        assert opened[-1] == base + "/", opened
        call("/api/setup", {"username": "neu", "password": "neuesPasswort1"})
        assert call("/api/transactions") == [], "alte Daten sind noch vorhanden"
        print("✓ «Neu beginnen» löscht alle Daten und startet ein leeres Dashboard", flush=True)
    finally:
        lau.request_stop()
        worker.join(20)
    print("NEUBEGINN-TEST OK")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
