#!/usr/bin/env python3
"""Regressionstest (Windows-Zertifikate): Fehlt ein Stammzertifikat, lässt das Dashboard es von Windows ergänzen und
muss die Zertifikatsliste danach NEU einlesen – sonst scheitert die Wiederholung (Fehler in 1.0.1–1.0.3).

Simuliert mit einer echten TLS-Verbindung zu einem lokalen HTTPS-Server und eigener Test-Zertifizierungsstelle:
das Stammzertifikat steht erst nach der «Ergänzung durch Windows» im Speicher. Braucht das Programm openssl.
"""
import http.client
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def make_certs(d):
    run = lambda *a: subprocess.run(["openssl", *a], cwd=d, check=True, capture_output=True)  # noqa: E731
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
        "-subj", "/CN=Test-Stammzertifikat", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-addext", "subjectKeyIdentifier=hash")
    run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=localhost")
    Path(d, "ext").write_text("subjectAltName=DNS:localhost\nauthorityKeyIdentifier=keyid\n"
                              "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
    run("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "srv.pem",
        "-days", "2", "-extfile", "ext")


def main():
    if not shutil.which("openssl"):
        print("openssl fehlt – Test übersprungen")
        return
    d = tempfile.mkdtemp(prefix="fd-cert-")
    make_certs(d)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"[]")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(f"{d}/srv.pem", f"{d}/srv.key")
    srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    store = {"has_root": False}            # simulierter Windows-Zertifikatsspeicher
    orig = ssl.create_default_context

    def system_context(*_a, **_k):
        if store["has_root"]:
            return orig(cafile=f"{d}/ca.pem")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)   # ohne das Stammzertifikat
        return ctx

    ssl.create_default_context = system_context
    http.client._create_https_context = lambda _v: system_context()   # so erstellt urllib ab 3.12 seinen Kontext
    sys.path.insert(0, str(ROOT / "app"))
    import local_update as lu
    lu.sys.platform = "win32"
    lu.warm_windows_certs = lambda _u: store.update(has_root=True)
    url = f"https://localhost:{srv.server_address[1]}/test"
    body = lu.urlopen(urllib.request.Request(url), timeout=5).read()
    assert body == b"[]", body
    print("✓ fehlendes Stammzertifikat ergänzt und Zertifikatsliste neu eingelesen")
    print("ZERTIFIKAT-TEST OK")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
