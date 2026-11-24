"""Kurse und Wechselkurse (Yahoo Finance, Kraken/CoinGecko/Bitstamp, EZB über frankfurter) – nur serverseitig."""
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import config
import importers
import local_update
from config import PRICE_TTL
from validate import DATE_GLOB, is_iso_date
from store import current_db, db_tx, get_setting, set_setting
REFRESH_LOCKS = {}                 # Datenbank -> Lock (jeder Benutzer hat eigene Kurse; pro Datenbank eine Aktualisierung)
REFRESH_LOCKS_GUARD = threading.Lock()


# --------------------------------------------------------------------------
# Kurse (Yahoo Finance, ohne API-Key)
# --------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with local_update.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def market_day(meta):
    """Handelstag des Live-Kurses (Ortszeit der Börse). Vor Handelsbeginn und am Wochenende ist das der letzte
    Handelstag – nicht heute, sonst stünde der unveränderte Schlusskurs als heutiger Kurs da (24h = 0 %)."""
    try:
        t = int(meta["regularMarketTime"]) + int(meta.get("gmtoffset") or 0)
        day = datetime.fromtimestamp(t, timezone.utc).date()
        if day <= date.today() + timedelta(days=1):
            return min(day, date.today()).isoformat()
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    return date.today().isoformat()


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
        out.append((market_day(res["meta"]), live * factor))
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
    with db_tx() as con:
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
            with db_tx() as con:
                con.execute("UPDATE assets SET ticker=? WHERE symbol=? AND COALESCE(ticker,'')=''", (sym, isin))
    if changed:
        set_setting("isin_lookup", json.dumps(done))


def needed_tickers(con):
    """Alle Kurs- und Devisen-Ticker, die für das Portfolio gebraucht werden."""
    tickers = {}
    first = con.execute("SELECT MIN(date) FROM transactions WHERE date GLOB ?", (DATE_GLOB,)).fetchone()[0]
    if not first or not is_iso_date(first):
        return {}
    start = datetime.strptime(first, "%Y-%m-%d").date() - timedelta(days=7)
    for a in con.execute("SELECT * FROM assets WHERE ticker != ''"):
        tickers[a["ticker"]] = start
    curs = {r[0] for r in con.execute("SELECT DISTINCT currency FROM transactions")}
    curs |= {r[0] for r in con.execute("SELECT DISTINCT currency FROM price_meta WHERE currency IS NOT NULL")}
    for c in curs:
        if c and c != config.BASE:
            tickers[f"{c}{config.BASE}=X"] = start
    return tickers


def refresh_prices(force=False):
    with REFRESH_LOCKS_GUARD:
        lock = REFRESH_LOCKS.setdefault(str(current_db()), threading.Lock())
    if not lock.acquire(blocking=False):
        return {"status": "läuft bereits"}
    errors = {}
    try:
        try:
            resolve_isin_tickers()
        except Exception as e:  # noqa: BLE001
            print("ISIN-Suche fehlgeschlagen:", e)
        for _ in range(2):  # 2. Durchlauf: neu entdeckte Kurswährungen -> FX nachladen
            with db_tx() as con:
                tickers = needed_tickers(con)
                meta = {r["ticker"]: r for r in con.execute("SELECT * FROM price_meta")}
            for t, start in tickers.items():
                m = meta.get(t)
                if m and not force and m["fetched_at"] and time.time() - m["fetched_at"] < PRICE_TTL:
                    continue
                with db_tx() as con:
                    last = con.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (t,)).fetchone()[0]
                    first = con.execute("SELECT MIN(date) FROM prices WHERE ticker=?", (t,)).fetchone()[0]
                since = start
                # Nur neue Tage nachladen – ausser es fehlt noch Historie vor dem ersten gespeicherten Kurs
                has_gap = not first or first > (start + timedelta(days=5)).isoformat()
                if last and not has_gap:
                    since = max(start, datetime.strptime(last, "%Y-%m-%d").date() - timedelta(days=5))
                try:
                    cur, rows = fetch_prices(t, since)
                    with db_tx() as con:
                        if rows:
                            # Kurse, die früher mit einem zu späten Datum gespeichert wurden (Live-Kurs vor
                            # Handelsbeginn als «heute»), durch den aktuellen Stand ersetzen
                            con.execute("DELETE FROM prices WHERE ticker=? AND date>?", (t, max(d for d, _ in rows)))
                        con.executemany("INSERT OR REPLACE INTO prices(ticker,date,close) VALUES (?,?,?)",
                                        [(t, d, c) for d, c in rows])
                        con.execute("INSERT OR REPLACE INTO price_meta(ticker,currency,fetched_at,error) VALUES (?,?,?,NULL)",
                                    (t, cur, time.time()))
                except Exception as e:  # noqa: BLE001
                    errors[t] = str(e)[:300]
                    with db_tx() as con:
                        con.execute("""INSERT INTO price_meta(ticker,currency,fetched_at,error) VALUES (?,NULL,?,?)
                                       ON CONFLICT(ticker) DO UPDATE SET fetched_at=excluded.fetched_at, error=excluded.error""",
                                    (t, time.time(), str(e)[:300]))
    finally:
        lock.release()
    return {"status": "ok", "errors": errors}


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
