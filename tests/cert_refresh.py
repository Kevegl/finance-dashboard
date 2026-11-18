#!/usr/bin/env python3
"""Regressionstest (Windows-Zertifikate): Kennt Python/OpenSSL ein Stammzertifikat nicht, muss das Dashboard die
Prüfung an Windows übergeben (das fehlende Zertifikate selbst nachlädt) – ohne Edge, PowerShell oder Neustart.
Fehler in 1.0.1–1.1.0: dort scheiterte die Wiederholung bzw. hing an einer Ergänzung des Windows-Speichers.

Simuliert mit einer echten TLS-Verbindung zu einem lokalen HTTPS-Server und eigener Test-Zertifizierungsstelle:
Python kennt das Stammzertifikat nicht, die (simulierte) Windows-Prüfung schon. Braucht das Programm openssl.
Die echte Windows-Prüfung testet tests/wincert_check.py auf einem Windows-Rechner.
"""
import http.client
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

    # Python/OpenSSL kennt das Test-Stammzertifikat nicht
    no_root = lambda *_a, **_k: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # noqa: E731
    ssl.create_default_context = no_root
    http.client._create_https_context = lambda _v: no_root()
    sys.path.insert(0, str(ROOT / "app"))
    import local_update as lu
    lu.sys.platform = "win32"
    calls = []

    def windows_trusts(req, timeout):        # simulierte Windows-Prüfung: kennt das Stammzertifikat
        calls.append(req.full_url)
        trusted = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trusted.load_verify_locations(f"{d}/ca.pem")
        return urllib.request.urlopen(req, timeout=timeout, context=trusted)

    lu._windows_urlopen = windows_trusts
    url = f"https://localhost:{srv.server_address[1]}/test"
    assert lu.urlopen(urllib.request.Request(url), timeout=5).read() == b"[]"
    assert len(calls) == 1 and lu._WIN["on"], calls
    assert lu.urlopen(urllib.request.Request(url), timeout=5).read() == b"[]"   # danach direkt über Windows
    assert len(calls) == 2
    print("✓ Python kennt das Zertifikat nicht -> Windows prüft, ohne Edge/PowerShell/Neustart")

    def windows_rejects(req, timeout):       # Windows lehnt ab -> verständliche Meldung, kein «Edge öffnen»
        raise urllib.error.URLError(ssl.SSLCertVerificationError(
            "Zertifikat von localhost ungültig: Stammzertifikat nicht vertrauenswürdig"))

    lu._windows_urlopen = windows_rejects
    try:
        lu.urlopen(urllib.request.Request(url), timeout=5)
        raise SystemExit("FEHLER: ungültiges Zertifikat wurde akzeptiert")
    except urllib.error.URLError as e:
        msg = lu.net_error_text(e)
        assert "Windows-Zertifikatsprüfung" in msg and "Stammzertifikat nicht vertrauenswürdig" in msg, msg
        assert "Edge" not in msg, msg
    print("✓ von Windows abgelehntes Zertifikat bleibt abgelehnt, mit verständlicher Meldung")
    print("ZERTIFIKAT-TEST OK")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
