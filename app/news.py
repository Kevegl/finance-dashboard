"""News-Ticker: Google News RSS, serverseitig abgerufen und zwischengespeichert."""
import re
import threading
import time
import urllib.parse
import urllib.request

import local_update
from store import db_tx
from prices import UA
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
        now = time.time()
        if len(NEWS_CACHE) > 300:      # abgelaufene Einträge entfernen, damit der Speicher nicht stetig wächst
            for k in [k for k, v in NEWS_CACHE.items() if now - v[0] >= NEWS_TTL]:
                del NEWS_CACHE[k]
        NEWS_CACHE[key] = (now, items)
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
    with db_tx() as con:
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
    with db_tx() as con:
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
