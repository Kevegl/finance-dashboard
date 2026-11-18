"""HTTPS-Zertifikate mit der Prüfung von Windows selbst verifizieren (wie Edge/Chrome) – nur Windows, nur stdlib.

Python/OpenSSL prüft unter Windows gegen eine Kopie der Stammzertifikate, die es beim Start aus dem Windows-Speicher
liest. Windows selbst lädt fehlende Stamm- und Zwischenzertifikate aber erst bei Bedarf nach (Windows Update im
Hintergrund). Auf frisch eingerichteten PCs fehlt deshalb oft genau das Zertifikat, das GitHub, Yahoo o.ä. brauchen.

Dieses Modul baut die TLS-Verbindung auf und lässt das Zertifikat des Servers von der Windows-Kryptografie
(CertGetCertificateChain + CertVerifyCertificateChainPolicy, Richtlinie «SSL») prüfen – inklusive Kette, Hostname
und Gültigkeit. Windows lädt fehlende Zertifikate dabei selbst nach. Geprüft wird, bevor Daten gesendet werden;
bei einem Fehler wird die Verbindung geschlossen.
"""
import ctypes
import http.client
import ssl
import sys
import urllib.request
from ctypes import wintypes

X509_PKCS7 = 0x00010001                 # X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
CERT_STORE_PROV_MEMORY = 2
CERT_STORE_ADD_USE_EXISTING = 2
CERT_CHAIN_POLICY_SSL = 4
AUTHTYPE_SERVER = 2
USAGE_MATCH_TYPE_AND = 0
SERVER_AUTH_OID = b"1.3.6.1.5.5.7.3.1"
CERT_CHAIN_CACHE_END_CERT = 0x00000001

ERRORS = {   # die häufigsten Fehler der Windows-Prüfung, für verständliche Meldungen
    0x800B0109: "Stammzertifikat nicht vertrauenswürdig",
    0x800B010F: "Zertifikat passt nicht zum Servernamen",
    0x800B0101: "Zertifikat abgelaufen oder noch nicht gültig",
    0x800B010A: "Zertifikatskette unvollständig",
    0x80092010: "Zertifikat wurde widerrufen",
    0x800B0111: "Zertifikat ausdrücklich nicht vertrauenswürdig",
}


class CERT_ENHKEY_USAGE(ctypes.Structure):
    _fields_ = [("cUsageIdentifier", wintypes.DWORD), ("rgpszUsageIdentifier", ctypes.POINTER(ctypes.c_char_p))]


class CERT_USAGE_MATCH(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("Usage", CERT_ENHKEY_USAGE)]


class CERT_CHAIN_PARA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("RequestedUsage", CERT_USAGE_MATCH)]


class SSL_EXTRA_CERT_CHAIN_POLICY_PARA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwAuthType", wintypes.DWORD), ("fdwChecks", wintypes.DWORD),
                ("pwszServerName", wintypes.LPCWSTR)]


class CERT_CHAIN_POLICY_PARA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("pvExtraPolicyPara", ctypes.c_void_p)]


class CERT_CHAIN_POLICY_STATUS(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwError", wintypes.DWORD), ("lChainIndex", wintypes.LONG),
                ("lElementIndex", wintypes.LONG), ("pvExtraPolicyStatus", ctypes.c_void_p)]


_API = {}


def _crypt32():
    if not _API:
        c = ctypes.WinDLL("crypt32", use_last_error=True)
        c.CertOpenStore.restype = ctypes.c_void_p
        c.CertOpenStore.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        c.CertAddEncodedCertificateToStore.restype = wintypes.BOOL
        c.CertAddEncodedCertificateToStore.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_char_p, wintypes.DWORD,
                                                        wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        c.CertGetCertificateChain.restype = wintypes.BOOL
        c.CertGetCertificateChain.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                              ctypes.POINTER(CERT_CHAIN_PARA), wintypes.DWORD, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_void_p)]
        c.CertVerifyCertificateChainPolicy.restype = wintypes.BOOL
        c.CertVerifyCertificateChainPolicy.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                       ctypes.POINTER(CERT_CHAIN_POLICY_PARA),
                                                       ctypes.POINTER(CERT_CHAIN_POLICY_STATUS)]
        c.CertFreeCertificateChain.restype = None
        c.CertFreeCertificateChain.argtypes = [ctypes.c_void_p]
        c.CertFreeCertificateContext.restype = wintypes.BOOL
        c.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
        c.CertCloseStore.restype = wintypes.BOOL
        c.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        _API["c"] = c
    return _API["c"]


