"""Lokaler Betrieb (ohne Server): Pfade, Versionsstand und Updates über GitHub Releases.

Wird vom Startprogramm (launcher.py) und vom Server (server.py, nur bei FD_LOCAL=1) benutzt.
Nur Python-Standardbibliothek.

Aufbau im Datenordner (FD_DATA_DIR):
  finance.db                Datenbank (wie auf dem Server)
  local/state.json          {"current": "1.2.0" | null, "previous": …, "pending": …, "pending_action": …}
                            null bedeutet: die mitgelieferte Version im entpackten Download-Ordner
  local/versions/<version>/ heruntergeladene Versionen (Inhalt des Ordners app/)
  local/status.json         Stand für Einstellungen → Updates (gleiche Felder wie beim Update-Dienst)

Ablauf eines Updates: Der Server lädt das Release herunter, entpackt es nach versions/, trägt es als
"pending" ein und beendet sich mit RESTART_CODE. Das Startprogramm wechselt dann auf die neue Version,
prüft ob sie startet und kehrt sonst automatisch zur bisherigen zurück.
"""
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

RESTART_CODE = 75            # Server beendet sich, damit das Startprogramm neu startet (z.B. nach Update)
DEFAULT_PORT = 8765
REPO = os.environ.get("FD_UPDATE_REPO", "Kevegl/finance-dashboard")
API = os.environ.get("FD_UPDATE_API", "https://api.github.com").rstrip("/")
MAX_DOWNLOAD = 60 * 1024 * 1024
LOG_KEEP = 200
VERSION_RX = re.compile(r"^v?(\d+(?:\.\d+){1,3})$")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- Pfade

def default_data_dir():
    """Datenordner im Benutzerprofil, je nach Betriebssystem."""
    if os.environ.get("FD_DATA_DIR"):
        return Path(os.environ["FD_DATA_DIR"]).expanduser()
    home = Path.home()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(home / "AppData" / "Local")
        return Path(base) / "FinanzDashboard"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "FinanzDashboard"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "finanz-dashboard"


def local_dir(data_dir):
    return Path(data_dir) / "local"


def versions_dir(data_dir):
    return local_dir(data_dir) / "versions"


# ---------------------------------------------------------------- Versionen

