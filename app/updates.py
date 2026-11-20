"""Updates über das Dashboard: Auftragsdatei für den root-Dienst bzw. lokaler Updater (Programm/Docker)."""
import json
import os
import secrets
import threading
import time
from datetime import datetime

import local_update
from config import APP_VERSION, BUNDLED_VERSION, DATA_DIR, DOCKER, LOCAL, REPO_DIR, UPDATE_ACTIONS, UPDATE_STATUS


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

LOCAL_UPD = {"thread": None}
HTTPD = {"server": None, "exit": 0}


def request_exit(code):
    """Beendet den Server nach der laufenden Antwort (nur lokaler Betrieb)."""
    HTTPD["exit"] = code
    if HTTPD["server"]:
        threading.Timer(0.5, HTTPD["server"].shutdown).start()


def local_update_busy():
    t = LOCAL_UPD["thread"]
    return bool(t and t.is_alive())


def local_update_status():
    st = local_update.read_status(DATA_DIR)
    busy = st.get("state") in ("checking", "updating", "rolling_back")
    if busy and not local_update_busy():
        # Hier läuft kein Vorgang (mehr). Direkt nach einem Versionswechsel trägt das Startprogramm das Ergebnis erst
        # ein, wenn die neue Version antwortet – so lange gilt der Lauf als nicht unterbrochen (Frist 2 Minuten).
        try:
            age = time.time() - datetime.fromisoformat(st.get("updated_at") or "").timestamp()
        except ValueError:
            age = 1e9
        if age > 120:
            busy = False
            st.update(state="error", message="Der letzte Lauf wurde unterbrochen.")
    cur = st.get("current") or {}
    if cur.get("commit") != APP_VERSION:
        st["current"] = local_update.version_info(APP_VERSION)
    mine = local_update.parse_version(APP_VERSION) or (0,)
    st["commits"] = [c for c in st.get("commits") or [] if (local_update.parse_version(c.get("short")) or (0,)) > mine]
    st["behind"] = len(st["commits"])
    prev = (st.get("previous") or {}).get("commit")
    if prev and prev == APP_VERSION:
        st["previous"] = None
    st.update(available=True, mode="docker" if DOCKER else "release", branch="Docker" if DOCKER else "Releases",
              pending=False, busy=busy, ahead=0, dirty=False)
    if DOCKER:
        st["previous"] = None
    return st


def local_request_update(action):
    if local_update_busy():
        raise ValueError("Es läuft bereits ein Update-Vorgang.")
    rid = secrets.token_hex(8)

    def work():
        u = local_update.LocalUpdater(DATA_DIR, APP_VERSION, BUNDLED_VERSION)
        if u.run(action, rid):
            request_exit(local_update.RESTART_CODE)

    LOCAL_UPD["thread"] = threading.Thread(target=work, daemon=True)
    LOCAL_UPD["thread"].start()
    return rid


def local_update_checker():
    """Prüft im lokalen Betrieb einmal täglich, ob es ein neues Release gibt (installiert nichts)."""
    time.sleep(30)
    while True:
        try:
            last = local_update.read_status(DATA_DIR).get("last_check")
            age = time.time() - datetime.fromisoformat(last).timestamp() if last else 1e9
            if age > 20 * 3600 and not local_update_busy():
                local_request_update("check")
        except Exception as e:  # noqa: BLE001
            print(f"Update-Prüfung: {e}", flush=True)
        time.sleep(3600)


def update_status():
    """Stand des Update-Dienstes für die Einstellungen (ohne Log, das gibt es separat)."""
    if LOCAL or DOCKER:
        return local_update_status()
    if not REPO_DIR:
        return {"available": False}
    try:
        st = json.loads(UPDATE_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st["available"] = True
    if isinstance(st.get("current"), dict) and not st["current"].get("version"):
        st["current"]["version"] = APP_VERSION      # Status eines älteren Update-Dienstes ohne Versionsnummer
    st["pending"] = (DATA_DIR / "update-request").exists()
    st["busy"] = st.get("state") in ("checking", "updating", "rolling_back")
    if st.get("updated_at") and st["busy"]:
        # Ein Lauf, der seit über 40 Minuten "läuft", ist abgebrochen (z.B. Container-Neustart)
        try:
            age = time.time() - datetime.fromisoformat(st["updated_at"]).timestamp()
            if age > 2400:
                st["busy"] = False
                st["state"] = "error"
                st["message"] = "Der letzte Lauf wurde unterbrochen."
        except ValueError:
            pass
    return st


def request_update(action):
    if action not in UPDATE_ACTIONS:
        raise ValueError("Unbekannte Aktion")
    if DOCKER and action != "check":
        raise ValueError("Im Docker-Betrieb wird über ein neues Image aktualisiert (docker compose pull).")
    if LOCAL or DOCKER:
        return local_request_update(action)
    if not REPO_DIR:
        raise ValueError("Updates über das Dashboard sind bei dieser Installation nicht verfügbar.")
    rid = secrets.token_hex(8)
    tmp = DATA_DIR / f".update-request-{rid}"
    tmp.write_text(f"{action} {rid}\n", encoding="ascii")
    os.replace(tmp, DATA_DIR / "update-request")
    return rid