def verify_chain(der_chain, server_name):
    """Prüft die Zertifikatskette (DER, Server-Zertifikat zuerst) mit Windows für server_name.
    Wirft ssl.SSLCertVerificationError, wenn Windows sie nicht als gültig anerkennt."""
    if not der_chain:
        raise ssl.SSLCertVerificationError("Kein Zertifikat vom Server erhalten")
    c = _crypt32()
    store = c.CertOpenStore(ctypes.c_void_p(CERT_STORE_PROV_MEMORY), 0, None, 0, None)
    if not store:
        raise ssl.SSLCertVerificationError(f"Windows-Zertifikatsprüfung nicht möglich ({ctypes.get_last_error()})")
    leaf, chain = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        for i, der in enumerate(der_chain):   # Server-Zertifikat + mitgeschickte Zwischenzertifikate
            ok = c.CertAddEncodedCertificateToStore(store, X509_PKCS7, der, len(der), CERT_STORE_ADD_USE_EXISTING,
                                                    ctypes.byref(leaf) if i == 0 else None)
            if i == 0 and not ok:
                raise ssl.SSLCertVerificationError("Zertifikat des Servers nicht lesbar")
        oids = (ctypes.c_char_p * 1)(SERVER_AUTH_OID)
        para = CERT_CHAIN_PARA(ctypes.sizeof(CERT_CHAIN_PARA),
                               CERT_USAGE_MATCH(USAGE_MATCH_TYPE_AND, CERT_ENHKEY_USAGE(1, oids)))
        if not c.CertGetCertificateChain(None, leaf, None, store, ctypes.byref(para), CERT_CHAIN_CACHE_END_CERT,
                                         None, ctypes.byref(chain)):
            raise ssl.SSLCertVerificationError(f"Windows konnte die Zertifikatskette nicht bilden ({ctypes.get_last_error()})")
        extra = SSL_EXTRA_CERT_CHAIN_POLICY_PARA(ctypes.sizeof(SSL_EXTRA_CERT_CHAIN_POLICY_PARA), AUTHTYPE_SERVER, 0,
                                                 server_name)
        ppara = CERT_CHAIN_POLICY_PARA(ctypes.sizeof(CERT_CHAIN_POLICY_PARA), 0,
                                       ctypes.cast(ctypes.pointer(extra), ctypes.c_void_p))
        status = CERT_CHAIN_POLICY_STATUS(ctypes.sizeof(CERT_CHAIN_POLICY_STATUS))
        if not c.CertVerifyCertificateChainPolicy(ctypes.c_void_p(CERT_CHAIN_POLICY_SSL), chain, ctypes.byref(ppara),
                                                  ctypes.byref(status)):
            raise ssl.SSLCertVerificationError(f"Windows-Zertifikatsprüfung fehlgeschlagen ({ctypes.get_last_error()})")
        if status.dwError:
            why = ERRORS.get(status.dwError, f"Windows-Fehler 0x{status.dwError:08X}")
            raise ssl.SSLCertVerificationError(f"Zertifikat von {server_name} ungültig: {why}")
    finally:
        if chain:
            c.CertFreeCertificateChain(chain)
        if leaf:
            c.CertFreeCertificateContext(leaf)
        c.CertCloseStore(store, 0)


def _peer_chain(sock):
    """Server-Zertifikat und (ab Python 3.13) die mitgeschickten Zwischenzertifikate als DER."""
    chain = []
    get = getattr(sock, "get_unverified_chain", None)
    if get:
        try:
            for cert in get() or []:
                chain.append(cert if isinstance(cert, bytes) else cert.public_bytes(ssl._ssl.ENCODING_DER))
        except Exception:  # noqa: BLE001
            chain = []
    if not chain:
        leaf = sock.getpeercert(binary_form=True)
        chain = [leaf] if leaf else []
    return chain


class WindowsVerifiedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS-Verbindung, deren Zertifikat Windows prüft (statt der OpenSSL-Kopie der Stammzertifikate)."""

    def __init__(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False          # übernimmt Windows (Richtlinie «SSL» mit Servername)
        ctx.verify_mode = ssl.CERT_NONE     # übernimmt Windows – Prüfung unten, bevor Daten gesendet werden
        kwargs["context"] = ctx
        super().__init__(*args, **kwargs)

    def connect(self):
        http.client.HTTPConnection.connect(self)          # TCP (inkl. Proxy-Tunnel)
        host = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=host)
        try:
            verify_chain(_peer_chain(self.sock), host)
        except Exception:
            self.sock.close()
            self.sock = None
            raise


class WindowsVerifiedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(WindowsVerifiedHTTPSConnection, req)


def available():
    return sys.platform.startswith("win")


def urlopen(req, timeout=30):
    """Wie urllib.request.urlopen, aber das Zertifikat prüft Windows."""
    opener = urllib.request.build_opener(WindowsVerifiedHTTPSHandler())
    return opener.open(req, timeout=timeout)


def selftest(host="api.github.com", port=443, timeout=15):
    """Für Tests: Verbindung zu host aufbauen und nur mit Windows prüfen."""
    conn = WindowsVerifiedHTTPSConnection(host, port, timeout=timeout)
    try:
        conn.connect()
        return True
    finally:
        conn.close()


__all__ = ["available", "urlopen", "verify_chain", "selftest", "WindowsVerifiedHTTPSHandler"]
