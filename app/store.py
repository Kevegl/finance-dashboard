"""SQLite-Datenbank: Verbindungen, Schema (init_db, nur additive Migrationen), Einstellungen, Sicherung.

Mehrere Benutzer: Jeder Benutzer hat eine eigene Datenbank. finance.db gehört dem Administrator (dem bei der
Ersteinrichtung angelegten Benutzer, uid 0) und enthält zusätzlich die Benutzerliste und alle Sitzungen; weitere
Benutzer liegen in users/u<id>.db. use_user()/user_context() wählen die Datenbank für den aktuellen Thread."""
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import config
from config import CTX, CURRENCIES, DATA_DIR, DB_PATH, LANGS, USERS_DIR

DB_LOCK = threading.RLock()

EXCH_SUFFIX = {
    "LSE": ".L", "LSEETF": ".L", "LSEIOB1": ".L", "SWX": ".SW", "EBS": ".SW", "VIRTX": ".SW",
    "IBIS": ".DE", "IBIS2": ".DE", "XETRA": ".DE", "FWB": ".F", "FWB2": ".F", "AEB": ".AS",
    "SBF": ".PA", "BVME": ".MI", "BVME.ETF": ".MI", "ENEXT.BE": ".BR", "TSE": ".TO", "ASX": ".AX",
    "SEHK": ".HK", "NYSE": "", "NASDAQ": "", "ARCA": "", "AMEX": "", "BATS": "", "NYSEARCA": "",
}


# --------------------------------------------------------------------------
# Datenbank
# --------------------------------------------------------------------------

def user_db_path(uid):
    """Datenbank eines Benutzers: uid 0 = Administrator (finance.db), sonst users/u<uid>.db."""
    uid = int(uid or 0)
    return DB_PATH if uid == 0 else USERS_DIR / f"u{uid}.db"


def current_uid():
    return getattr(CTX, "uid", 0) or 0


def current_db():
    return getattr(CTX, "db", None) or DB_PATH


def use_user(uid):
    """Wählt für den aktuellen Thread die Datenbank des Benutzers (inkl. dessen Basiswährung)."""
    uid = int(uid or 0)
    CTX.uid, CTX.db, CTX.base = uid, user_db_path(uid), None
    b = get_setting("base_ccy") if uid == 0 or CTX.db.is_file() else None   # neue Datenbank: noch nichts gespeichert
    CTX.base = b if b in CURRENCIES else (config.BASE_DEFAULT if uid else None)


@contextmanager
def user_context(uid):
    """Vorübergehend als anderer Benutzer arbeiten (z.B. Hintergrund-Aktualisierung aller Benutzer)."""
    old = (getattr(CTX, "uid", None), getattr(CTX, "db", None), getattr(CTX, "base", None))
    use_user(uid)
    try:
        yield
    finally:
        CTX.uid, CTX.db, CTX.base = old


def spawn(fn, *args):
    """Startet fn in einem Hintergrund-Thread mit der Datenbank des aktuellen Benutzers."""
    uid = current_uid()

    def run():
        use_user(uid)
        fn(*args)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def connect(main=False):
    """Neue Verbindung zur Datenbank des aktuellen Benutzers (main=True: Hauptdatenbank mit Benutzern/Sitzungen).
    Lieber db_tx() verwenden – das schliesst die Verbindung wieder."""
    con = sqlite3.connect(DB_PATH if main else current_db(), timeout=10)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def db_tx(main=False):
    """Eine Datenbank-Transaktion: committet bei Erfolg, rollt bei Fehlern zurück und schliesst die Verbindung.
    (sqlite3-Verbindungen als Kontextmanager schliessen sich nicht selbst – früher blieb jede offen, bis der
    Garbage Collector sie aufräumte.) DB_LOCK serialisiert Schreib- und Lesezugriffe innerhalb des Prozesses."""
    with DB_LOCK:
        con = connect(main)
        try:
            with con:
                yield con
        finally:
            con.close()


def get_setting(key, default=None, main=False):
    with db_tx(main) as con:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(key, value, main=False):
    with db_tx(main) as con:
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


def list_portfolios():
    with db_tx() as con:
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
    with db_tx() as con:
        data = {"app": "finance-dashboard", "version": 2,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "base": config.BASE,
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
    with db_tx() as con:
        for table in ("transactions", "assets", "portfolios", "prices", "price_meta"):
            con.execute(f"DELETE FROM {table}")
        for table, cols in BACKUP_TABLES.items():
            src_cols = data.get(table + "_columns") or cols
            if not isinstance(src_cols, list) or not all(isinstance(c, str) for c in src_cols):
                raise ValueError("Das ist keine gültige Sicherungsdatei des Finanz-Dashboards.")
            use = [c for c in src_cols if c in cols]
            idx = [src_cols.index(c) for c in use]
            rows = [[row[i] for i in idx] for row in data.get(table) or []
                    if isinstance(row, list) and len(row) == len(src_cols)]
            con.executemany(f"INSERT INTO {table} ({','.join(use)}) VALUES ({','.join('?' * len(use))})", rows)
            counts[table] = len(rows)
        prices = [tuple(r[:3]) for r in data.get("prices") or [] if isinstance(r, list) and len(r) >= 3]
        con.executemany("INSERT OR REPLACE INTO prices(ticker, date, close) VALUES (?,?,?)", prices)
        counts["prices"] = len(prices)
        if "kind" not in (data.get("portfolios_columns") or []):   # ältere Sicherung ohne Portfolio-Typ
            con.execute("""UPDATE portfolios SET kind='3a' WHERE id IN (SELECT DISTINCT portfolio_id FROM transactions
                           WHERE type IN ('deposit','withdrawal','transfer'))""")
        for k, v in (data.get("settings") or {}).items():
            if isinstance(k, str) and not k.startswith("auth_") and (v is None or isinstance(v, str)):
                con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (k, v))
        # Basiswährung der Sicherung sofort übernehmen (nicht erst nach einem Neustart)
        r = con.execute("SELECT value FROM settings WHERE key='base_ccy'").fetchone()
        if r and r[0] in CURRENCIES:
            config.BASE = r[0]
    return counts


def init_db():
    """Legt die Datenbank des aktuellen Benutzers an bzw. migriert sie (nur additiv)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    is_main = current_db() == DB_PATH
    if not is_main:
        USERS_DIR.mkdir(parents=True, exist_ok=True)
    with db_tx() as con:
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
        if is_main:
            # Weitere Benutzer (eigene Datenbank users/u<id>.db) und deren Sitzungen. Die Sitzungen des Administrators
            # bleiben in «sessions», damit eine ältere Version nach einem Rollback keine fremde Sitzung annimmt.
            con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
                created REAL
            );
            CREATE TABLE IF NOT EXISTS user_sessions (
                token_hash TEXT PRIMARY KEY, uid INTEGER NOT NULL, created REAL, expires REAL
            );
            """)
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
            config.BASE = r[0]
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
        ticker = f"BTC-{config.BASE}"
    elif tx.get("asset_class") == "crypto":
        ticker = f"{sym}-{config.BASE}"
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


def get_lang():
    v = get_setting("lang", "de")
    return v if v in LANGS else "de"
