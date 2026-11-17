"""Online mode: shared registry database, user accounts, sessions and request guards.

Local mode (the Mac/PC launchers) never imports this module's server-side features:
it keeps its 127.0.0.1 listener, URL token and private SQLite registry.
"""
import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import sqlite3
import struct
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit


# ── Database: SQLite file (local, OVH) or PostgreSQL (Scalingo, DATABASE_URL) ──

class Database:
    """Tiny adapter: '?' placeholders, rows as dicts, one IntegrityError tuple."""

    def __init__(self, url=None, sqlite_path=None):
        self.url = url
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None
        if url:
            import psycopg
            from psycopg.rows import dict_row
            self._psycopg, self._dict_row = psycopg, dict_row
            self.IntegrityError = (psycopg.errors.IntegrityError,)
        else:
            self.IntegrityError = (sqlite3.IntegrityError,)
        self.postgres = bool(url)

    @contextmanager
    def connect(self):
        if self.postgres:
            with self._psycopg.connect(self.url, row_factory=self._dict_row, connect_timeout=10) as db:
                yield _Connection(db, postgres=True)
            return
        self.sqlite_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(self.sqlite_path, timeout=15)
        try:
            self.sqlite_path.chmod(0o600)
        except OSError:
            pass
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield _Connection(db, postgres=False)
        finally:
            db.close()


class _Connection:
    def __init__(self, db, postgres):
        self.db, self.postgres = db, postgres

    def execute(self, sql, params=()):
        if self.postgres:
            sql = sql.replace('?', '%s')
        return _Cursor(self.db.execute(sql, params))

    def columns(self, table):
        if self.postgres:
            rows = self.execute('SELECT column_name AS name FROM information_schema.columns WHERE table_name=?', (table,)).fetchall()
        else:
            rows = self.execute(f'PRAGMA table_info({table})').fetchall()
        return {row['name'] for row in rows}


