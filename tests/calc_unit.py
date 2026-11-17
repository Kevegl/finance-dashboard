#!/usr/bin/env python3
"""Unit-Tests für Zahlen, Daten, Berechnung und Login-Sperre (nur Standardbibliothek, ohne Internet).

Aufruf:  python3 tests/calc_unit.py
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="fd-unit-")
os.environ["FD_DATA_DIR"] = TMP
os.environ.pop("FD_LOCAL", None)
os.environ.pop("FD_DOCKER", None)
sys.path.insert(0, str(ROOT / "app"))

import config     # noqa: E402
import importers  # noqa: E402
import portfolio  # noqa: E402
import prices     # noqa: E402
import server     # noqa: E402
import store      # noqa: E402
import validate   # noqa: E402
import auth       # noqa: E402

store.init_db()


def add(pid, date, typ, asset, qty=0.0, price=0.0, total=None, ccy="CHF"):
    total = qty * price if total is None else total
    with store.db_tx() as con:
        store.ensure_asset(con, {"asset": asset, "asset_class": "etf"})
        con.execute("""INSERT INTO transactions (portfolio_id,date,type,asset,quantity,price,currency,fee,total,source,hash)
                       VALUES (?,?,?,?,?,?,?,0,?,'Test',?)""",
                    (pid, date, typ, asset, qty, price, ccy, total, f"{date}{typ}{asset}{qty}{total}"))


def new_portfolio(name):
    with store.db_tx() as con:
        return con.execute("INSERT INTO portfolios(name, sort, kind) VALUES (?, 9, 'standard')", (name,)).lastrowid


def holding(summary, sym):
    return next(h for h in summary["holdings"] if h["symbol"] == sym)


class ParseNum(unittest.TestCase):
    def test_formats(self):
        P = importers.parse_num
        self.assertEqual(P("1'234.56"), 1234.56)
        self.assertEqual(P("1.234,56"), 1234.56)
        self.assertEqual(P("1,234.56"), 1234.56)
        self.assertEqual(P("1,234"), 1234.0)
        self.assertEqual(P("12,5"), 12.5)
        self.assertEqual(P("0,125"), 0.125)
        self.assertEqual(P("(12.5)"), -12.5)
        self.assertEqual(P("CHF -50.00"), -50.0)
        self.assertIsNone(P(""))
        self.assertIsNone(P("N/A"))

    def test_not_finite(self):
        self.assertIsNone(importers.parse_num("1e999"))
        self.assertIsNone(importers.parse_num("-1e999"))


class Dates(unittest.TestCase):
    def test_iso_must_exist(self):
        p = importers.date_parser_for([])
        self.assertIsNone(p("2024-13-45"))
        self.assertIsNone(p("2023-02-29"))
        self.assertEqual(p("2024-02-29"), "2024-02-29")
        self.assertEqual(p("2024-02-29T10:00:00+01:00"), "2024-02-29")

    def test_slash_day_first_detection(self):
        p = importers.date_parser_for(["13/02/2024", "01/02/2024"])
        self.assertEqual(p("01/02/2024"), "2024-02-01")
        p = importers.date_parser_for(["02/13/2024", "01/02/2024"])
        self.assertEqual(p("01/02/2024"), "2024-01-02")

    def test_swiss(self):
        self.assertEqual(importers.date_parser_for([])("17.11.2026"), "2026-11-17")

    def test_server_validation(self):
        self.assertTrue(validate.is_iso_date("2026-11-17"))
        for bad in ("17.11.2026", "2024-13-45", "", None, 20261117, "2026-11-17<x>"):
            self.assertFalse(validate.is_iso_date(bad), bad)


class NormalizeTx(unittest.TestCase):
    def base(self, **kw):
        b = {"date": "2026-01-02", "type": "buy", "asset": " vwrl ", "quantity": "2", "price": "50", "fee": "1"}
        b.update(kw)
        return b

    def test_buy_total_includes_fee(self):
        t = validate.normalize_tx(self.base(), "CHF")
        self.assertEqual((t["asset"], t["total"], t["currency"]), ("VWRL", 101.0, "CHF"))

    def test_signs(self):
        self.assertEqual(validate.normalize_tx(self.base(type="fee", total="5"), "CHF")["total"], -5.0)
        self.assertEqual(validate.normalize_tx(self.base(type="deposit", total="-5"), "CHF")["total"], 5.0)

    def test_rejects(self):
        for kw in ({"date": "17.11.2026"}, {"date": "2024-13-45"}, {"date": 5}, {"price": "1e999"},
                   {"quantity": "nan"}, {"quantity": "abc"}, {"type": "gift"}, {"asset": " "}, {"currency": "EURO"},
                   {"quantity": "1e200", "price": "1e200"}):
            with self.assertRaises(ValueError, msg=kw):
                validate.normalize_tx(self.base(**kw), "CHF")

    def test_import_problem(self):
        self.assertIsNone(validate.import_problem({"date": "2026-01-01", "quantity": 1, "price": 1, "fee": 0, "total": 1}))
        self.assertTrue(validate.import_problem({"date": "2026-01-01", "quantity": float("inf"), "price": 1, "fee": 0, "total": 1}))
        self.assertTrue(validate.import_problem({"date": "2024-13-45", "quantity": 1, "price": 1, "fee": 0, "total": 1}))


class Summary(unittest.TestCase):
    def test_average_cost_and_partial_sell(self):
        pid = new_portfolio("Durchschnitt")
        add(pid, "2024-01-02", "buy", "AAA", 10, 100)    # 1000
        add(pid, "2024-02-02", "buy", "AAA", 10, 200)    # 2000 -> Ø 150
        add(pid, "2024-03-02", "sell", "AAA", 5, 300)    # Erlös 1500, Kosten 750
        s = portfolio.compute_summary(pid)
        h = holding(s, "AAA")
        self.assertAlmostEqual(h["quantity"], 15)
        self.assertAlmostEqual(h["cost"], 2250)
        self.assertAlmostEqual(h["avg_price"], 150)
        self.assertAlmostEqual(h["realized"], 750)
        self.assertFalse(h["basis_missing"])
        self.assertEqual(s["basis_missing"], [])

    def test_rounding_rest_closes_position(self):
        pid = new_portfolio("Rundung")
        add(pid, "2024-01-02", "buy", "BBB", 3.0004, 10)
        add(pid, "2024-02-02", "sell", "BBB", 3.0, 12)
        h = holding(portfolio.compute_summary(pid), "BBB")
        self.assertEqual(h["quantity"], 0.0)
        self.assertEqual(h["cost"], 0.0)

    def test_sell_without_purchase_keeps_proceeds(self):
        pid = new_portfolio("Ohne Kauf")
        add(pid, "2024-03-02", "sell", "CCC", 5, 40)
        s = portfolio.compute_summary(pid)
        self.assertAlmostEqual(holding(s, "CCC")["realized"], 200)
        self.assertAlmostEqual(s["kpis"]["realized"], 200)
        self.assertEqual(s["basis_missing"], ["CCC"])

    def test_invalid_rows_do_not_break_summary(self):
        pid = new_portfolio("Altbestand")
        add(pid, "2024-01-02", "buy", "DDD", 1, 10)
        add(pid, "17.11.2026", "buy", "DDD", 1, 10)       # wie früher über die API ungeprüft gespeichert
        add(pid, "2024-01-03", "buy", "DDD", 1, float("inf"))
        s = portfolio.compute_summary(pid)
        self.assertEqual(s["invalid_tx"], 2)
        self.assertAlmostEqual(holding(s, "DDD")["quantity"], 1)
        json.dumps(s, allow_nan=False, default=list)       # gültiges JSON für den Browser
        with store.db_tx() as con:
            prices.needed_tickers(con)                       # darf nicht mehr abbrechen


class SetupCode(unittest.TestCase):
    """Ersteinrichtung nur mit Einrichtungscode, und nur einmal – auch bei gleichzeitigen Anfragen."""

    def setUp(self):
        store.set_setting("auth_hash", None)
        store.set_setting("auth_user", None)
        auth.setup_code_clear()
        server.FAILED.clear()
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.FAILED.clear()

    def call(self, path, body=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
        except OSError:
            return None, None

    def test_code_required_and_single_use(self):
        st = self.call("/api/auth/status")[1]
        self.assertTrue(st["setup_required"] and st["setup_code"])
        code = auth.SETUP_CODE_FILE.read_text(encoding="ascii").strip()
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        pw = {"username": "neu", "password": "langespasswort"}
        self.assertEqual(self.call("/api/setup", pw)[0], 403)
        self.assertEqual(self.call("/api/setup", dict(pw, setup_code="AAAA-AAAA"))[0], 403)
        self.assertEqual(self.call("/api/setup", dict(pw, password="kurz", setup_code=code))[0], 400)
        codes = []
        threads = [threading.Thread(target=lambda: codes.append(self.call("/api/setup", dict(pw, setup_code=code.lower()))[0]))
                   for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(codes.count(200), 1, codes)
        self.assertTrue(all(c in (200, 409, None) for c in codes), codes)
        self.assertFalse(auth.SETUP_CODE_FILE.exists())
        self.assertFalse(self.call("/api/auth/status")[1]["setup_code"])

    def test_wrong_codes_lock_out(self):
        auth.setup_code()
        for _ in range(server.MAX_FAILS):
            self.assertEqual(self.call("/api/setup", {"password": "langespasswort", "setup_code": "AAAA-AAAA"})[0], 403)
        right = auth.SETUP_CODE_FILE.read_text(encoding="ascii").strip()
        self.assertEqual(self.call("/api/setup", {"password": "langespasswort", "setup_code": right})[0], 429)

    def test_local_mode_needs_no_code(self):
        old = auth.LOCAL
        try:
            auth.LOCAL = True
            self.assertFalse(auth.setup_code_required())
        finally:
            auth.LOCAL = old


class Restore(unittest.TestCase):
    def test_base_currency_applied_immediately(self):
        old = config.BASE
        try:
            bk = store.make_backup()
            bk["settings"]["base_ccy"] = "EUR"
            bk["transactions"].append(["kaputt"])            # falsche Zeilenlänge wird übersprungen
            store.restore_backup(bk)
            self.assertEqual(config.BASE, "EUR")
            with self.assertRaises(ValueError):
                store.restore_backup(dict(bk, portfolios_columns="id,name"))
        finally:
            store.set_setting("base_ccy", old)
            config.BASE = old


class LoginLock(unittest.TestCase):
    """Parallele Fehlversuche dürfen die Grenze von 5 Versuchen nicht überschreiten."""

    @classmethod
    def setUpClass(cls):
        store.set_setting("auth_user", "test")
        store.set_setting("auth_hash", auth.hash_pw("richtig123", iterations=20_000))
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        server.FAILED.clear()

    def login(self, pw, path="/api/login"):
        body = json.dumps({"username": "test", "password": pw, "code": "AAAAA-AAAAA-AAAAA-AAAAA-AAAAA"}).encode()
        req = urllib.request.Request(self.url + path, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except OSError:            # Verbindung abgewiesen (Warteschlange des Testservers voll)
            return None

    def test_parallel_failures_are_capped(self):
        codes = []
        threads = [threading.Thread(target=lambda: codes.append(self.login("falsch999"))) for _ in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(codes.count(401), server.MAX_FAILS, codes)
        self.assertTrue(all(c in (401, 429, None) for c in codes), codes)
        self.assertGreater(codes.count(429), 0, codes)
        self.assertEqual(self.login("richtig123"), 429)       # Sperre gilt auch für das richtige Passwort

    def test_success_does_not_count(self):
        for _ in range(server.MAX_FAILS + 2):
            self.assertEqual(self.login("richtig123"), 200)

    def test_recovery_parallel_capped(self):
        codes = []
        threads = [threading.Thread(target=lambda: codes.append(self.login("x", "/api/recovery"))) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(codes.count(401), server.MAX_FAILS, codes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
