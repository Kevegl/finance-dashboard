#!/usr/bin/env python3
"""Erzeugt ein Demo-Dashboard mit ausschliesslich erfundenen Werten (für die Screenshots im README).

  python3 docs/screenshots/demo_data.py /tmp/fd-demo      -> Datenordner mit Demo-Datenbank

Startet den Server kurz im Hintergrund, richtet den Zugang ein (demo / demo-passwort), importiert die Dateien aus
beispiel-csv/, legt ein Säule-3a-Portfolio mit erfundenen Einzahlungen an und schreibt erfundene, aber plausible
Kursverläufe in die Datenbank (es werden keine echten Kurse abgefragt). Danach den Server mit
  FD_DATA_DIR=/tmp/fd-demo FD_PRICE_TTL=999999999 python3 app/server.py
starten und die Screenshots mit docs/screenshots/shoot.js aufnehmen.
"""
import http.cookiejar
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = 18555
BASE = f"http://127.0.0.1:{PORT}"
USER, PASSWORD = "demo", "demo-passwort"


def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fd-demo")
    if data.exists() and any(data.iterdir()):
        raise SystemExit(f"{data} ist nicht leer")
    data.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FD_DATA_DIR=str(data), FD_PORT=str(PORT), FD_HOST="127.0.0.1",
               FD_PRICE_TTL="999999999", FD_UPDATE_API="http://127.0.0.1:9")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT / "app", env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, body=None):
        req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        with op.open(req, timeout=30) as r:
            return json.loads(r.read().decode() or "null")

    try:
        for _ in range(60):
            try:
                call("/api/health")
                break
            except OSError:
                time.sleep(0.3)
        call("/api/setup", {"username": USER, "password": PASSWORD, "lang": "de"})
        call("/api/settings", {"title": "Mein Portfolio", "base": "CHF", "lang": "de", "news": False})
        pf = call("/api/portfolios")[0]
        call("/api/portfolios/rename", {"id": pf["id"], "name": "Depot"})
        for f in ("ibkr_activity_statement.csv", "relai_beispiel.csv", "relai_deutsch.csv", "generisch_beispiel.csv"):
            body = {"text": (ROOT / "beispiel-csv" / f).read_text(encoding="utf-8-sig"), "portfolio": pf["id"]}
            prev = call("/api/import/preview", body)
            if prev.get("needs_mapping"):
                body.update(provider="generic", mapping=prev.get("mapping") or prev.get("suggested_mapping"))
            call("/api/import/commit", body)
        # Säule 3a (erfunden): jährliche Einzahlungen, Käufe eines Vorsorgefonds
        p3 = call("/api/portfolios", {"name": "Säule 3a", "kind": "3a"})["id"]
        nav = 100.0
        for y in (2022, 2023, 2024, 2025):
            call("/api/transactions", {"portfolio": p3, "date": f"{y}-01-10", "type": "deposit", "asset": "CASH",
                                       "asset_class": "cash", "name": "Konto (Zinsen & Gebühren)", "total": 7056,
                                       "currency": "CHF", "source": "Manuell", "note": "Einzahlung 3a"})
            units = round(7000 / nav, 3)
            call("/api/transactions", {"portfolio": p3, "date": f"{y}-01-12", "type": "buy", "asset": "GLOBAL100",
                                       "name": "Vorsorgefonds Global 100", "quantity": units, "price": nav,
                                       "currency": "CHF", "source": "Manuell"})
            nav *= 1.07
        time.sleep(1)
    finally:
        proc.terminate()
        proc.wait(10)
    fake_prices(data / "finance.db")
    print(f"Demo-Daten in {data} – Anmeldung: {USER} / {PASSWORD}")


def fake_prices(db_path):
    """Erfundene Tageskurse: verlaufen durch die Kaufkurse der Beispieldaten und steigen danach moderat."""
    rnd = random.Random(42)
    con = sqlite3.connect(db_path)
    first = date.fromisoformat(con.execute("SELECT MIN(date) FROM transactions").fetchone()[0]) - timedelta(days=10)
    today = date.today()
    con.execute("UPDATE assets SET ticker='GLOBAL100.DEMO' WHERE symbol='GLOBAL100'")
    con.execute("UPDATE assets SET ticker='SPICHA.SW', name='UBS SPI ETF' WHERE symbol='SPICHA'")
    tickers = {r[0]: r[1] for r in con.execute("SELECT ticker, symbol FROM assets WHERE ticker != ''")}
    anchors = {}
    for t, sym in tickers.items():
        rows = con.execute("SELECT date, price, currency FROM transactions WHERE asset=? AND price > 0 ORDER BY date",
                           (sym,)).fetchall()
        anchors[t] = [(date.fromisoformat(d), p) for d, p, _ in rows]
    cur = {t: (con.execute("SELECT currency FROM transactions WHERE asset=? LIMIT 1", (s,)).fetchone() or ["CHF"])[0]
           for t, s in tickers.items()}
    end_gain = {"BTC": 1.18}                                    # Kurs heute relativ zum letzten Kauf
    fx = {"USDCHF=X": [(first, 0.86), (date(2024, 12, 31), 0.90), (today, 0.80)],
          "EURCHF=X": [(first, 0.94), (today, 0.93)]}
    series = {}
    for t, pts in anchors.items():
        if not pts:
            continue
        g = end_gain.get(tickers[t], 1.12)
        pts = [(first, pts[0][1] * 0.97)] + pts + [(today, pts[-1][1] * g)]
        series[t] = pts
    series.update(fx)
    for t, pts in series.items():
        vol = 0.025 if t.startswith("BTC") else 0.006 if "=X" not in t else 0.002
        noise, rows, d = 0.0, [], first
        while d <= today:
            if d.weekday() < 5 or t.startswith("BTC"):
                # log-lineare Interpolation zwischen den Stützpunkten + gedämpftes Rauschen
                i = max(k for k in range(len(pts)) if pts[k][0] <= d) if d >= pts[0][0] else 0
                a, b = pts[i], pts[min(i + 1, len(pts) - 1)]
                span = max((b[0] - a[0]).days, 1)
                w = min(max((d - a[0]).days / span, 0), 1)
                base = math.exp(math.log(a[1]) * (1 - w) + math.log(b[1]) * w)
                noise = noise * 0.9 + rnd.gauss(0, vol)
                rows.append((t, d.isoformat(), round(base * math.exp(noise), 4)))
            d += timedelta(days=1)
        con.executemany("INSERT OR REPLACE INTO prices(ticker,date,close) VALUES (?,?,?)", rows)
        c = cur.get(t, "CHF") if "=X" not in t else None
        con.execute("INSERT OR REPLACE INTO price_meta(ticker,currency,fetched_at,error) VALUES (?,?,?,NULL)",
                    (t, c, time.time()))
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
