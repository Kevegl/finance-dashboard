#!/usr/bin/env python3
"""Startprogramm für den lokalen Betrieb (Windows, macOS, Linux – ohne Server, ohne Installation).

  python3 app/launcher.py               startet das Dashboard und öffnet den Browser
  python3 app/launcher.py --no-browser  ohne Browser zu öffnen

Normalerweise über start.bat (Windows), start.command (macOS) oder start.sh (Linux) aufgerufen.

Das Startprogramm
  * legt den Datenordner im Benutzerprofil an (FD_DATA_DIR überschreibt ihn),
  * startet den Server nur auf 127.0.0.1 (nicht im Netzwerk erreichbar), Port 8765 (FD_PORT),
  * öffnet nur den Browser, wenn das Dashboard schon läuft (keine doppelte Instanz),
  * wechselt nach einem Update auf die neue Version, prüft ob sie startet und kehrt sonst
    automatisch zur bisherigen zurück,
  * beendet sich, wenn das Dashboard über Einstellungen → «Dashboard beenden» geschlossen wird.
"""
import os
import sys

if sys.version_info < (3, 10):
    print("Das Finanz-Dashboard braucht Python 3.10 oder neuer – installiert ist "
          f"{sys.version.split()[0]}. Download: https://www.python.org/downloads/")
    print("The finance dashboard needs Python 3.10 or newer.")
    sys.exit(1)

import json
import runpy
import signal
import socket
import subprocess
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH_WAIT = 45
FROZEN = getattr(sys, "frozen", False)     # .exe (Windows) bzw. .app (macOS), gebaut mit PyInstaller


def serve(app_dir):
    """Kindprozess-Modus der .exe/App: startet server.py aus app_dir mit dem eingebauten Python."""
    if FROZEN:
        # Module zuerst aus app_dir laden (aktualisierte Version), erst danach aus dem Paket
        import importlib.machinery
        sys.meta_path.insert(0, importlib.machinery.PathFinder)
        if sys.stdout is None and os.environ.get("FD_DATA_DIR"):   # ohne Konsole: ins Protokoll schreiben
            try:
                f = open(Path(os.environ["FD_DATA_DIR"], "local", "server.log"), "a", encoding="utf-8", buffering=1)  # noqa: SIM115
                sys.stdout = sys.stderr = f
            except OSError:
                pass
    sys.path.insert(0, str(app_dir))
    sys.argv = [str(Path(app_dir, "server.py"))]
    runpy.run_path(str(Path(app_dir, "server.py")), run_name="__main__")


if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
    serve(sys.argv[2])
    sys.exit(0)

import local_update as lu  # noqa: E402  (liegt neben dieser Datei)

# mitgelieferte Version: entpackter Download bzw. der Ordner app/ im Paket der .exe/App
BUNDLE = Path(os.environ.get("FD_BUNDLE_APP") or (Path(getattr(sys, "_MEIPASS", HERE)) / "app" if FROZEN else HERE))


# Ausgabe umgeleitet (z.B. Windows ohne Konsole, Tests): immer UTF-8, damit «→» und Umlaute nicht abstürzen
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None and not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass


def say(de, en=None):
    print(de if not en else f"{de}\n  {en}", flush=True)


def fail(de, en):
    """Fehlermeldung – bei der .exe/App zusätzlich als Fenster, weil es dort keine Konsole gibt."""
    say("✗ " + de, en)
    if not FROZEN:
        return
    text = f"{de}\n\n{en}"
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "Finanz-Dashboard", 0x10)
        elif sys.platform == "darwin":
            esc = text.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display alert "Finanz-Dashboard" message "{esc}" as critical'],
                           timeout=300, check=False)
    except Exception:  # noqa: BLE001
        pass