def parse_version(v):
    m = VERSION_RX.match((v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def norm_version(v):
    p = parse_version(v)
    return ".".join(map(str, p)) if p else None


def app_version(app_dir):
    try:
        return norm_version(Path(app_dir, "VERSION").read_text(encoding="utf-8").strip())
    except OSError:
        return None


def valid_app_dir(path):
    return path is not None and Path(path, "server.py").is_file() and Path(path, "static", "index.html").is_file()


# ---------------------------------------------------------------- JSON-Dateien

def read_json(path, default=None):
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else (default if default is not None else {})
    except (OSError, ValueError):
        return default if default is not None else {}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_state(data_dir):
    return read_json(local_dir(data_dir) / "state.json")


def write_state(data_dir, state):
    write_json(local_dir(data_dir) / "state.json", state)


def read_status(data_dir):
    return read_json(local_dir(data_dir) / "status.json")


def save_status(data_dir, log=None, **fields):
    st = read_status(data_dir)
    st.update(fields)
    st["updated_at"] = now()
    if log is not None:
        st["log"] = log[-LOG_KEEP:]
    write_json(local_dir(data_dir) / "status.json", st)
    return st


def version_info(version, date=None, subject=None):
    """Darstellung einer Version im Format, das die Update-Ansicht erwartet."""
    if not version:
        return None
    return {"commit": version, "short": "v" + version, "date": date,
            "subject": subject or f"Version {version}"}


# ---------------------------------------------------------------- HTTPS (mit Windows-Zertifikatsergänzung)

CERT_HINT = ("Das Zertifikat konnte nicht geprüft werden (Windows-Zertifikatsspeicher unvollständig). "
             "Bitte Windows Update ausführen oder die Seite einmal in Edge öffnen.")
_WARMED = set()


def is_cert_error(e):
    reason = getattr(e, "reason", e)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)


def net_error_text(e):
    return CERT_HINT if sys.platform.startswith("win") and is_cert_error(e) else str(e)


def warm_windows_certs(url):
    """Windows lädt fehlende Stammzertifikate erst nach, wenn ein Windows-Programm die Seite aufruft – Python
    prüft nur gegen bereits vorhandene. Ein unsichtbarer Aufruf über PowerShell ergänzt sie (Prüfung bleibt aktiv)."""
    env = dict(os.environ, FD_WARM_URL=url)   # Adresse als Variable, nie als Teil des Befehls
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                        "try { Invoke-WebRequest -UseBasicParsing -Method Head -TimeoutSec 20 "
                        "-Uri $env:FD_WARM_URL | Out-Null } catch { }"],
                       env=env, timeout=45, capture_output=True, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def urlopen(req, timeout=30):
    """urllib.request.urlopen; unter Windows bei fehlendem Stammzertifikat einmal pro Adresse ergänzen und wiederholen."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
    except urllib.error.URLError as e:
        if not (sys.platform.startswith("win") and is_cert_error(e)):
            raise
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        host = urllib.parse.urlsplit(url).hostname or ""
        if host in _WARMED:
            raise
        _WARMED.add(host)
        print(f"Zertifikat für {host} fehlt im Windows-Speicher – lasse Windows es ergänzen …", flush=True)
        warm_windows_certs(url)
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310


# ---------------------------------------------------------------- GitHub Releases

def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "finance-dashboard-updater",
                                               "Accept": "application/vnd.github+json"})
    return urlopen(req, timeout=timeout)


def fetch_releases():
    """Veröffentlichte Releases (ohne Entwürfe und Vorabversionen), neueste zuerst."""
    try:
        with _get(f"{API}/repos/{REPO}/releases?per_page=30") as r:
            data = json.loads(r.read(5 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("Auf GitHub wurden keine Versionen gefunden.") from None
        if e.code in (403, 429):
            raise RuntimeError("GitHub hat die Anfrage begrenzt – bitte später erneut versuchen.") from None
        raise RuntimeError(f"GitHub antwortet mit Fehler {e.code}.") from None
    except (OSError, ValueError) as e:
        raise RuntimeError(f"GitHub nicht erreichbar: {net_error_text(e)}") from None
    out = []
    for rel in data if isinstance(data, list) else []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        ver = norm_version(rel.get("tag_name"))
        if not ver:
            continue
        out.append({"version": ver, "tag": rel["tag_name"], "name": (rel.get("name") or "").strip()[:200],
                    "date": rel.get("published_at"), "notes": (rel.get("body") or "")[:4000],
                    "zip": f"{API}/repos/{REPO}/zipball/{rel['tag_name']}"})
    out.sort(key=lambda r: parse_version(r["version"]), reverse=True)
    return out


def download_release(rel, data_dir, log):
    """Lädt das Release herunter und entpackt dessen Ordner app/ nach versions/<version>/."""
    target = versions_dir(data_dir) / rel["version"]
    if valid_app_dir(target) and app_version(target) == rel["version"]:
        log(f"Version {rel['version']} liegt bereits vor.")
        return target
    versions_dir(data_dir).mkdir(parents=True, exist_ok=True)
    log(f"Lade {rel['tag']} von GitHub …")
    with tempfile.TemporaryDirectory(dir=versions_dir(data_dir), prefix=".dl-") as tmp:
        zpath = Path(tmp) / "release.zip"
        try:
            with _get(rel["zip"], timeout=120) as r, open(zpath, "wb") as f:
                size = 0
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise RuntimeError("Download zu gross.")
                    f.write(chunk)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Download fehlgeschlagen (Fehler {e.code}).") from None
        except OSError as e:
            raise RuntimeError(f"Download fehlgeschlagen: {net_error_text(e)}") from None
        log(f"{size / 1024:.0f} KB geladen, entpacke …")
        out = Path(tmp) / "app"
        try:
            with zipfile.ZipFile(zpath) as z:
                n = 0
                for info in z.infolist():
                    parts = info.filename.split("/")
                    # GitHub-Archiv: <repo>-<commit>/app/…  – nur dieser Teil wird übernommen
                    if len(parts) < 3 or parts[1] != "app" or info.is_dir():
                        continue
                    rel_parts = parts[2:]
                    if any(p in ("", ".", "..") or "\\" in p or ":" in p for p in rel_parts):
                        raise RuntimeError("Ungültiger Pfad im Archiv.")
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:   # symbolische Links auslassen
                        continue
                    dest = out.joinpath(*rel_parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    n += 1
        except zipfile.BadZipFile:
            raise RuntimeError("Das heruntergeladene Archiv ist beschädigt.") from None
        if not valid_app_dir(out):
            raise RuntimeError("Das Archiv enthält kein gültiges Dashboard.")
        found = app_version(out)
        if found and found != rel["version"]:
            log(f"Hinweis: VERSION-Datei meldet {found}, Release heisst {rel['version']}.")
        Path(out, "VERSION").write_text(rel["version"] + "\n", encoding="utf-8")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        os.replace(out, target)
        log(f"{n} Dateien entpackt.")
    return target


def cleanup_versions(data_dir, keep):
    """Entfernt heruntergeladene Versionen, die weder aktuell noch vorherige sind."""
    vd = versions_dir(data_dir)
    if not vd.is_dir():
        return
    for p in vd.iterdir():
        if p.name.startswith(".dl-") and time.time() - p.stat().st_mtime > 3600:
            shutil.rmtree(p, ignore_errors=True)
        elif not p.name.startswith(".") and p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------- Aktionen (laufen im Server)

class LocalUpdater:
    """Prüfen, Herunterladen und Zurücksetzen im lokalen Betrieb. Der eigentliche Wechsel der
    Version passiert im Startprogramm, nachdem sich der Server mit RESTART_CODE beendet hat."""

    def __init__(self, data_dir, running_version, bundled_version):
        self.data_dir = Path(data_dir)
        self.running = running_version
        self.bundled = bundled_version
        self.log_lines = []

    def log(self, msg):
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        self.log_lines.append(line)
        print(line, flush=True)

    def previous_info(self):
        prev = read_state(self.data_dir).get("previous")
        if prev == "bundled":
            prev = self.bundled
        return version_info(prev) if prev and prev != self.running else None

    def check(self):
        self.log(f"Prüfe github.com/{REPO} auf neue Versionen …")
        cur = parse_version(self.running) or (0,)
        rels = fetch_releases()
        newer = [r for r in rels if parse_version(r["version"]) > cur]
        own = next((r for r in rels if r["version"] == self.running), None)
        info = {"mode": "release", "branch": "Releases", "behind": len(newer), "ahead": 0, "dirty": False,
                "current": version_info(self.running, own and own["date"], own and own["name"]),
                "remote": version_info(newer[0]["version"], newer[0]["date"], newer[0]["name"]) if newer else None,
                "commits": [{"short": r["tag"], "date": r["date"], "subject": r["name"] or f"Version {r['version']}"}
                            for r in newer[:20]],
                "notes": newer[0]["notes"] if newer else "",
                "previous": self.previous_info(), "last_check": now()}
        self.log(f"Installiert: v{self.running} · {len(newer)} neue Version(en) verfügbar")
        return info, newer

    def run(self, action, request_id):
        """Gibt True zurück, wenn der Server sich für einen Versionswechsel beenden soll."""
        self.log_lines = []
        busy = {"check": "checking", "update": "updating", "rollback": "rolling_back"}[action]
        save_status(self.data_dir, log=[], state=busy, action=action, request_id=request_id, message="",
                    started_at=now())
        try:
            if action == "check":
                info, newer = self.check()
                msg = "Neue Version verfügbar." if newer else "Das Dashboard ist auf dem neuesten Stand."
                save_status(self.data_dir, log=self.log_lines, state="ok", message=msg, finished_at=now(),
                            error=None, **info)
                return False
            if action == "update":
                info, newer = self.check()
                if not newer:
                    save_status(self.data_dir, log=self.log_lines, state="ok", finished_at=now(), error=None,
                                message="Das Dashboard ist bereits auf dem neuesten Stand.", **info)
                    return False
                save_status(self.data_dir, log=self.log_lines, **info)
                download_release(newer[0], self.data_dir, self.log)
                target = newer[0]["version"]
            else:
                prev = read_state(self.data_dir).get("previous")
                if not prev:
                    raise RuntimeError("Es ist keine vorherige Version gespeichert.")
                if prev != "bundled" and not valid_app_dir(versions_dir(self.data_dir) / prev):
                    raise RuntimeError("Die gespeicherte vorherige Version ist nicht mehr vorhanden.")
                target = prev
            st = read_state(self.data_dir)
            st.update(pending=target, pending_action=action)
            write_state(self.data_dir, st)
            self.log("Starte das Dashboard mit der neuen Version neu …")
            save_status(self.data_dir, log=self.log_lines)
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"✗ {e}")
            save_status(self.data_dir, log=self.log_lines, state="error", message=str(e), error=str(e),
                        finished_at=now())
            return False
