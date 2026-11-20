"""Portfolio-Berechnung: Bestände, Kostenbasis (Durchschnittskosten), Erträge, Kontoguthaben, Historie."""
import bisect
from datetime import date, datetime, timedelta

import config
from config import FLOW_TYPES
from validate import import_problem
from store import db_tx, get_setting
from prices import Series


def cash_effect(t, amount):
    """Wirkung einer Transaktion auf das Kontoguthaben (amount = Betrag in Basiswährung)."""
    if t["type"] == "buy":
        return -abs(amount)
    if t["type"] == "sell":
        return abs(amount)
    if t["type"] == "withdrawal":
        return -abs(amount)
    return amount            # deposit (+), transfer (±), dividend, interest (+), tax, fee (−)


def change_24h(s, today=None):
    """Kursveränderung in % gegenüber dem letzten Kurs vor dem Tag des aktuellen Kurses (Vortagesschluss).

    Liefert None ohne Vergleichskurs oder wenn der letzte Kurs veraltet ist (älter als 3 Tage)."""
    if not s or len(s.d) < 2:
        return None
    today = today or date.today()
    try:
        last_day = datetime.strptime(s.d[-1], "%Y-%m-%d").date()
    except ValueError:
        return None
    if last_day < today - timedelta(days=3):
        return None
    ref_day = (last_day - timedelta(days=1)).isoformat()
    k = bisect.bisect_right(s.d, ref_day) - 1
    if k < 0:
        return None
    ref, cur = s.v[k], s.v[-1]
    if not ref or cur is None:
        return None
    return (cur - ref) / ref * 100