class Launcher:
    def __init__(self):
        self.data = lu.default_data_dir()
        self.port = int(os.environ.get("FD_PORT") or lu.DEFAULT_PORT)
        self.url = f"http://127.0.0.1:{self.port}/"
        self.bundled = lu.app_version(BUNDLE) or "0.0.0"
        self.child = None

    # ---------- Version wählen
    def active(self):
        """(App-Ordner, Version), die gestartet werden soll."""
        st = lu.read_state(self.data)
        cur = st.get("current")
        cur_dir = lu.versions_dir(self.data) / cur if cur else None
        if cur and lu.valid_app_dir(cur_dir) and (lu.parse_version(cur) or ()) >= (lu.parse_version(self.bundled) or ()):
            return cur_dir, cur
        if cur:
            # Neuerer Download von Hand entpackt (oder Version fehlt) -> mitgelieferte Version verwenden
            st.update(current=None, previous=None)
            lu.write_state(self.data, st)
        return BUNDLE, self.bundled

    def delegate(self):
        """Startet das Startprogramm der aktiven Version, damit auch dieses aktualisiert wird."""
        if os.environ.get("FD_LAUNCHER_DELEGATED") or getattr(sys, "frozen", False):
            return False
        app_dir, _ = self.active()
        target = Path(app_dir, "launcher.py")
        if app_dir.resolve() == HERE or not target.is_file():
            return False
        try:
            compile(target.read_text(encoding="utf-8"), str(target), "exec")
        except (OSError, SyntaxError, ValueError):
            return False
        os.environ["FD_LAUNCHER_DELEGATED"] = "1"
        os.environ["FD_BUNDLE_APP"] = str(BUNDLE)
        sys.path[0] = str(app_dir)
        sys.modules.pop("local_update", None)
        sys.argv[0] = str(target)
        runpy.run_path(str(target), run_name="__main__")
        return True

    # ---------- Server-Prozess
    def health(self, timeout=2):
        try:
            with urllib.request.urlopen(self.url + "api/health", timeout=timeout) as r:  # noqa: S310
                return r.status == 200 and json.loads(r.read(4096).decode()).get("ok") is True
        except (OSError, ValueError):
            return False

    def port_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name == "posix":
                # wie der Server selbst: Verbindungen im TIME_WAIT nach einem Neustart blockieren nicht
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", self.port))
                return True
            except OSError:
                return False

    def start(self, app_dir, version):
        env = {k: v for k, v in os.environ.items()
               if k not in ("FD_CERT", "FD_KEY", "FD_REPO_DIR", "FD_LAUNCHER_DELEGATED", "FD_BUNDLE_APP")}
        env.update(FD_LOCAL="1", FD_HOST="127.0.0.1", FD_PORT=str(self.port), FD_DATA_DIR=str(self.data),
                   FD_APP_VERSION=version, FD_BUNDLED_VERSION=self.bundled, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8:replace")
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--serve", str(app_dir)]
        else:
            cmd = [sys.executable, str(Path(app_dir, "server.py"))]
        self.child = subprocess.Popen(cmd, cwd=str(app_dir), env=env)
        return self.child

    def wait_healthy(self):
        deadline = time.time() + HEALTH_WAIT
        while time.time() < deadline:
            if self.child.poll() is not None:
                return False
            if self.health(timeout=2):
                return True
            time.sleep(0.5)
        return False

    def stop_child(self):
        c, self.child = self.child, None
        if c and c.poll() is None:
            c.terminate()
            try:
                c.wait(10)
            except subprocess.TimeoutExpired:
                c.kill()
                c.wait()

    # ---------- Versionswechsel nach Update/Rollback
    def switch(self):
        """Führt einen vom Server vorgemerkten Wechsel aus. Gibt True zurück, wenn der Server danach läuft."""
        st = lu.read_state(self.data)
        target, action = st.get("pending"), st.get("pending_action") or "update"
        if not target:
            return False
        status = lu.read_status(self.data)
        log = list(status.get("log") or [])

        def note(msg):
            log.append(f"{time.strftime('%H:%M:%S')} {msg}")
            print(msg, flush=True)

        old_cur, old_prev = st.get("current"), st.get("previous")
        new_cur = None if target == "bundled" else target
        st.pop("pending", None)
        st.pop("pending_action", None)
        if new_cur and not lu.valid_app_dir(lu.versions_dir(self.data) / new_cur):
            lu.write_state(self.data, st)
            note("✗ Die neue Version ist unvollständig – Wechsel abgebrochen.")
            lu.save_status(self.data, log=log, state="error", finished_at=lu.now(),
                           message="Die neue Version ist unvollständig.", error="Die neue Version ist unvollständig.")
            return False
        st["current"] = new_cur
        st["previous"] = (old_cur or "bundled") if action == "update" else None
        lu.write_state(self.data, st)
        app_dir, version = self.active()
        known = {c.get("short"): c for c in status.get("commits") or []}

        def info(v):
            c = known.get("v" + v) or known.get(v) or {}
            return lu.version_info(v, c.get("date"), c.get("subject"))
        note(f"Starte Version {version} …")
        self.start(app_dir, version)
        if self.wait_healthy():
            prev = st.get("previous")
            prev_v = self.bundled if prev == "bundled" else prev
            note(f"✓ Version {version} läuft.")
            done = "Update installiert." if action == "update" else "Vorherige Version wiederhergestellt."
            lu.save_status(self.data, log=log, state="ok", message=done, finished_at=lu.now(), error=None,
                           current=info(version), previous=info(prev_v) if prev_v else None,
                           behind=0, commits=[], remote=None, last_update=lu.now())
            lu.cleanup_versions(self.data, {v for v in (st.get("current"), st.get("previous")) if v})
            return True
        self.stop_child()
        note(f"✗ Version {version} startet nicht – stelle die bisherige Version wieder her …")
        st.update(current=old_cur, previous=old_prev)
        lu.write_state(self.data, st)
        msg = "Die neue Version startet nicht. Die vorherige Version wurde wiederhergestellt."
        lu.save_status(self.data, log=log, state="error", message=msg, error=msg, finished_at=lu.now())
        lu.cleanup_versions(self.data, {v for v in (old_cur, old_prev) if v})
        return False

    # ---------- Hauptschleife
    def run(self, open_browser=True):
        if self.health(timeout=1):
            say(f"Das Finanz-Dashboard läuft bereits: {self.url}",
                f"The finance dashboard is already running: {self.url}")
            if open_browser:
                webbrowser.open(self.url)
            return 0
        if not self.port_free():
            fail(f"Port {self.port} ist von einem anderen Programm belegt. Anderen Port wählen, z.B. FD_PORT=8766.",
                 f"Port {self.port} is used by another program. Choose another one, e.g. FD_PORT=8766.")
            return 1
        try:
            self.data.mkdir(parents=True, exist_ok=True)
            if os.name == "posix":
                os.chmod(self.data, 0o700)
        except OSError as e:
            fail(f"Datenordner kann nicht angelegt werden: {e}", f"Cannot create data folder: {e}")
            return 1
        say("")
        say("  Finanz-Dashboard")
        say(f"  Adresse / Address:  {self.url}")
        say(f"  Daten / Data:       {self.data}")
        say("  Zum Beenden: Einstellungen → «Dashboard beenden», dieses Fenster schliessen oder Strg+C.")
        say("  To quit: Settings → “Quit dashboard”, close this window or press Ctrl+C.")
        say("")
        first, crashes = True, []
        while True:
            running = self.switch()
            if not running:
                app_dir, version = self.active()
                self.start(app_dir, version)
                if first:
                    if not self.wait_healthy():
                        if self.child.poll() is None:
                            self.stop_child()
                        fail("Das Dashboard startet nicht.", "The dashboard does not start.")
                        return 1
                    if open_browser:
                        webbrowser.open(self.url)
            first = False
            code = self.child.wait()
            self.child = None
            if code == 0:
                say("Das Finanz-Dashboard wurde beendet.", "The finance dashboard has been closed.")
                return 0
            if code == lu.RESTART_CODE:
                continue
            crashes = [t for t in crashes if time.time() - t < 120] + [time.time()]
            if len(crashes) >= 3:
                fail(f"Das Dashboard ist mehrfach abgestürzt (Code {code}).",
                     f"The dashboard crashed repeatedly (code {code}).")
                return 1
            say(f"Das Dashboard wurde unerwartet beendet (Code {code}) – starte neu …",
                f"The dashboard stopped unexpectedly (code {code}) – restarting …")
            time.sleep(2)


def log_to_file():
    """Die .exe/App hat kein Konsolenfenster: Ausgaben in local/launcher.log im Datenordner schreiben."""
    try:
        d = lu.local_dir(lu.default_data_dir())
        d.mkdir(parents=True, exist_ok=True)
        log = d / "launcher.log"
        if log.is_file() and log.stat().st_size > 1024 * 1024:
            log.replace(d / "launcher.log.1")
        f = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 (bleibt bis Programmende offen)
        sys.stdout = sys.stderr = f
        print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    except OSError:
        pass


def main():
    if FROZEN:
        log_to_file()
    launcher = Launcher()
    if launcher.delegate():
        return 0
    def _term(*_):
        raise KeyboardInterrupt
    for sig in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), _term)
    try:
        return launcher.run(open_browser="--no-browser" not in sys.argv and not os.environ.get("FD_NO_BROWSER"))
    except KeyboardInterrupt:
        say("\nDas Finanz-Dashboard wird beendet …", "Closing the finance dashboard …")
        return 0
    finally:
        launcher.stop_child()


if __name__ == "__main__":
    sys.exit(main())
