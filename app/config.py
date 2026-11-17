"""Einstellungen aus Umgebungsvariablen und Konstanten. config.BASE ist die aktuelle Basiswährung
(beim Start aus der Datenbank gelesen, in den Einstellungen änderbar) – immer als config.BASE lesen."""
import os
from pathlib import Path

import local_update

TX_TYPES = ("buy", "sell", "dividend", "tax", "interest", "fee", "deposit", "withdrawal", "transfer")
FLOW_TYPES = ("deposit", "withdrawal", "transfer")
PF_KINDS = ("standard", "3a")

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
DATA_DIR = Path(os.environ.get("FD_DATA_DIR", HERE.parent / "data"))
DB_PATH = DATA_DIR / "finance.db"
BASE = os.environ.get("FD_BASE_CCY", "CHF").upper()   # Startwert; in den Einstellungen änderbar
CURRENCIES = ["CHF", "EUR", "USD", "GBP", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN", "CZK", "HKD", "SGD"]
SESSION_DAYS = 30
COOKIE = "fd_session"
MIN_PW = 8
# Lokaler Betrieb (Startprogramm launcher.py): nur auf diesem Computer, Updates über GitHub Releases
LOCAL = os.environ.get("FD_LOCAL") == "1"
DOCKER = not LOCAL and os.environ.get("FD_DOCKER") == "1"
APP_VERSION = local_update.norm_version(os.environ.get("FD_APP_VERSION", "")) or local_update.app_version(HERE)
BUNDLED_VERSION = local_update.norm_version(os.environ.get("FD_BUNDLED_VERSION", ""))
TLS_CERT = None if LOCAL else os.environ.get("FD_CERT")
TLS_KEY = None if LOCAL else os.environ.get("FD_KEY")
PRICE_TTL = int(os.environ.get("FD_PRICE_TTL", "14400"))
# Updates über das Dashboard: der Server schreibt nur eine Auftragsdatei, ausgeführt wird sie vom
# root-Dienst finance-dashboard-update (systemd .path-Unit). Status liest er aus dessen Verzeichnis.
REPO_DIR = os.environ.get("FD_REPO_DIR", "")
UPDATE_STATUS = Path(os.environ.get("FD_UPDATE_STATUS", "/var/lib/finance-dashboard-update/status.json"))
UPDATE_ACTIONS = ("check", "update", "rollback")
MAX_BODY = 100 * 1024 * 1024


# --- News (Google News RSS, serverseitig abgerufen und zwischengespeichert)
LANGS = ("de", "en")