def compute_summary(pid):
    with db_tx() as con:
        txs = [dict(r) for r in con.execute("SELECT * FROM transactions WHERE portfolio_id=? ORDER BY date, id",
                                            (pid,))]
        # Ältere Einträge mit unlesbarem Datum oder unendlichen Werten würden die ganze Berechnung abbrechen:
        # überspringen und als Hinweis melden (in der Transaktionsliste bleiben sie sicht- und korrigierbar).
        invalid_tx = {t["id"] for t in txs if import_problem(t)}
        if invalid_tx:
            txs = [t for t in txs if t["id"] not in invalid_tx]
        row = con.execute("SELECT COALESCE(kind,'standard') FROM portfolios WHERE id=?", (pid,)).fetchone()
        kind = row[0] if row else "standard"
        assets = {r["symbol"]: dict(r) for r in con.execute("SELECT * FROM assets")}
        meta = {r["ticker"]: dict(r) for r in con.execute("SELECT * FROM price_meta")}

        def series(t):
            return Series([(r[0], r[1]) for r in con.execute(
                "SELECT date, close FROM prices WHERE ticker=? ORDER BY date", (t,))])

        price_series = {}
        price_ccy = {}
        for sym, a in assets.items():
            t = a["ticker"]
            if t and meta.get(t, {}).get("currency"):
                s = series(t)
                if s.v:
                    price_series[sym] = s
                    price_ccy[sym] = meta[t]["currency"]
        all_ccy = {t["currency"] for t in txs} | set(price_ccy.values())
        fx = {c: series(f"{c}{config.BASE}=X") for c in all_ccy if c != config.BASE}

    def fx_rate(cur, day=None):
        if cur == config.BASE:
            return 1.0
        s = fx.get(cur)
        if not s or not s.v:
            return None
        return s.lookup(day) if day else s.last()

    fx_missing = sorted(c for c in all_ccy if c != config.BASE and not (fx.get(c) and fx[c].v))

    # --- Bestände und Kostenbasis je Asset (Durchschnittskostenmethode, in Basiswährung)
    pos = {}
    events_by_day = {}
    for t in txs:
        rate = fx_rate(t["currency"], t["date"])
        if rate is None:
            rate = 1.0
        t["total_base"] = t["total"] * rate
        t["fee_base"] = (t["fee"] or 0) * rate
        events_by_day.setdefault(t["date"], []).append(t)

    def blank(sym):
        return dict(symbol=sym, qty=0.0, cost=0.0, cost_ccy=0.0, realized=0.0, dividends=0.0, taxes=0.0,
                    interest=0.0, acct_fees=0.0, basis_missing=False,
                    last_tx_price=None, last_tx_ccy=None, sources=set())

    for t in txs:
        p = pos.setdefault(t["asset"], blank(t["asset"]))
        p["sources"].add(t["source"])
        if t["type"] == "buy":
            p["qty"] += t["quantity"]
            p["cost"] += t["total_base"]
            p["cost_ccy"] += t["total"]
            if t["price"]:
                p["last_tx_price"], p["last_tx_ccy"] = t["price"], t["currency"]
        elif t["type"] == "sell":
            if p["qty"] > 0:
                share = min(1.0, t["quantity"] / p["qty"])
                removed = p["cost"] * share
                p["cost_ccy"] -= p["cost_ccy"] * share
                p["cost"] -= removed
                p["realized"] += t["total_base"] - removed
            else:
                # Kauf nicht erfasst (z.B. IBKR-Auszug ab einem späteren Datum): Erlös nicht verschlucken,
                # sondern ohne Kostenbasis als realisiert zählen und in der Übersicht darauf hinweisen.
                p["realized"] += t["total_base"]
                p["basis_missing"] = True
            rest = p["qty"] - t["quantity"]
            # Rundungsreste (Anbieter runden Anteile auf 2–3 Stellen) gelten als geschlossen
            p["qty"] = rest if rest > max(1e-8, p["qty"] * 1e-3) else 0.0
            if not p["qty"]:
                p["cost"] = p["cost_ccy"] = 0.0
            if t["price"]:
                p["last_tx_price"], p["last_tx_ccy"] = t["price"], t["currency"]
        elif t["type"] == "dividend":
            p["dividends"] += t["total_base"]
        elif t["type"] == "tax":
            p["taxes"] += t["total_base"]
        elif t["type"] == "interest":
            p["interest"] += t["total_base"]
        elif t["type"] == "fee":
            p["acct_fees"] += abs(t["total_base"])

    holdings = []
    for sym, p in pos.items():
        a = assets.get(sym, {})
        price, ccy, price_src, chg = None, None, "—", None
        if a.get("manual_price"):
            price, ccy, price_src = a["manual_price"], p["last_tx_ccy"] or config.BASE, "manuell"
        elif sym in price_series:
            price, ccy, price_src = price_series[sym].last(), price_ccy[sym], "live"
            chg = change_24h(price_series[sym])
        elif p["last_tx_price"]:
            price, ccy, price_src = p["last_tx_price"], p["last_tx_ccy"], "letzter Kauf"
        rate = fx_rate(ccy) if ccy else None
        value = p["qty"] * price * (rate if rate is not None else 1.0) if price else 0.0
        holdings.append(dict(
            symbol=sym, name=a.get("name") or sym, asset_class=a.get("asset_class", "etf"),
            ticker=a.get("ticker", ""), manual_price=a.get("manual_price"), color_slot=a.get("color_slot", 0),
            news_query=a.get("news_query") or "",
            quantity=p["qty"], cost=p["cost"], avg_price=(p["cost_ccy"] / p["qty"]) if p["qty"] else None,
            avg_ccy=p["last_tx_ccy"], price=price, price_ccy=ccy, price_source=price_src,
            change_24h=chg if p["qty"] else None,
            price_error=meta.get(a.get("ticker", ""), {}).get("error"),
            value=value, gain=value - p["cost"] if p["qty"] else 0.0,
            gain_pct=((value - p["cost"]) / p["cost"] * 100) if p["cost"] > 0 and p["qty"] else None,
            realized=p["realized"], dividends=p["dividends"], taxes=p["taxes"], sources=sorted(p["sources"]),
            interest=p["interest"], acct_fees=p["acct_fees"], basis_missing=p["basis_missing"],
        ))
    # Portfolios mit erfassten Ein-/Auszahlungen (z.B. Säule 3a): Kontoguthaben und eigene Einlagen
    flow_txs = any(t["type"] in FLOW_TYPES for t in txs)
    has_flows = kind == "3a" and flow_txs      # nur 3a-Portfolios rechnen mit Einzahlungen und Kontoguthaben
    flows = None
    if has_flows:
        cash = sum(cash_effect(t, t["total_base"]) for t in txs)
        if -1.0 < cash < 0:     # Rundungsreste aus Belegen (z.B. −0.11 nach Saldierung) – kein echtes Minus
            cash = 0.0
        dep = sum(t["total_base"] for t in txs if t["type"] == "deposit")
        wd = sum(abs(t["total_base"]) for t in txs if t["type"] == "withdrawal")
        t_in = sum(t["total_base"] for t in txs if t["type"] == "transfer" and t["total_base"] > 0)
        t_out = -sum(t["total_base"] for t in txs if t["type"] == "transfer" and t["total_base"] < 0)
        net = dep - wd + t_in - t_out
        flows = dict(cash=cash, deposits=dep, withdrawals=wd, transfer_in=t_in, transfer_out=t_out,
                     own=dep - wd, net=net)
        h = next((x for x in holdings if x["symbol"] == "CASH"), None)
        if h is None:
            a = assets.get("CASH", {})
            h = dict(symbol="CASH", name=a.get("name") or "Konto", asset_class="cash", ticker="", manual_price=None,
                     color_slot=a.get("color_slot", 7), news_query="", cost=0.0, avg_price=None, avg_ccy=config.BASE,
                     price_error=None, realized=0.0, dividends=0.0, taxes=0.0, interest=0.0, acct_fees=0.0,
                     sources=[])
            holdings.append(h)
        h.update(name="Kontoguthaben", asset_class="cash", quantity=cash if abs(cash) >= 0.005 else 0.0,
                 price=1.0, price_ccy=config.BASE, price_source="Konto", change_24h=None, value=cash, cost=cash, gain=0.0, gain_pct=None,
                 avg_price=1.0, avg_ccy=config.BASE, is_cash=True)
    holdings.sort(key=lambda h: -h["value"])
    total_value = sum(h["value"] for h in holdings)
    total_cost = sum(h["cost"] for h in holdings if h["quantity"] > 0 and not h.get("is_cash"))
    for h in holdings:
        h["weight"] = (h["value"] / total_value * 100) if total_value and h["value"] > 0 else 0

    # --- Tägliche Historie: Wert vs. investiertes Kapital
    history = []
    if txs:
        start = datetime.strptime(txs[0]["date"], "%Y-%m-%d").date()
        today = date.today()
        qty = {}
        cost = {}
        cost_ccy = {}
        last_px = {}
        fx_seq = {c: Series(list(zip(s.d, s.v))) for c, s in fx.items()}
        px_seq = {k: Series(list(zip(s.d, s.v))) for k, s in price_series.items()}
        manual = {s: a["manual_price"] for s, a in assets.items() if a.get("manual_price")}
        d = start
        cash_h = net_h = 0.0
        while d <= today:
            ds = d.isoformat()
            fx_today = {c: s.at(ds) for c, s in fx_seq.items()}
            for t in events_by_day.get(ds, []):
                s = t["asset"]
                if has_flows:
                    cash_h += cash_effect(t, t["total_base"])
                    if t["type"] in FLOW_TYPES:
                        net_h += t["total_base"] if t["type"] != "withdrawal" else -abs(t["total_base"])
                if t["type"] == "buy":
                    qty[s] = qty.get(s, 0) + t["quantity"]
                    cost[s] = cost.get(s, 0) + t["total_base"]
                    if t["price"]:
                        last_px[s] = (t["price"], t["currency"])
                elif t["type"] == "sell":
                    q = qty.get(s, 0)
                    if q > 0:
                        cost[s] = cost.get(s, 0) * (1 - min(1.0, t["quantity"] / q))
                    rest = q - t["quantity"]
                    qty[s] = rest if rest > max(1e-8, q * 1e-3) else 0.0
                    if t["price"]:
                        last_px[s] = (t["price"], t["currency"])
            value = 0.0
            for s, q in qty.items():
                if q <= 0:
                    continue
                if s in px_seq and (ds >= px_seq[s].d[0] or s not in last_px):
                    px, c = px_seq[s].at(ds), price_ccy[s]
                elif d == today and s in manual:
                    px, c = manual[s], last_px.get(s, (0, config.BASE))[1]
                else:
                    px, c = last_px.get(s, (0, config.BASE))
                r = 1.0 if c == config.BASE else (fx_today.get(c) or 1.0)
                value += q * (px or 0) * r
            if has_flows:   # Wert inkl. Konto, Linie "Investiert" = eingezahltes Geld (netto)
                history.append([ds, round(value + (0.0 if -1.0 < cash_h < 0 else cash_h), 2), round(net_h, 2)])
            else:
                history.append([ds, round(value, 2), round(sum(cost.values()), 2)])
            d += timedelta(days=1)
        if history and holdings:  # letzter Punkt = aktuelle Bewertung
            history[-1][1] = round(total_value, 2)

    pos_value = total_value - (flows["cash"] if flows else 0.0)
    stale = [m["ticker"] for m in meta.values() if m.get("error")]
    last_ok = max([m["fetched_at"] for m in meta.values() if m.get("fetched_at") and not m.get("error")] or [0])
    return dict(
        base=config.BASE,
        title=get_setting("title", "Portfolio"),
        kind=kind, flow_txs=flow_txs,
        kpis=dict(value=total_value, cost=total_cost, gain=pos_value - total_cost,
                  gain_pct=((pos_value - total_cost) / total_cost * 100) if total_cost else None,
                  cash=flows["cash"] if flows else None,
                  flows=flows,
                  gain_total=(total_value - flows["net"]) if flows else None,
                  gain_total_pct=((total_value - flows["net"]) / flows["net"] * 100) if flows and flows["net"] > 0 else None,
                  realized=sum(h["realized"] for h in holdings),
                  dividends=sum(h["dividends"] for h in holdings), taxes=sum(h["taxes"] for h in holdings),
                  count=len(txs),
                  interest=sum(h["interest"] for h in holdings),
                  account_fees=sum(h["acct_fees"] for h in holdings),
                  trade_fees=sum(t["fee_base"] for t in txs if t["type"] in ("buy", "sell")),
                  fees=sum(t["fee_base"] for t in txs if t["type"] in ("buy", "sell"))
                  + sum(h["acct_fees"] for h in holdings)),
        holdings=holdings, history=history,
        sources=sorted({t["source"] for t in txs}),
        price_errors=stale, fx_missing=fx_missing, invalid_tx=len(invalid_tx),
        basis_missing=sorted(h["symbol"] for h in holdings if h.get("basis_missing")),
        last_refresh=last_ok or None,
    )