class Row(dict):
    """A dict that also answers row[0], like sqlite3.Row."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(row) for row in self.cursor.fetchall()]


# ── Passwords and one-time codes ──

SCRYPT = {'n': 2 ** 14, 'r': 8, 'p': 1}


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, dklen=32, **SCRYPT)
    return 'scrypt${n}${r}${p}$'.format(**SCRYPT) + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def check_password(password, stored):
    try:
        _, n, r, p, salt, digest = stored.split('$')
        actual = hashlib.scrypt(password.encode('utf-8'), salt=base64.b64decode(salt), dklen=32, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Same cost as a real check, so an unknown user takes as long as a wrong password.
_DUMMY = hash_password(secrets.token_urlsafe(16))


def totp(secret, at=None, step=30):
    key = base64.b32decode(secret.upper() + '=' * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return '%06d' % ((struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000)


def check_totp(secret, code, at=None):
    code = re.sub(r'\s', '', str(code or ''))
    if not re.fullmatch(r'[0-9]{6}', code):
        return False
    now = time.time() if at is None else at
    return any(hmac.compare_digest(totp(secret, now + drift * 30), code) for drift in (-1, 0, 1))


def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


# ── Accounts, sessions, login throttling ──

USERNAME = re.compile(r'[a-z0-9][a-z0-9._-]{1,39}')
IDLE = 4 * 3600        # session ends after 4 hours without activity
ABSOLUTE = 12 * 3600   # and in any case 12 hours after login
COOKIE = '__Host-fullmoon'


class Accounts:
    def __init__(self, database):
        self.db = database
        self.failures = {}
        self.lock = threading.Lock()
        with self.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, '
                       'totp_secret TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, '
                       'created DOUBLE PRECISION NOT NULL, seen DOUBLE PRECISION NOT NULL)')

    # Administration (command line only, never from the web page).
    def add(self, username, password, with_totp=False):
        username = username.strip().lower()
        if not USERNAME.fullmatch(username):
            raise ValueError('Identifiant : 2 à 40 caractères, minuscules, chiffres, point, tiret ou souligné.')
        if len(password) < 12:
            raise ValueError('Mot de passe : 12 caractères minimum.')
        secret = new_totp_secret() if with_totp else None
        with self.db.connect() as db:
            if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                db.execute('UPDATE users SET password_hash=?, totp_secret=?, active=1 WHERE username=?', (hash_password(password), secret, username))
            else:
                db.execute('INSERT INTO users(username,password_hash,totp_secret,active,created_at) VALUES(?,?,?,1,?)',
                           (username, hash_password(password), secret, time.strftime('%Y-%m-%dT%H:%M:%S%z')))
            db.execute('DELETE FROM sessions WHERE username=?', (username,))
        return secret

    def disable(self, username):
        with self.db.connect() as db:
            found = db.execute('UPDATE users SET active=0 WHERE username=?', (username.strip().lower(),)).cursor.rowcount
            db.execute('DELETE FROM sessions WHERE username=?', (username.strip().lower(),))
        if not found:
            raise ValueError('Utilisateur inconnu.')

    def list(self):
        with self.db.connect() as db:
            return db.execute('SELECT username, active, totp_secret IS NOT NULL AS totp, created_at FROM users ORDER BY username').fetchall()

    # Web login.
    def _throttled(self, keys, now):
        with self.lock:
            for key in keys:
                recent = [t for t in self.failures.get(key, []) if now - t < 900]
                self.failures[key] = recent
                if len(recent) >= 5:
                    return True
        return False

    def _failed(self, keys, now):
        with self.lock:
            for key in keys:
                self.failures.setdefault(key, []).append(now)

    def login(self, username, password, code, ip):
        now = time.time()
        username = str(username or '').strip().lower()
        keys = ('ip:' + ip, 'user:' + username)
        if self._throttled(keys, now):
            raise PermissionError('Trop d’essais. Réessayez dans 15 minutes.')
        with self.db.connect() as db:
            user = db.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone() if USERNAME.fullmatch(username) else None
        valid = check_password(str(password or ''), user['password_hash'] if user else _DUMMY) and user is not None
        if valid and user['totp_secret']:
            valid = check_totp(user['totp_secret'], code, now)
        if not valid:
            self._failed(keys, now)
            raise PermissionError('Identifiant, mot de passe ou code incorrect.')
        token = secrets.token_urlsafe(32)
        with self.db.connect() as db:
            db.execute('DELETE FROM sessions WHERE seen<? OR created<?', (now - IDLE, now - ABSOLUTE))
            db.execute('INSERT INTO sessions(token_hash,username,created,seen) VALUES(?,?,?,?)', (_digest(token), username, now, now))
        return token

    def session(self, token):
        if not token:
            return None
        now = time.time()
        with self.db.connect() as db:
            row = db.execute('SELECT s.username, s.created, s.seen FROM sessions s JOIN users u ON u.username=s.username '
                             'WHERE s.token_hash=? AND u.active=1', (_digest(token),)).fetchone()
            if not row or now - row['seen'] > IDLE or now - row['created'] > ABSOLUTE:
                return None
            if now - row['seen'] > 60:
                db.execute('UPDATE sessions SET seen=? WHERE token_hash=?', (now, _digest(token)))
        return row['username']

    def logout(self, token):
        if token:
            with self.db.connect() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (_digest(token),))


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ── Settings and request guards ──

class Settings:
    def __init__(self, environ=None):
        env = os.environ if environ is None else environ
        public = env.get('FULLMOON_PUBLIC_URL', '').strip().rstrip('/')
        parts = urlsplit(public)
        # http only for a trial on the machine itself (browsers treat localhost as secure).
        local_trial = parts.scheme == 'http' and parts.hostname == 'localhost'
        if (parts.scheme != 'https' and not local_trial) or not parts.netloc or parts.path:
            raise SystemExit('FULLMOON_PUBLIC_URL doit être l’adresse https de l’outil, par exemple https://outil.exemple.fr')
        self.https = parts.scheme == 'https'
        self.origin = f'{parts.scheme}://{parts.netloc}'
        self.host = parts.netloc
        self.port = int(env.get('PORT', '8790'))
        self.bind = env.get('FULLMOON_BIND', '0.0.0.0')
        self.trust_proxy = env.get('FULLMOON_TRUST_PROXY', '1') == '1'
        self.networks = [ipaddress.ip_network(x.strip(), strict=False) for x in env.get('FULLMOON_ALLOWED_IPS', '').split(',') if x.strip()]
        self.database_url = env.get('SCALINGO_POSTGRESQL_URL') or env.get('DATABASE_URL') or None
        self.data_dir = Path(env.get('FULLMOON_DATA_DIR', Path(__file__).resolve().parent / 'donnees-en-ligne'))
        self.cart_key = env.get('PS_CART_KEY', '').strip() or None
        for name in ('PS_SHOP_URL', 'PS_API_KEY'):
            if not env.get(name, '').strip() and not (Path(__file__).resolve().parents[1] / '.env').is_file():
                raise SystemExit(f'Variable {name} manquante.')

    def database(self):
        return Database(self.database_url, None if self.database_url else self.data_dir / 'registre.sqlite')

    def client_ip(self, headers, peer):
        if self.trust_proxy and headers.get('X-Forwarded-For'):
            # The reverse proxy appends the address it saw: the last entry is the only trustworthy one.
            return headers['X-Forwarded-For'].split(',')[-1].strip()
        return peer

    def ip_allowed(self, ip):
        if not self.networks:
            return True
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in network for network in self.networks)


def cookie_value(header):
    for part in (header or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name == COOKIE:
            return value
    return None


def session_cookie(token, max_age=ABSOLUTE):
    return f'{COOKIE}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Strict'
