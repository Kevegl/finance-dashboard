"""Gemeindesteuerfüsse (offene Daten des Statistischen Amts ZH bzw. eigener Upload)."""
import csv
import io
import json
import re
import threading
import time
import urllib.request

import local_update
from store import get_setting, json_setting, set_setting
from prices import UA


def save_muni_rates(body, src="upload", info=None):
    """Steuerfüsse eines Jahres speichern oder löschen. rates: {Gemeinde: [[ohne Kirche, ref., kath., christkath.], …]}
    (mehrere Einträge, wenn eine Gemeinde je nach Schulgemeinde/Kirchgemeinde verschiedene Steuerfüsse hat)."""
    if not isinstance(body, dict):
        raise ValueError("Ungültige Daten")
    canton = str(body.get("canton") or "")
    year = str(body.get("year") or "")
    if not re.fullmatch(r"[A-Z]{2}", canton) or not re.fullmatch(r"20\d\d", year):
        raise ValueError("Ungültige Daten")
    try:
        allr = json.loads(get_setting("muni_rates", "{}"))
    except ValueError:
        allr = {}
    if not isinstance(allr, dict):
        allr = {}
    meta = json_setting("muni_meta", {})
    years_meta = meta.setdefault(canton, {}).setdefault("years", {})
    if body.get("delete"):
        allr.get(canton, {}).pop(year, None)
        years_meta.pop(year, None)
    else:
        rates = body.get("rates")
        if not isinstance(rates, dict) or not 0 < len(rates) <= 400:
            raise ValueError("Keine Steuerfüsse gefunden")
        clean = {}
        for name, variants in rates.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 60 or not isinstance(variants, list):
                raise ValueError("Ungültige Daten")
            vs = []
            for v in variants[:3]:
                if not isinstance(v, list) or len(v) != 4:
                    raise ValueError("Ungültige Daten")
                vs.append([float(x) if isinstance(x, (int, float)) and 0 <= x <= 400 else None for x in v])
            if not vs or vs[0][0] is None:
                raise ValueError("Ungültige Daten")
            clean[name.strip()] = vs
        allr.setdefault(canton, {})[year] = clean
        years_meta[year] = {"src": src, **(info or {})}
        if len(allr[canton]) > 10:   # nur die letzten 10 Jahre behalten
            for y in sorted(allr[canton])[:-10]:
                del allr[canton][y]
                years_meta.pop(y, None)
    set_setting("muni_rates", json.dumps(allr, ensure_ascii=False, separators=(",", ":")))
    set_setting("muni_meta", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    return {"ok": True, "years": sorted(allr.get(canton, {}))}


# Offene Daten der Kantone: aktuelle Gemeindesteuerfüsse (natürliche Personen) als CSV
MUNI_SOURCES = {
    "ZH": "https://www.web.statistik.zh.ch/ogd/data/steuerfuesse/kanton_zuerich_stf_aktuell.csv",
}
MUNI_CHECK_EVERY = 24 * 3600
MUNI_LOCK = threading.Lock()


def parse_zh_muni_csv(text):
    """CSV des Statistischen Amts ZH -> {Jahr: {Gemeinde: Varianten}}, {Jahr: {flag, refresh}}."""
    groups = ["STF_O_KIRCHE", "STF_REF", "STF_KATH", "STF_CKRKATH"]
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or not {"YEAR", "STF_O_KIRCHE1"} <= set(reader.fieldnames):
        raise ValueError("Unerwartetes Format der Steuerfuss-Daten")

    def num(x):
        try:
            v = float(str(x).strip())
        except ValueError:
            return None
        return v if 0 < v <= 400 else None   # leer oder 0 = keine weitere Variante
    data, info = {}, {}
    for row in reader:
        year, name = str(row.get("YEAR") or "").strip(), (row.get("COMMUNITY") or row.get("GDE_NAME") or "").strip()
        if not re.fullmatch(r"20\d\d", year) or not name:
            continue
        vals = [[num(row.get(f"{g}{i}")) for i in (1, 2, 3)] for g in groups]
        if vals[0][0] is None:
            continue
        n = max(3 if v[2] is not None else 2 if v[1] is not None else 1 for v in vals)
        variants = []
        for j in range(n):
            v = [col[j] if col[j] is not None else col[0] for col in vals]
            if v not in variants:
                variants.append(v)
        data.setdefault(year, {})[name] = variants
        info.setdefault(year, {"flag": (row.get("FLAG") or "").strip()[:20], "refresh": (row.get("LAST_REFRESH") or "").strip()[:20]})
    data = {y: rows for y, rows in data.items() if len(rows) >= 50}
    if not data:
        raise ValueError("Keine Steuerfüsse gefunden")
    return data, info


def refresh_muni_rates(canton="ZH", force=False):
    """Steuerfüsse beim Kanton abholen (höchstens einmal täglich). Hochgeladene Jahre werden nur mit force ersetzt."""
    url = MUNI_SOURCES.get(canton)
    if not url or (not force and get_setting("muni_auto", "1") != "1"):
        return
    with MUNI_LOCK:
        meta = json_setting("muni_meta", {})
        m = meta.get(canton, {})
        if not force and time.time() - m.get("checked", 0) < MUNI_CHECK_EVERY:
            return
        err = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv"})
            with local_update.urlopen(req, timeout=20) as r:
                text = r.read(2_000_000).decode("utf-8-sig", "replace")
            data, info = parse_zh_muni_csv(text)
            for year, rows in data.items():
                if not force and m.get("years", {}).get(year, {}).get("src") == "upload":
                    continue
                save_muni_rates({"canton": canton, "year": year, "rates": rows}, src="auto", info=info.get(year))
        except Exception as e:  # noqa: BLE001
            err = str(e)[:200]
            print("Steuerfüsse laden fehlgeschlagen:", err)
        meta = json_setting("muni_meta", {})   # neu lesen: save_muni_rates hat die Jahre ergänzt
        meta.setdefault(canton, {}).update(checked=time.time(), error=err)
        set_setting("muni_meta", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    if err and force:
        raise ValueError(f"Steuerfüsse konnten nicht geladen werden: {err}")


def muni_state():
    return {"rates": json_setting("muni_rates", {}), "meta": json_setting("muni_meta", {}),
            "auto": get_setting("muni_auto", "1") == "1", "sources": sorted(MUNI_SOURCES)}
