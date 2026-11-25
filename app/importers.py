"""CSV-Importer für verschiedene Anbieter.

Jeder Importer liefert eine Liste normalisierter Transaktionen:
    {date, type, asset, asset_class, quantity, price, currency, fee, total,
     source, note, name?, listing_exchange?}

type:  buy | sell | dividend | tax | interest | fee | deposit | withdrawal | transfer
       (interest/fee: Kontozinsen bzw. Kontogebühren; deposit/withdrawal: eigene Ein-/Auszahlungen;
        transfer: Übertrag zwischen Stiftungen/Depots, Betrag mit Vorzeichen (+ ein, − aus); alle mit Asset "CASH")
quantity: immer positiv
total: Betrag in `currency` (bei buy inkl. Gebühren, bei sell netto erhalten)
"""
import csv
import io
import math
import re
from datetime import datetime


class ImportErrorDetail(Exception):
    pass


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

def read_rows(text):
    text = text.lstrip("﻿")
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ";" if sample.count(";") > sample.count(",") else ","
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]


def parse_num(v):
    """Robuste Zahl: 1'234.56 | 1,234.56 | 1.234,56 | -12 | CHF 50.00 | (12.5)"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in {"-", "--", "N/A", "n/a"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("'", "").replace("’", "")
    # e/E nur bei wissenschaftlicher Schreibweise behalten – sonst würde z.B. «EUR 12.50» zu «E12.50»
    s = s.replace(" ", "") if re.fullmatch(r"[-+]?\d+(?:\.\d+)?[eE][-+]?\d+", s.replace(" ", "")) \
        else re.sub(r"[^\d,.\-]", "", s)
    if not s or s in {"-", "."}:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        # 1,234 (Tausender) vs 12,5 (Dezimal)
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0].lstrip("-")) <= 3 and parts[0].lstrip("-") != "0":
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        n = float(s)
    except ValueError:
        return None
    if not math.isfinite(n):        # z.B. "1e999" -> unendlich, in JSON für den Browser unlesbar
        return None
    return -abs(n) if neg else n


_DATE_FORMATS = [
    "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y%m%d", "%Y%m%d;%H%M%S", "%Y%m%d %H%M%S",
    "%d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%y",
    "%Y/%m/%d", "%Y/%m/%d %H:%M:%S",
]


def _clean_date(s):
    s = str(s).strip().replace(", ", " ").replace(",", " ")
    s = re.sub(r"([+-]\d{2}:?\d{2}|\s?UTC|\s?CET|\s?CEST|\s?EST|\s?EDT)$", "", s).strip()
    return s


def date_parser_for(values):
    """Ermittelt anhand aller Werte einer Spalte, ob Slash-Daten d/m oder m/d sind."""
    day_first = False
    for v in values:
        m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", str(v))
        if m and int(m.group(1)) > 12:
            day_first = True
            break

    def parse(v):
        s = _clean_date(v)
        if not s:
            return None
        for f in _DATE_FORMATS:
            try:
                return datetime.strptime(s, f).strftime("%Y-%m-%d")
            except ValueError:
                pass
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            d, mo = (a, b) if day_first else (b, a)
            try:
                return datetime(y, mo, d).strftime("%Y-%m-%d")
            except ValueError:
                return None
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            try:      # nur existierende Daten (2024-13-45 würde später die Auswertung abbrechen)
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
            except ValueError:
                return None
        return None

    return parse


def norm(h):
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def find_col(headers, *candidates, exclude=()):
    """Findet die erste Spalte, deren normalisierter Name einen Kandidaten enthält."""
    nh = [norm(h) for h in headers]
    for c in candidates:
        c = norm(c)
        for i, h in enumerate(nh):
            if h == c:
                return i
    for c in candidates:
        c = norm(c)
        for i, h in enumerate(nh):
            if c in h and not any(norm(x) in h for x in exclude):
                return i
    return None


def cell(row, i):
    if i is None or i >= len(row):
        return ""
    return row[i].strip()


FIAT = {"CHF", "EUR", "USD", "GBP", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "HKD", "SGD"}


# --------------------------------------------------------------------------
# Relai
# --------------------------------------------------------------------------

def is_relai(rows):
    head = " ".join(rows[0]).lower() if rows else ""
    if "btc amount" in head or "btc price" in head or "relai" in head:
        return True
    if any(k in norm(head) for k in ("btcbetrag", "btckurs", "btcmenge")):
        return True
    # CoinTracking-Format (Relai-Export ist dafür optimiert)
    nh = [norm(h) for h in rows[0]] if rows else []
    return any("buyamount" in h for h in nh) and any("sellamount" in h for h in nh)


def parse_relai(rows):
    headers = rows[0]
    nh = [norm(h) for h in headers]
    out, skipped = [], []

    # Variante A: CoinTracking-Format
    if any("buyamount" in h for h in nh) and any("sellamount" in h for h in nh):
        i_type = find_col(headers, "type")
        i_ba = find_col(headers, "buy amount")
        i_bc = find_col(headers, "buy currency", "buy cur", exclude=("amount",))
        i_sa = find_col(headers, "sell amount")
        i_sc = find_col(headers, "sell currency", "sell cur", exclude=("amount",))
        i_fee = find_col(headers, "fee", exclude=("cur", "currency"))
        i_fc = find_col(headers, "fee currency", "fee cur")
        i_date = find_col(headers, "date")
        i_cm = find_col(headers, "comment")
        pdate = date_parser_for([cell(r, i_date) for r in rows[1:]])
        for n, r in enumerate(rows[1:], start=2):
            bc, sc = cell(r, i_bc).upper(), cell(r, i_sc).upper()
            ba, sa = parse_num(cell(r, i_ba)), parse_num(cell(r, i_sa))
            fee = parse_num(cell(r, i_fee)) or 0.0
            fcur = cell(r, i_fc).upper()
            d = pdate(cell(r, i_date))
            if not d:
                skipped.append(f"Zeile {n}: Datum unlesbar")
                continue
            if bc == "BTC" and sc in FIAT and ba and sa:
                fee_fiat = fee if fcur == sc else 0.0
                total = abs(sa) + fee_fiat
                out.append(dict(date=d, type="buy", asset="BTC", asset_class="crypto", quantity=abs(ba),
                                price=abs(sa) / abs(ba), currency=sc, fee=fee_fiat, total=total,
                                source="Relai", note=cell(r, i_cm), name="Bitcoin"))
            elif sc == "BTC" and bc in FIAT and ba and sa:
                fee_fiat = fee if fcur == bc else 0.0
                out.append(dict(date=d, type="sell", asset="BTC", asset_class="crypto", quantity=abs(sa),
                                price=abs(ba) / abs(sa), currency=bc, fee=fee_fiat, total=abs(ba) - fee_fiat,
                                source="Relai", note=cell(r, i_cm), name="Bitcoin"))
            else:
                skipped.append(f"Zeile {n}: {cell(r, i_type) or 'Typ ?'} übersprungen (kein Kauf/Verkauf)")
        return out, skipped

    # Variante B: Relai-eigenes Format (Date, Transaction Type, BTC Amount, BTC Price, Fiat Amount, Fee …)
    i_date = find_col(headers, "date", "datum")
    i_type = find_col(headers, "transaction type", "transaktionstyp", "type", "typ")
    i_btc = find_col(headers, "btc amount", "btc-betrag", "btc-menge", "amount btc", "bitcoin amount", "btc")
    i_price = find_col(headers, "btc price", "btc-kurs", "price", "kurs")
    i_fiat = find_col(headers, "fiat amount", "fiat-betrag", "amount chf", "amount eur", "fiat")
    i_cur = find_col(headers, "fiat currency", "fiat-währung", "currency pair", "währungspaar", "currency", "währung")
    i_fee = find_col(headers, "fee", "gebühr", exclude=("currency", "cur", "währung"))
    i_id = find_col(headers, "operation id", "order id", "id")
    if i_date is None or i_btc is None:
        raise ImportErrorDetail("Relai-Format nicht erkannt (Spalten 'Date' und 'BTC Amount' fehlen).")
    pdate = date_parser_for([cell(r, i_date) for r in rows[1:]])
    for n, r in enumerate(rows[1:], start=2):
        t = cell(r, i_type).lower()
        qty = parse_num(cell(r, i_btc))
        d = pdate(cell(r, i_date))
        if not d or not qty:
            skipped.append(f"Zeile {n}: unvollständig")
            continue
        if any(k in t for k in ("withdraw", "send", "transfer", "auszahlung", "abheb", "überweis", "deposit", "receive", "einzahl", "empfang")) and "buy" not in t and "kauf" not in t:
            skipped.append(f"Zeile {n}: '{cell(r, i_type)}' (Wallet-Bewegung) übersprungen")
            continue
        is_sell = "sell" in t or "verkauf" in t or (not t and qty < 0)
        cur_raw = cell(r, i_cur).upper()
        cur = next((c for c in FIAT if c in cur_raw), "CHF")
        price = parse_num(cell(r, i_price))
        fiat = parse_num(cell(r, i_fiat))
        fee = abs(parse_num(cell(r, i_fee)) or 0.0)
        qty = abs(qty)
        if fiat is None and price:
            fiat = qty * price
        fiat = abs(fiat or 0.0)
        if not price and fiat:
            price = fiat / qty
        out.append(dict(date=d, type="sell" if is_sell else "buy", asset="BTC", asset_class="crypto",
                        quantity=qty, price=price or 0.0, currency=cur, fee=fee,
                        total=(fiat - fee) if is_sell else (fiat + fee),
                        source="Relai", note=cell(r, i_id), name="Bitcoin"))
    return out, skipped


# --------------------------------------------------------------------------
# Interactive Brokers
# --------------------------------------------------------------------------

def is_ibkr_statement(rows):
    return any(len(r) > 2 and r[0] in ("Trades", "Transaktionen") and r[1] == "Header" for r in rows[:2000]) or \
        bool(rows and rows[0][:2] == ["Statement", "Header"])


def is_ibkr_flex(rows):
    for r in rows[:5]:
        nh = {norm(c) for c in r}
        if "tradeprice" in nh or ("ibcommission" in nh) or ("currencyprimary" in nh and "quantity" in nh):
            return True
    return False


ASSET_CLASS_MAP = {"stocks": "etf", "stk": "etf", "etf": "etf", "funds": "etf", "fund": "etf",
                   "bonds": "bond", "bond": "bond", "crypto": "crypto", "cryptocurrency": "crypto",
                   "forex": "cash", "cash": "cash"}


# Bereichsnamen des Kontoauszugs (englisch + deutsch)
IB_SECTIONS = {
    "Trades": "trades", "Transaktionen": "trades",
    "Dividends": "div", "Dividenden": "div",
    "Payment In Lieu Of Dividends": "div", "Zahlung anstelle von Dividenden": "div",
    "Withholding Tax": "wht", "Quellensteuer": "wht",
    "Financial Instrument Information": "fii", "Informationen zum Finanzinstrument": "fii",
    "Grant Activity": "grant", "Stock Grant Activity": "grant", "Gewährung von Bezugsrechten": "grant",
    "Corporate Actions": "ca", "Kapitalmaßnahmen": "ca",
}


def _g(rec, *names, default=""):
    for n in names:
        v = rec.get(n)
        if v not in (None, ""):
            return v
    return default


def parse_ibkr_statement(rows):
    headers = {}
    info = {}
    out, skipped = [], []
    recs = {"trades": [], "div": [], "wht": [], "grant": [], "ca": []}
    for r in rows:
        if len(r) < 3:
            continue
        sec, kind = IB_SECTIONS.get(r[0]), r[1]
        if not sec:
            continue
        if kind == "Header":
            headers[sec] = r[2:]   # Header kann innerhalb eines Bereichs wechseln (z.B. Aktien vs. Devisen)
            continue
        if kind != "Data" or sec not in headers:
            continue
        rec = dict(zip(headers[sec], r[2:]))
        if sec == "fii":
            for sym in [x.strip() for x in _g(rec, "Symbol").split(",") if x.strip()]:
                info[sym] = rec
        else:
            recs[sec].append(rec)

    if not any(recs.values()):
        raise ImportErrorDetail("Keine Transaktionen im IBKR-Kontoauszug gefunden.")

    all_dates = [_g(t, "Date/Time", "Datum/Zeit") for t in recs["trades"]] + \
                [_g(d, "Date", "Datum") for d in recs["div"] + recs["wht"]]
    pdate = date_parser_for(all_dates)

    def asset_class(sym, cat=""):
        typ = _g(info.get(sym, {}), "Type", "Typ").upper()
        if typ == "ETF":
            return "etf"
        if typ in ("COMMON", "ADR", "REIT", "PREFERRED"):
            return "stock"
        return ASSET_CLASS_MAP.get(cat.lower(), "etf")

    for t in recs["trades"]:
        disc = _g(t, "DataDiscriminator", default="Order")
        if disc not in ("Order", "Trade"):
            continue
        cat = _g(t, "Asset Category", "Vermögenswertkategorie")
        if cat.lower().startswith(("forex", "devisen")):
            skipped.append(f"Devisentausch {_g(t, 'Symbol')} übersprungen")
            continue
        sym = _g(t, "Symbol").strip()
        qty = parse_num(_g(t, "Quantity", "Menge"))
        price = parse_num(_g(t, "T. Price", "T.-Kurs", "TradePrice"))
        d = pdate(_g(t, "Date/Time", "Datum/Zeit"))
        if not sym or not qty or not d:
            continue
        comm = abs(parse_num(_g(t, "Comm/Fee", "Prov./Gebühr", "Comm in CHF", "Provisionseink. CHF", default="0")) or 0.0)
        proceeds = parse_num(_g(t, "Proceeds", "Erlös"))
        gross = abs(proceeds) if proceeds is not None else abs(qty) * (price or 0)
        is_sell = qty < 0
        inf = info.get(sym, {})
        out.append(dict(date=d, type="sell" if is_sell else "buy", asset=sym, asset_class=asset_class(sym, cat),
                        quantity=abs(qty), price=price or (gross / abs(qty)),
                        currency=_g(t, "Currency", "Währung", default="USD"),
                        fee=comm, total=(gross - comm) if is_sell else (gross + comm), source="IBKR",
                        note="", name=_g(inf, "Description", "Beschreibung"),
                        listing_exchange=_g(inf, "Listing Exch", "Börse")))

    # Dividenden (positiv) und Quellensteuer (negativ) – zusammen = Netto-Dividende
    for kind, lst in (("div", recs["div"]), ("wht", recs["wht"])):
        for dv in lst:
            cur = _g(dv, "Currency", "Währung")
            if cur.lower().startswith(("total", "gesamt")) or len(cur) != 3:
                continue
            desc = _g(dv, "Description", "Beschreibung")
            amt = parse_num(_g(dv, "Amount", "Betrag"))
            d = pdate(_g(dv, "Date", "Datum"))
            m = re.match(r"^\s*([A-Z0-9.\-]+)\s*\(", desc)
            if not (m and amt and d):
                continue
            out.append(dict(date=d, type="tax" if kind == "wht" else "dividend", asset=m.group(1),
                            asset_class=asset_class(m.group(1)), quantity=0.0, price=0.0, currency=cur, fee=0.0,
                            total=-abs(amt) if kind == "wht" else amt, source="IBKR", note=desc[:120]))

    # Gratis-Aktien von IBKR für Bareinzahlungen (Stock Grants), Kostenbasis 0.
    # Jede Zuteilung erscheint ein Jahr später nochmals als "Vesting" – das sind dieselben Aktien.
    # Gezählt wird nur die Zuteilung (Berichtsdatum = Zuteilungsdatum).
    for g in recs["grant"]:
        sym = _g(g, "Symbol").strip()
        if not sym or sym.lower().startswith(("total", "gesamt")):
            continue
        rep = _g(g, "Report Date", "Berichtsdatum")
        award = _g(g, "Award Date", "Zuteilungsdatum")
        if award and rep and award != rep:
            skipped.append(f"Vesting {sym} {rep} (bereits bei Zuteilung gezählt)")
            continue
        qty = parse_num(_g(g, "Quantity", "Menge"))
        d = pdate(award or rep)
        if not qty or not d:
            continue
        inf = info.get(sym, {})
        out.append(dict(date=d, type="buy", asset=sym, asset_class=asset_class(sym), quantity=abs(qty), price=0.0,
                        currency=_g(inf, "Currency", "Währung", default="USD"), fee=0.0, total=0.0, source="IBKR",
                        note="Gratis-Aktien (Stock Grant)", name=_g(inf, "Description", "Beschreibung"),
                        listing_exchange=_g(inf, "Listing Exch", "Börse")))

    # Kapitalmassnahmen (z.B. Aktiensplit): zusätzliche Stücke ohne Kosten bzw. Abgänge
    for c in recs["ca"]:
        cat = _g(c, "Asset Category", "Vermögenswertkategorie")
        if cat.lower().startswith(("total", "gesamt")):
            continue
        desc = _g(c, "Description", "Beschreibung")
        m = re.match(r"^\s*([A-Z0-9.\-]+)\s*\(", desc)
        qty = parse_num(_g(c, "Quantity", "Menge"))
        d = pdate(_g(c, "Date/Time", "Datum/Zeit", "Report Date", "Berichtsdatum"))
        if not (m and qty and d):
            continue
        sym = m.group(1)
        proceeds = abs(parse_num(_g(c, "Proceeds", "Erlös")) or 0.0)
        inf = info.get(sym, {})
        out.append(dict(date=d, type="buy" if qty > 0 else "sell", asset=sym, asset_class=asset_class(sym, cat),
                        quantity=abs(qty), price=(proceeds / abs(qty)) if proceeds else 0.0,
                        currency=_g(c, "Currency", "Währung", default="USD"), fee=0.0, total=proceeds,
                        source="IBKR", note=desc[:120], name=_g(inf, "Description", "Beschreibung"),
                        listing_exchange=_g(inf, "Listing Exch", "Börse")))
    return out, skipped


def parse_ibkr_flex(rows):
    # Header-/Trailer-Records (HEADER/DATA/BOS/EOS…) entfernen
    if rows and rows[0] and rows[0][0] in ("BOF", "BOA", "BOS", "HEADER", "DATA", "EOS", "EOA", "EOF"):
        hdr = next((r[2:] for r in rows if r[0] == "HEADER"), None)
        rows = [hdr] + [r[2:] for r in rows if r[0] == "DATA"]
    headers = rows[0]
    i_sym = find_col(headers, "Symbol", exclude=("underlying",))
    i_date = find_col(headers, "TradeDate", "DateTime", "Date/Time", "Date")
    i_qty = find_col(headers, "Quantity")
    i_price = find_col(headers, "TradePrice", "Price")
    i_cur = find_col(headers, "CurrencyPrimary", "Currency")
    i_comm = find_col(headers, "IBCommission", "Commission")
    i_bs = find_col(headers, "Buy/Sell")
    i_cls = find_col(headers, "AssetClass")
    i_desc = find_col(headers, "Description")
    i_exch = find_col(headers, "ListingExchange")
    i_proc = find_col(headers, "Proceeds")
    i_id = find_col(headers, "TradeID", "TransactionID")
    if None in (i_sym, i_date, i_qty, i_price):
        raise ImportErrorDetail("IBKR Flex-Query: Spalten Symbol, TradeDate, Quantity, TradePrice werden benötigt.")
    pdate = date_parser_for([cell(r, i_date) for r in rows[1:]])
    out, skipped = [], []
    for n, r in enumerate(rows[1:], start=2):
        if r == headers:
            continue
        cls = cell(r, i_cls).upper()
        if cls == "CASH":
            skipped.append(f"Zeile {n}: Devisentausch übersprungen")
            continue
        qty, price = parse_num(cell(r, i_qty)), parse_num(cell(r, i_price))
        d = pdate(cell(r, i_date))
        sym = cell(r, i_sym)
        if not (qty and d and sym):
            skipped.append(f"Zeile {n}: unvollständig")
            continue
        is_sell = cell(r, i_bs).upper().startswith("SELL") or qty < 0
        comm = abs(parse_num(cell(r, i_comm)) or 0.0)
        proc = parse_num(cell(r, i_proc))
        gross = abs(proc) if proc else abs(qty) * (price or 0)
        out.append(dict(date=d, type="sell" if is_sell else "buy", asset=sym,
                        asset_class=ASSET_CLASS_MAP.get(cls.lower(), "etf"), quantity=abs(qty),
                        price=price or 0.0, currency=cell(r, i_cur) or "USD", fee=comm,
                        total=(gross - comm) if is_sell else (gross + comm), source="IBKR",
                        note=cell(r, i_id), name=cell(r, i_desc), listing_exchange=cell(r, i_exch)))
    return out, skipped


# --------------------------------------------------------------------------
# Generisch (mit Spalten-Zuordnung aus der UI)
# --------------------------------------------------------------------------

CRYPTO = {"BTC", "ETH", "SOL", "ADA", "XRP", "DOT", "LTC", "DOGE", "LINK", "XBT"}
GENERIC_FIELDS = ["date", "type", "asset", "quantity", "price", "total", "fee", "currency"]


def guess_mapping(headers):
    g = {
        "date": find_col(headers, "date", "datum", "tradedate", "zeit"),
        "type": find_col(headers, "type", "typ", "art", "side", "buy/sell", "action"),
        "asset": find_col(headers, "symbol", "ticker", "asset", "isin", "wertpapier", "titel"),
        "quantity": find_col(headers, "quantity", "menge", "anzahl", "stück", "shares", "units", "amount"),
        "price": find_col(headers, "price", "kurs", "preis"),
        "total": find_col(headers, "total", "betrag", "value", "wert", "netamount", "proceeds"),
        "fee": find_col(headers, "fee", "gebühr", "commission", "courtage"),
        "currency": find_col(headers, "currency", "währung", "ccy"),
        "source": find_col(headers, "quelle", "source", "anbieter", "broker", "exchange"),
    }
    return {k: (headers[v] if v is not None else "") for k, v in g.items()}


def parse_generic(rows, mapping, defaults):
    headers = rows[0]
    idx = {k: (headers.index(v) if v in headers else None) for k, v in (mapping or {}).items()}
    if idx.get("date") is None or (idx.get("quantity") is None and idx.get("total") is None):
        raise ImportErrorDetail("Bitte mindestens Datum und Menge (oder Betrag) zuordnen.")
    if idx.get("asset") is None and not defaults.get("asset"):
        raise ImportErrorDetail("Bitte eine Asset-Spalte zuordnen oder ein festes Asset angeben.")
    pdate = date_parser_for([cell(r, idx["date"]) for r in rows[1:]])
    src = defaults.get("source") or "Import"
    out, skipped = [], []
    for n, r in enumerate(rows[1:], start=2):
        d = pdate(cell(r, idx["date"]))
        qty = parse_num(cell(r, idx.get("quantity")))
        price = parse_num(cell(r, idx.get("price")))
        total = parse_num(cell(r, idx.get("total")))
        fee = abs(parse_num(cell(r, idx.get("fee"))) or 0.0)
        t = cell(r, idx.get("type")).lower()
        if not d:
            skipped.append(f"Zeile {n}: Datum unlesbar")
            continue
        if any(k in t for k in ("quellensteuer", "steuer", "withholding", "tax")):
            typ = "tax"
        elif "div" in t or "ausschüttung" in t:
            typ = "dividend"
        elif any(k in t for k in ("sell", "verkauf", "sld", "sale")) or (not t and qty is not None and qty < 0):
            typ = "sell"
        elif not t or any(k in t for k in ("buy", "kauf", "bot", "purchase", "sparplan", "trade")):
            typ = "buy"
        else:
            skipped.append(f"Zeile {n}: Typ '{t}' übersprungen")
            continue
        qty = abs(qty) if qty is not None else None
        if qty is None and price and total:
            qty = abs(total) / price
        if price is None and qty and total:
            price = abs(total) / qty
        if total is None and qty and price:
            total = qty * price + (fee if typ == "buy" else -fee)
        if typ not in ("dividend", "tax") and not qty:
            skipped.append(f"Zeile {n}: Menge fehlt")
            continue
        out.append(dict(date=d, type=typ, asset=(cell(r, idx.get("asset")) or defaults.get("asset", "")).upper(),
                        asset_class="crypto" if (cell(r, idx.get("asset")) or defaults.get("asset", "")).upper() in CRYPTO
                        else (defaults.get("asset_class") or "etf"), quantity=qty or 0.0, price=price or 0.0,
                        currency=(cell(r, idx.get("currency")) or defaults.get("currency") or "CHF").upper()[:3],
                        fee=fee, total=-abs(total or 0.0) if typ == "tax" else abs(total or 0.0),
                        source=cell(r, idx.get("source")) or src, note=""))
    return out, skipped



# --------------------------------------------------------------------------
# Säule 3a: Finpension (CSV) und VIAC (PDF-Belege)
# --------------------------------------------------------------------------
CASH = dict(asset="CASH", name="Konto (Zinsen & Gebühren)", asset_class="cash")


def is_finpension(rows):
    h = {norm(c) for c in rows[0]}
    return {"category", "assetname", "isin", "numberofshares", "cashflow"} <= h


def parse_finpension(rows):
    head = rows[0]
    ix = {k: find_col(head, *c) for k, c in {
        "date": ("date",), "cat": ("category",), "name": ("asset name",), "isin": ("isin",),
        "qty": ("number of shares",), "ccy": ("asset currency",), "price": ("asset price in chf",),
        "flow": ("cash flow",)}.items()}
    out, skipped = [], []
    for n, r in enumerate(rows[1:], start=2):
        cat = cell(r, ix["cat"]).strip()
        c = cat.lower()
        raw = cell(r, ix["date"]).strip()
        d = raw[:10] if re.match(r"\d{4}-\d{2}-\d{2}", raw) else _de_date(raw[:10])
        flow = parse_num(cell(r, ix["flow"])) or 0.0
        if not d:
            skipped.append(f"Zeile {n}: Datum unlesbar")
            continue
        base = dict(date=d, currency="CHF", fee=0.0, source="Finpension 3a", note="")
        isin = cell(r, ix["isin"]).strip().upper()
        name = cell(r, ix["name"]).strip()
        if c in ("buy", "sell"):
            qty = abs(parse_num(cell(r, ix["qty"])) or 0)
            if not qty or not isin:
                skipped.append(f"Zeile {n}: unvollständig")
                continue
            price = parse_num(cell(r, ix["price"])) or (abs(flow) / qty)
            out.append(dict(base, type=c, asset=isin, name=name, asset_class="etf", quantity=qty,
                            price=price, total=abs(flow)))
        elif c in ("dividend", "dividends", "distribution"):
            out.append(dict(base, type="dividend", asset=isin or CASH["asset"], name=name, asset_class="etf",
                            quantity=0.0, price=0.0, total=abs(flow)))
        elif c.startswith("interest"):
            out.append(dict(base, **CASH, type="interest", quantity=0.0, price=0.0, total=abs(flow), note=cat))
        elif "fee" in c:
            out.append(dict(base, **CASH, type="fee", quantity=0.0, price=0.0, total=-abs(flow), note=cat))
        elif "tax" in c:
            out.append(dict(base, type="tax", asset=isin or CASH["asset"], name=name, asset_class="etf",
                            quantity=0.0, price=0.0, total=-abs(flow), note=cat))
        elif c.startswith("deposit"):
            out.append(dict(base, **CASH, type="deposit", quantity=0.0, price=0.0, total=abs(flow), note=cat))
        elif c.startswith("transfer"):
            out.append(dict(base, **CASH, type="transfer", quantity=0.0, price=0.0, total=flow, note=cat))
        elif c.startswith(("withdrawal", "payout", "payment")):
            out.append(dict(base, **CASH, type="withdrawal", quantity=0.0, price=0.0, total=-abs(flow), note=cat))
        else:
            skipped.append(f"Zeile {n}: Typ '{cat}' übersprungen")
    return out, skipped


def _de_date(s):
    try:
        return datetime.strptime(s, "%d.%m.%Y").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _amount_line(text, *labels):
    """Datum (Valuta) und CHF-Betrag aus Zeilen wie 'Verrechneter Betrag:  Valuta 27.07.2023  CHF  55.85'."""
    for lab in labels:
        for m in re.finditer(rf"^\s*{lab}\b.*$", text, re.M):
            line = m.group(0)
            amt = re.findall(r"CHF\s+(-?[\d'’.,]+)", line)
            dt = re.search(r"(\d{2}\.\d{2}\.\d{4})", line)
            if amt:
                return (_de_date(dt.group(1)) if dt else None), parse_num(amt[-1])
    return None, None


def is_viac(text):
    return "viac.ch" in text.lower() or "Terzo Vorsorgestiftung" in text


def parse_viac(text, fname=""):
    """Ein VIAC-Beleg (PDF-Text) -> (transaktion | None, grund_zum_überspringen | None)."""
    t = text.replace("\f", "\n")
    letter = re.search(r"Basel,\s*(\d{2}\.\d{2}\.\d{4})", t)
    letter_d = _de_date(letter.group(1)) if letter else None
    on = re.search(r"Am (\d{2}\.\d{2}\.\d{4})", t)
    on_d = _de_date(on.group(1)) if on else None
    pos = re.search(r"^\s*([\d'’.,]+)\s+Ant(?:eile?|\.)?\s+(.+?)\s*$", t, re.M)
    isin = re.search(r"ISIN:?\s+([A-Z]{2}[A-Z0-9]{9}\d)", t)
    base = dict(currency="CHF", fee=0.0, source="VIAC 3a", note="")
    label = fname or "Beleg"

    if re.search(r"Börsenabrechnung|^\s*Order:", t, re.M):
        kind = re.search(r"Order:\s+(Kauf|Verkauf)", t) or re.search(r"Börsenabrechnung\s*-\s*(Kauf|Verkauf)", t)
        if not (kind and pos and isin):
            return None, f"{label}: Börsenabrechnung nicht lesbar"
        d, total = _amount_line(t, "Verrechneter Betrag", "Gutgeschriebener Betrag")
        kurs = re.search(r"Kurs:\s+([A-Z]{3})\s+([\d'’.,]+)", t)
        # VIAC rundet die Anteile auf 2–3 Stellen; genauer ist Betrag / Kurs (in der Kurswährung)
        qty = parse_num(pos.group(1)) or 0.0
        betrag = re.search(r"^\s*Betrag\s+([A-Z]{3})\s+(-?[\d'’.,]+)", t, re.M)
        if kurs and betrag and betrag.group(1) == kurs.group(1):
            px, amt_ccy = parse_num(kurs.group(2)), parse_num(betrag.group(2))
            if px and amt_ccy:
                qty = round(abs(amt_ccy) / px, 6)
        if total == 0:
            return None, f"{label}: Börsenabrechnung über CHF 0.00 übersprungen"
        if not total or not qty:
            return None, f"{label}: Börsenabrechnung ohne Betrag"
        fees = sum(abs(parse_num(x) or 0) for x in re.findall(
            r"^\s*(?:Courtage|Kommission|Gebühr(?:en)?|Eidg\. Umsatzabgabe|Stempel\w*)\b.*?CHF\s+(-?[\d'’.,]+)", t, re.M))
        return dict(base, date=d or letter_d, type="buy" if kind.group(1) == "Kauf" else "sell",
                    asset=isin.group(1), name=pos.group(2), asset_class="etf", quantity=abs(qty),
                    price=abs(total) / abs(qty), total=abs(total), fee=fees,
                    note=f"Kurs {kurs.group(1)} {kurs.group(2)}" if kurs else ""), None

    if re.search(r"Dividendenaussch[üu]ttung|Aussch[üu]ttung", t):
        d, total = _amount_line(t, "Gutgeschriebener Betrag", "Verrechneter Betrag", "Belasteter Betrag")
        if total is not None and re.search(r"storniert|Belasteter Betrag", t):
            total = -abs(total)          # Korrektur / Storno einer früheren Ausschüttung
        if total is None or not isin:
            return None, f"{label}: Ausschüttung nicht lesbar"
        art = re.search(r"Dividendenart:\s+(.+)", t)
        note = art.group(1).strip() if art else ""
        if total < 0:
            note = ("Storno " + note).strip()
        return dict(base, date=d or letter_d, type="dividend", asset=isin.group(1),
                    name=pos.group(2) if pos else "", asset_class="etf", quantity=0.0, price=0.0,
                    total=total, note=note), None

    if re.search(r"^\s*Zins\s*$|Zinsgutschrift", t, re.M):
        d, total = _amount_line(t, "Verrechneter Betrag", "Zinsgutschrift")
        if total is None:
            return None, f"{label}: Zinsbeleg nicht lesbar"
        rate = re.search(r"Zinssatz:\s+([\d.,]+\s*%)", t)
        return dict(base, **CASH, date=on_d or d or letter_d, type="interest", quantity=0.0, price=0.0,
                    total=abs(total), note=f"Zins {rate.group(1)}" if rate else "Zins"), None

    # Kontosaldierung (beim Wechsel der Stiftung): Schlusszins als Zins buchen, der Saldo selbst ist ein Transfer
    if "Kontosaldierung" in t:
        zins = re.search(r"^\s*Zins:\s+CHF\s+(-?[\d'’.,]+)", t, re.M)
        geb = re.search(r"^\s*Gebühr:\s+CHF\s+(-?[\d'’.,]+)", t, re.M)
        d, _ = _amount_line(t, "Zu vergütender Betrag", "Abrechnung")
        z, g = (parse_num(zins.group(1)) if zins else 0) or 0, (parse_num(geb.group(1)) if geb else 0) or 0
        if g:
            # selten: Schlussgebühr – als eigene Zeile zusätzlich zum Zins nicht abbildbar, daher netto
            z -= abs(g)
        if not z:
            return None, f"{label}: Kontosaldierung ohne Zins/Gebühr übersprungen"
        return dict(base, **CASH, date=d or letter_d, type="interest" if z > 0 else "fee", quantity=0.0, price=0.0,
                    total=z, note="Schlusszins Kontosaldierung" if z > 0 else "Kontosaldierung"), None

    # Eingang: eigene Einzahlung oder Übertrag von einer anderen Stiftung
    if re.search(r"Einzahlung|Gutschrift vom|Zahlungseingang", t):
        d, total = _amount_line(t, "Gutschrift", "Zahlungseingang", "Verrechneter Betrag")
        if total is None:
            return None, f"{label}: Einzahlung ohne Betrag"
        typ = re.search(r"Zahlungstyp:\s+(.+)", t)
        incoming = re.search(r"Übertrag|Freizügigkeit|Vorsorgestiftung", typ.group(1)) if typ else None
        return dict(base, **CASH, date=d or letter_d, type="transfer" if incoming else "deposit",
                    quantity=0.0, price=0.0, total=abs(total),
                    note=typ.group(1).strip() if typ else "Einzahlung"), None

    # Ausgang: Übertrag an eine andere Stiftung (Transfer) oder Auszahlung (Bezug)
    if re.search(r"Vergütung|Begünstigte|Auszahlung|Übertrag|Freizügigkeit", t):
        d, total = _amount_line(t, "Belastung", "Vergütungsauftrag", "Verrechneter Betrag", "Auszahlung")
        to = re.search(r"Begünstigter:\s+(.+)", t)
        if total is None:
            return None, f"{label}: Auszahlung ohne Betrag"
        is_transfer = bool(to and re.search(r"Stiftung|Vorsorge|3a|Freizügigkeit", to.group(1), re.I))
        return dict(base, **CASH, date=d or letter_d, type="transfer" if is_transfer else "withdrawal",
                    quantity=0.0, price=0.0, total=-abs(total),
                    note=f"an {to.group(1).strip()}" if to else "Auszahlung"), None

    if re.search(r"Verwaltungsgeb[üu]hr", t):
        d, total = _amount_line(t, "Verrechneter Betrag", "Belastung")
        if total is None:
            return None, f"{label}: Gebührenbeleg nicht lesbar"
        return dict(base, **CASH, date=d or on_d or letter_d, type="fee", quantity=0.0, price=0.0,
                    total=-abs(total), note="Verwaltungsgebühr"), None

    return None, f"{label}: Belegart nicht erkannt"


def parse_documents(docs):
    """docs: Liste (dateiname, text) aus PDF-Belegen -> (transaktionen, übersprungen, anbieter)."""
    out, skipped = [], []
    providers = set()
    for fname, text in docs:
        if is_viac(text):
            providers.add("viac")
            tx, why = parse_viac(text, fname)
        else:
            tx, why = None, f"{fname}: Anbieter nicht erkannt (unterstützt: VIAC)"
        if tx:
            if not tx.get("date"):
                skipped.append(f"{fname}: Datum nicht gefunden")
                continue
            out.append(tx)
        if why:
            skipped.append(why)
    out.sort(key=lambda x: x["date"])
    return out, skipped, ("viac" if providers == {"viac"} else "pdf")

# --------------------------------------------------------------------------
# Einstiegspunkt
# --------------------------------------------------------------------------

def detect(rows):
    if is_finpension(rows):
        return "finpension"
    if is_ibkr_statement(rows):
        return "ibkr"
    if is_ibkr_flex(rows):
        return "ibkr"
    if is_relai(rows):
        return "relai"
    return "generic"


def parse(text, provider="auto", mapping=None, defaults=None):
    rows = read_rows(text)
    if len(rows) < 2:
        raise ImportErrorDetail("Die Datei enthält keine Daten.")
    detected = detect(rows) if provider in (None, "", "auto") else provider
    if detected == "ibkr":
        txs, skipped = parse_ibkr_statement(rows) if is_ibkr_statement(rows) else parse_ibkr_flex(rows)
    elif detected == "relai":
        txs, skipped = parse_relai(rows)
    elif detected == "finpension":
        txs, skipped = parse_finpension(rows)
    else:
        auto = guess_mapping(rows[0])
        if not mapping and all(auto.get(k) for k in ("date", "asset", "quantity", "total", "source")):
            mapping = auto
            detected = "standard"
        if not mapping:
            return dict(provider="generic", needs_mapping=True, headers=rows[0],
                        sample=rows[1:6], mapping=guess_mapping(rows[0]), transactions=[], skipped=[])
        txs, skipped = parse_generic(rows, mapping, defaults or {})
    return dict(provider=detected, needs_mapping=False, transactions=txs, skipped=skipped,
                headers=rows[0], sample=[])
