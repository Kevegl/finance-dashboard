#!/usr/bin/env python3
"""Baut die eigenständige Programmversion (Windows .exe bzw. macOS .app) mit PyInstaller.

Läuft in GitHub Actions (.github/workflows/release.yml). Nur zum Bauen wird PyInstaller gebraucht –
das Dashboard selbst bleibt reine Python-Standardbibliothek.

  pip install pyinstaller==6.11.1
  python packaging/build_app.py            -> dist/FinanzDashboard-<version>-<system>.zip

Die .exe/App enthält Python, das Startprogramm (app/launcher.py) und den Ordner app/. Updates lädt sie wie die
Python-Variante in den Datenordner; deshalb werden alle Standardbibliotheks-Module mitgeliefert, die der Code in
app/ benutzt (plus eine Reserve für spätere Versionen).
"""
import ast
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
NAME = "FinanzDashboard"
# Reserve: Module, die spätere Versionen von app/ zusätzlich brauchen könnten
EXTRA = ["calendar", "decimal", "fractions", "statistics", "uuid", "gzip", "bz2", "lzma", "tarfile", "html",
         "html.parser", "xml.etree.ElementTree", "email.utils", "email.message", "mimetypes", "locale", "shlex",
         "difflib", "textwrap", "string", "random", "heapq", "bisect", "copy", "pprint", "queue", "concurrent.futures",
         "unicodedata", "codecs", "encodings.idna", "importlib.machinery", "http.cookiejar", "socketserver",
         "logging", "traceback", "dataclasses", "enum", "functools", "itertools", "operator", "typing",
         "tkinter", "tkinter.ttk", "tkinter.messagebox"]   # Statusfenster (app/status_window.py)


def stdlib_imports():
    names = set()
    for f in APP.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module)
    std = getattr(sys, "stdlib_module_names", None)
    return sorted(n for n in names if std is None or n.split(".")[0] in std)


def version():
    return (APP / "VERSION").read_text(encoding="utf-8").strip()


for _s in (sys.stdout, sys.stderr):   # Windows-Konsole in GitHub Actions: cp1252 -> UTF-8
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main():
    system = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")
    arch = platform.machine().lower().replace("x86_64", "x64").replace("amd64", "x64").replace("aarch64", "arm64")
    build = ROOT / "build"
    dist = ROOT / "dist"
    shutil.rmtree(build, ignore_errors=True)
    shutil.rmtree(dist, ignore_errors=True)
    # Ordner app/ ohne Python-Cache mitliefern
    stage = build / "stage" / "app"
    shutil.copytree(APP, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data"))
    hidden = sorted(set(stdlib_imports()) | set(EXTRA))
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
           "--distpath", str(dist), "--workpath", str(build / "work"), "--specpath", str(build),
           "--add-data", f"{stage}{os.pathsep}app",
           "--osx-bundle-identifier", "io.github.finanzdashboard"]
    for h in hidden:
        cmd += ["--hidden-import", h]
    cmd.append(str(APP / "launcher.py"))
    print("PyInstaller mit", len(hidden), "Standardmodulen …", flush=True)
    subprocess.run(cmd, check=True)

    out = dist / f"{NAME}-{version()}-{system}-{arch}.zip"
    if system == "macos":
        # ditto erhält Symlinks und Signatur des .app-Pakets
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(dist / f"{NAME}.app"), str(out)], check=True)
    else:
        folder = dist / NAME
        shutil.copy(ROOT / "packaging" / "LIESMICH.txt", folder / "LIESMICH.txt")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(folder.rglob("*")):
                z.write(f, Path(NAME) / f.relative_to(folder))
    print("Fertig:", out, f"({out.stat().st_size / 1e6:.1f} MB)")
    # Pfad für den Kurztest und den Upload in GitHub Actions
    exe = dist / NAME / (NAME + ".exe" if system == "windows" else NAME)
    if system == "macos":
        exe = dist / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"zip={out}\nexe={exe}\n")


if __name__ == "__main__":
    main()
