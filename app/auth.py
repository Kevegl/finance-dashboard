"""Anmeldung: Passwort-Hash (PBKDF2-SHA256), Sitzungen, Anmeldeprotokoll, Zwei-Faktor (TOTP), Wiederherstellungscode."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import struct
import threading
import time
import urllib.parse

from config import DATA_DIR, LOCAL, MIN_PW, SESSION_DAYS
from store import db_tx, get_setting, set_setting


# --------------------------------------------------------------------------
# Anmeldung (Passwort-Hash PBKDF2-SHA256, Sitzungen per HttpOnly-Cookie)
# --------------------------------------------------------------------------

def hash_pw(pw, iterations=310_000):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${h.hex()}"


class ServerBusy(Exception):
    """Zu viele gleichzeitige Passwortprüfungen (Schutz vor CPU-Überlast durch PBKDF2)."""


PW_SLOTS = threading.BoundedSemaphore(2)   # höchstens 2 PBKDF2-Berechnungen gleichzeitig


def verify_pw(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        salt_b, it_n = bytes.fromhex(salt), int(it)
    except Exception:  # noqa: BLE001
        return False
    if not PW_SLOTS.acquire(timeout=10):
        raise ServerBusy()
    try:
        calc = hashlib.pbkdf2_hmac("sha256", str(pw).encode(), salt_b, it_n).hex()
    finally:
        PW_SLOTS.release()
    return hmac.compare_digest(calc, h)


def auth_configured():
    return bool(get_setting("auth_hash"))


def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db_tx() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        con.execute("INSERT INTO sessions(token_hash,user,created,expires) VALUES (?,?,?,?)",
                    (_th(token), user, now, now + SESSION_DAYS * 86400))
    return token


def session_user(token):
    if not token:
        return None
    with db_tx() as con:
        r = con.execute("SELECT user, expires FROM sessions WHERE token_hash=?", (_th(token),)).fetchone()
    if not r or r["expires"] < time.time():
        return None
    return get_setting("auth_user") or r["user"]


def revoke_sessions(keep_token=None):
    with db_tx() as con:
        if keep_token:
            con.execute("DELETE FROM sessions WHERE token_hash != ?", (_th(keep_token),))
        else:
            con.execute("DELETE FROM sessions")


def check_new_password(pw):
    if len(pw or "") < MIN_PW:
        raise ValueError(f"Das Passwort muss mindestens {MIN_PW} Zeichen lang sein.")


# --- Anmeldeprotokoll
AUTH_LOG_KEEP = 500
FAIL_EVENTS = ("login_fail", "totp_fail", "recovery_fail", "password_fail", "setup_fail")


def log_auth(event, ip, detail=""):
    """Schreibt ein Anmeldeereignis ins Protokoll (letzte 500) und als Zeile auf stdout (Journal).
    detail ist nur ein gekürzter User-Agent o.ä. – nie Passwörter oder Codes."""
    detail = (detail or "")[:120]
    try:
        with db_tx() as con:
            con.execute("INSERT INTO auth_log(ts, event, ip, detail) VALUES (?,?,?,?)",
                        (time.time(), event, ip or "", detail))
            con.execute("DELETE FROM auth_log WHERE id <= (SELECT MAX(id) FROM auth_log) - ?", (AUTH_LOG_KEEP,))
    except sqlite3.Error:
        pass
    print(f"Anmeldung: {event} von {ip or '-'}", flush=True)


def auth_log_summary(session_created):
    since = time.time() - 30 * 86400
    with db_tx() as con:
        rows = [dict(r) for r in con.execute("SELECT ts, event, ip, detail FROM auth_log ORDER BY id DESC LIMIT 30")]
        last = con.execute("SELECT ts, ip FROM auth_log WHERE event IN ('login_ok','recovery_used','setup') AND ts < ? "
                           "ORDER BY ts DESC LIMIT 1", (session_created or time.time(),)).fetchone()
        fails = con.execute(f"SELECT COUNT(*) FROM auth_log WHERE ts >= ? AND event IN ({','.join('?' * len(FAIL_EVENTS))})",
                            (since, *FAIL_EVENTS)).fetchone()[0]
    return {"entries": rows, "last_login": dict(last) if last else None, "fails_30d": fails}


# --- Zwei-Faktor (TOTP, RFC 6238: HMAC-SHA1, 30 s, 6 Stellen)
TOTP_LOCK = threading.Lock()


def totp_new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return f"{(struct.unpack('>I', mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_match(secret, code, last_step=-1):
    """Gibt den passenden Zeitschritt zurück (Fenster ±1, nur neuer als last_step) oder None."""
    code = re.sub(r"\s", "", str(code or ""))
    if not secret or not re.fullmatch(r"[0-9]{6}", code):   # nur ASCII-Ziffern (compare_digest)
        return None
    now, found = int(time.time() // 30), None
    for step in (now - 1, now, now + 1):
        if hmac.compare_digest(totp_code(secret, step), code) and step > last_step and (found is None or step > found):
            found = step
    return found


def totp_active():
    return bool(get_setting("auth_totp_secret"))


def totp_verify_login(code):
    """Prüft den Code gegen das aktive Geheimnis und verbraucht den Zeitschritt (keine Wiederverwendung)."""
    with TOTP_LOCK:
        step = totp_match(get_setting("auth_totp_secret"), code, int(get_setting("auth_totp_last", "-1") or -1))
        if step is None:
            return False
        set_setting("auth_totp_last", str(step))
        return True


def totp_uri(user, secret):
    label = "Finanz-Dashboard:" + urllib.parse.quote(user or "", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer=Finanz-Dashboard&digits=6&period=30"


def totp_off():
    for k in ("auth_totp_secret", "auth_totp_pending", "auth_totp_last"):
        set_setting(k, None)


# --- Wiederherstellungscode (nur als PBKDF2-Hash gespeichert)
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # ohne 0/O/1/I/L


def recovery_norm(code):
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def recovery_new():
    raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(25))
    set_setting("auth_recovery_hash", hash_pw(raw))
    return "-".join(raw[i:i + 5] for i in range(0, 25, 5))


def recovery_check(code):
    stored = get_setting("auth_recovery_hash")
    norm = recovery_norm(code)
    return bool(stored) and len(norm) == 25 and verify_pw(norm, stored)


# --- Einrichtungscode: schützt die Ersteinrichtung, solange noch kein Passwort festgelegt ist
# Ohne ihn könnte jeder, der eine frische Installation im Netzwerk zuerst aufruft (oder eine fremde Webseite per
# DNS-Rebinding), das Dashboard übernehmen. Der Code steht im Protokoll (journalctl/docker logs), in der Ausgabe von
# install.sh und in setup-code.txt im Datenordner. Im lokalen Betrieb (nur 127.0.0.1) braucht es ihn nicht.
SETUP_CODE_FILE = DATA_DIR / "setup-code.txt"
SETUP_CODE_LOCK = threading.Lock()


def setup_code_required():
    return not LOCAL and not auth_configured()


def setup_code_pending():
    """Für die Anzeige: braucht die Einrichtung einen Code? Legt ihn dann an (z.B. nach --reset-password bei
    laufendem Dashboard), damit er in setup-code.txt und im Protokoll steht, bevor jemand ihn eingeben muss."""
    if not setup_code_required():
        return False
    setup_code()
    return True


def _announce(code):
    print(f"Einrichtungscode für die Ersteinrichtung: {code[:4]}-{code[4:]}  (auch in {SETUP_CODE_FILE})", flush=True)


def setup_code(announce=False):
    """Liefert den aktuellen Einrichtungscode (8 Zeichen, ohne Bindestrich); legt ihn bei Bedarf an.
    Ein neuer Code erscheint immer im Protokoll, ein bestehender nur mit announce=True (beim Start)."""
    with SETUP_CODE_LOCK:
        try:
            code = recovery_norm(SETUP_CODE_FILE.read_text(encoding="ascii"))
            if len(code) == 8:
                if announce:
                    _announce(code)
                return code
        except (OSError, UnicodeDecodeError):
            pass                        # fehlt oder unlesbar (z.B. von root angelegt) -> neu anlegen
        code = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(8))
        shown = f"{code[:4]}-{code[4:]}"
        tmp = SETUP_CODE_FILE.with_name(f".setup-code-{secrets.token_hex(4)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(shown + "\n")
        os.replace(tmp, SETUP_CODE_FILE)
        _announce(code)
        return code


def setup_code_ok(entered):
    return hmac.compare_digest(recovery_norm(entered).encode(), setup_code().encode())


def setup_code_clear():
    with SETUP_CODE_LOCK:
        try:
            SETUP_CODE_FILE.unlink()
        except OSError:
            pass
