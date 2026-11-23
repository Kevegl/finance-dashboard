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
from store import current_uid, db_tx, get_setting, init_db, set_setting, user_context, user_db_path


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
    """Ist der Administrator (Ersteinrichtung) angelegt? Erst dann gibt es Anmeldung und Registrierung."""
    return bool(get_setting("auth_hash", main=True))


def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user, uid=None):
    """Neue Sitzung für den aktuellen (bzw. angegebenen) Benutzer. Administrator: Tabelle sessions,
    weitere Benutzer: user_sessions (beide in der Hauptdatenbank)."""
    uid = current_uid() if uid is None else int(uid)
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db_tx(main=True) as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        con.execute("DELETE FROM user_sessions WHERE expires < ?", (now,))
        if uid == 0:
            con.execute("INSERT INTO sessions(token_hash,user,created,expires) VALUES (?,?,?,?)",
                        (_th(token), user, now, now + SESSION_DAYS * 86400))
        else:
            con.execute("INSERT INTO user_sessions(token_hash,uid,created,expires) VALUES (?,?,?,?)",
                        (_th(token), uid, now, now + SESSION_DAYS * 86400))
    return token


def session_lookup(token):
    """Gibt (uid, Benutzername, erstellt) der gültigen Sitzung zurück oder None."""
    if not token:
        return None
    now = time.time()
    with db_tx(main=True) as con:
        r = con.execute("SELECT user, created, expires FROM sessions WHERE token_hash=?", (_th(token),)).fetchone()
        if r:
            if r["expires"] < now:
                return None
            owner = con.execute("SELECT value FROM settings WHERE key='auth_user'").fetchone()
            return 0, (owner[0] if owner else None) or r["user"], r["created"]
        r = con.execute("SELECT s.uid, s.created, s.expires, u.username FROM user_sessions s "
                        "JOIN users u ON u.id = s.uid WHERE s.token_hash=?", (_th(token),)).fetchone()
    if not r or r["expires"] < now:
        return None
    return r["uid"], r["username"], r["created"]


def session_user(token):
    r = session_lookup(token)
    return r[1] if r else None


def revoke_sessions(keep_token=None, uid=None):
    """Meldet alle Sitzungen eines Benutzers ab (Standard: aktueller Benutzer), optional ausser keep_token."""
    uid = current_uid() if uid is None else int(uid)
    table, where, args = ("sessions", "1=1", []) if uid == 0 else ("user_sessions", "uid=?", [uid])
    if keep_token:
        where, args = where + " AND token_hash != ?", args + [_th(keep_token)]
    with db_tx(main=True) as con:
        con.execute(f"DELETE FROM {table} WHERE {where}", args)


def count_sessions(uid=None):
    uid = current_uid() if uid is None else int(uid)
    with db_tx(main=True) as con:
        if uid == 0:
            return con.execute("SELECT COUNT(*) FROM sessions WHERE expires > ?", (time.time(),)).fetchone()[0]
        return con.execute("SELECT COUNT(*) FROM user_sessions WHERE uid=? AND expires > ?",
                           (uid, time.time())).fetchone()[0]


# --- Mehrere Benutzer: Registrierung, Benutzerliste (nur Administrator), Löschen
USERS_LOCK = threading.Lock()
USERNAME_MAX = 40


def check_username(name):
    name = str(name or "").strip()
    if not name or len(name) > USERNAME_MAX or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError(f"Der Benutzername muss 1 bis {USERNAME_MAX} Zeichen lang sein.")
    return name


def name_key(name):
    """Vergleichsschlüssel für Benutzernamen: Gross-/Kleinschreibung egal, auch bei Umlauten."""
    return str(name or "").strip().casefold()


def find_user(name):
    """uid zum Benutzernamen (Gross-/Kleinschreibung egal): 0 = Administrator, None = unbekannt."""
    name = str(name or "").strip()
    if not name:
        return None
    owner = get_setting("auth_user", main=True) or ""
    if owner and hmac.compare_digest(name_key(name).encode(), name_key(owner).encode()):
        return 0
    with db_tx(main=True) as con:
        r = con.execute("SELECT id FROM users WHERE name_key = ?", (name_key(name),)).fetchone()
    return r[0] if r else None


def registration_open():
    return get_setting("registration", "1", main=True) == "1"


def create_user(name, password, lang=None):
    """Registriert einen weiteren Benutzer mit eigener, leerer Datenbank. Gibt (uid, Wiederherstellungscode) zurück."""
    name = check_username(name)
    check_new_password(password)
    with USERS_LOCK:
        if find_user(name) is not None:
            raise ValueError("Dieser Benutzername ist bereits vergeben.")
        with db_tx(main=True) as con:
            uid = con.execute("INSERT INTO users(username, name_key, created) VALUES (?,?,?)",
                              (name, name_key(name), time.time())).lastrowid
        _remove_db_files(uid)                         # Reste einer früher gelöschten Datenbank mit derselben Nummer
        with user_context(uid):
            init_db()
            set_setting("auth_user", name)
            set_setting("auth_hash", hash_pw(password))
            if lang:
                set_setting("lang", lang)
            code = recovery_new()
    return uid, code


def rename_user(uid, name):
    """Benutzername ändern (eindeutig über alle Benutzer)."""
    name = check_username(name)
    with USERS_LOCK:
        other = find_user(name)
        if other is not None and other != uid:
            raise ValueError("Dieser Benutzername ist bereits vergeben.")
        if uid:
            with db_tx(main=True) as con:
                con.execute("UPDATE users SET username=?, name_key=? WHERE id=?", (name, name_key(name), uid))
        with user_context(uid):
            set_setting("auth_user", name)
    return name


def list_users():
    """Alle Benutzer für die Verwaltung durch den Administrator."""
    out = [{"id": 0, "username": get_setting("auth_user", main=True) or "", "admin": True, "created": None}]
    with db_tx(main=True) as con:
        rows = [dict(r) for r in con.execute("SELECT id, username, created FROM users ORDER BY id")]
    for r in rows:
        try:
            with user_context(r["id"]), db_tx() as con:
                r["transactions"] = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        except sqlite3.Error:
            r["transactions"] = None
        out.append(dict(r, admin=False))
    with db_tx(main=True) as con:
        out[0]["transactions"] = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return out


def user_ids():
    """Alle Benutzer inkl. Administrator (0) – z.B. für die Kurs-Aktualisierung im Hintergrund."""
    with db_tx(main=True) as con:
        return [0] + [r[0] for r in con.execute("SELECT id FROM users ORDER BY id")]


def _remove_db_files(uid):
    base = user_db_path(uid)
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            base.with_name(base.name + suffix).unlink()
        except FileNotFoundError:
            pass


def delete_user(uid):
    """Löscht einen weiteren Benutzer samt Datenbank und Sitzungen (nicht den Administrator)."""
    uid = int(uid)
    if uid <= 0:
        raise ValueError("Der Administrator kann nicht gelöscht werden.")
    with USERS_LOCK:
        with db_tx(main=True) as con:
            if not con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                raise ValueError("Benutzer nicht gefunden.")
            con.execute("DELETE FROM user_sessions WHERE uid=?", (uid,))
            con.execute("DELETE FROM users WHERE id=?", (uid,))
        _remove_db_files(uid)


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
        last = con.execute("SELECT ts, ip FROM auth_log WHERE event IN ('login_ok','recovery_used','setup','register') AND ts < ? "
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
