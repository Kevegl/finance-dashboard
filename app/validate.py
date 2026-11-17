"""Prüfung von Transaktionen (Datum, Zahlen, Typ) vor dem Speichern."""
import math
import re
from datetime import date

from config import TX_TYPES


DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_iso_date(s):
    """True für ein existierendes Datum JJJJ-MM-TT (nicht nur das Format: 2024-13-45 ist ungültig)."""
    if not isinstance(s, str) or not ISO_DATE.fullmatch(s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


def valid_date(s):
    if not is_iso_date(s):
        raise ValueError("Ungültiges Datum")
    return s


def fnum(v, default=0.0):
    """Zahl aus einer Eingabe; leer -> default. Unendlich/NaN wären in JSON für den Browser unlesbar."""
    if v is None or v == "":
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("Ungültige Zahl") from None
    if not math.isfinite(x):
        raise ValueError("Ungültige Zahl")
    return x


def normalize_tx(b, base):
    """Prüft eine manuell erfasste oder bearbeitete Transaktion und vereinheitlicht Vorzeichen und Betrag."""
    if not isinstance(b, dict):
        raise ValueError("Ungültige Daten")
    typ = b.get("type", "buy")
    if typ not in TX_TYPES:
        raise ValueError("Ungültiger Typ")
    asset = str(b.get("asset") or "").strip().upper()
    if not asset:
        raise ValueError("Asset fehlt")
    qty, price, fee = fnum(b.get("quantity")), fnum(b.get("price")), fnum(b.get("fee"))
    total = fnum(b.get("total"), qty * price + (fee if typ == "buy" else -fee))
    if not math.isfinite(total):          # z.B. Menge × Kurs ausserhalb des Zahlenbereichs
        raise ValueError("Ungültige Zahl")
    if typ in ("tax", "fee", "withdrawal"):
        total = -abs(total)
    elif typ in ("interest", "deposit"):
        total = abs(total)
    ccy = str(b.get("currency") or base).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", ccy):
        raise ValueError("Unbekannte Währung")
    return dict(date=valid_date(b.get("date")), type=typ, asset=asset, quantity=qty, price=price,
                currency=ccy, fee=fee, total=total, note=str(b.get("note") or ""))


def import_problem(t):
    """Grund, warum eine importierte Transaktion nicht gespeichert werden kann (sonst None)."""
    if not is_iso_date(t.get("date")):
        return "Datum unlesbar"
    for k in ("quantity", "price", "fee", "total"):
        v = t.get(k)
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            return "ungültige Zahl"
    return None
