#!/usr/bin/env python3
"""Echte Windows-Zertifikatsprüfung (app/wincert.py) – läuft nur unter Windows (GitHub Actions: windows-latest).

Prüft, dass Windows
  1. ein gültiges Zertifikat anerkennt (api.github.com, auch über local_update mit erzwungener Windows-Prüfung),
  2. ein gültiges Zertifikat für den FALSCHEN Namen ablehnt,
  3. ein selbstsigniertes Zertifikat ablehnt (lokaler HTTPS-Server, braucht openssl).
"""
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))


def rejected(fn):
    try:
        fn()
    except (ssl.SSLCertVerificationError, urllib.error.URLError) as e:
        return str(getattr(e, "reason", e))
    return None


def main():
    if not sys.platform.startswith("win"):
        print("kein Windows – Test übersprungen")
        return
    import wincert
    import local_update as lu

    # 1. gültig
    assert wincert.selftest("api.github.com"), "api.github.com nicht anerkannt"
    lu._WIN["on"] = True                                   # wie nach dem ersten Fehlschlag mit OpenSSL
    try:
        rels = lu.fetch_releases()
        print("✓ api.github.com von Windows anerkannt, Releases:", [r["tag"] for r in rels][:3])
    except RuntimeError as e:
        if "begrenzt" not in str(e):                       # API-Grenze der geteilten Test-Rechner ist kein Fehler
            raise
        print("✓ api.github.com von Windows anerkannt (API-Grenze erreicht)")

    # 2. gültiges Zertifikat, falscher Name
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    import socket
    with socket.create_connection(("api.github.com", 443), timeout=15) as raw, \
            ctx.wrap_socket(raw, server_hostname="api.github.com") as s:
        chain = wincert._peer_chain(s)
    why = rejected(lambda: wincert.verify_chain(chain, "falscher-name.example.com"))
    assert why, "Zertifikat für falschen Namen wurde akzeptiert"
    print("✓ falscher Servername abgelehnt:", why)

    # 3. selbstsigniert
    if not shutil.which("openssl"):
        print("openssl fehlt – Test 3 übersprungen")
    else:
        d = tempfile.mkdtemp(prefix="fd-wincert-")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "k.pem", "-out", "c.pem",
                        "-days", "2", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                       cwd=d, check=True, capture_output=True)

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        sctx.load_cert_chain(f"{d}/c.pem", f"{d}/k.pem")
        srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"https://localhost:{srv.server_address[1]}/"
        why = rejected(lambda: wincert.urlopen(urllib.request.Request(url), timeout=10).read())
        assert why, "selbstsigniertes Zertifikat wurde akzeptiert"
        print("✓ selbstsigniertes Zertifikat abgelehnt:", why)
        msg = lu.net_error_text(urllib.error.URLError(ssl.SSLCertVerificationError(why)))
        assert "Edge" not in msg, msg
    print("WINDOWS-ZERTIFIKAT-TEST OK")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
