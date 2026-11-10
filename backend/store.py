"""Persistent storage for the borrower and lender portals.

* Metadata lives in SQLite (``<NB_DATA_DIR>/northbridge.db``).
* Document files, extraction results and staff notes contain personal data, so they are
  encrypted with AES-256-GCM before they touch the disk. Each ciphertext is bound to its
  record with associated data, so a blob cannot be swapped onto another record.
* The key comes from ``NB_ENCRYPTION_KEY`` (base64, 32 bytes). Without it a development key
  is created next to the data and a warning is logged; never do that in production.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger('northbridge.store')

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('borrower','lender')), pw_hash TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS applications(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, borrower_id INTEGER NOT NULL, status TEXT NOT NULL,
  check_status TEXT, check_reason TEXT, review_required INTEGER NOT NULL DEFAULT 0, review_reasons TEXT NOT NULL DEFAULT '[]',
  result_blob BLOB, result_slots TEXT NOT NULL DEFAULT '[]', stages TEXT NOT NULL DEFAULT '[]', decision_reason TEXT,
  created REAL NOT NULL, updated REAL NOT NULL, submitted REAL);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, slot TEXT NOT NULL, version INTEGER NOT NULL,
  current INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL, detected_type TEXT, reason TEXT, requested_by INTEGER,
  storage_key TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL,
  filename_blob BLOB);
CREATE TABLE IF NOT EXISTS upload_links(
  token_hash TEXT PRIMARY KEY, application_id INTEGER NOT NULL, slot TEXT NOT NULL, user_id INTEGER NOT NULL,
  expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, author_id INTEGER NOT NULL, body BLOB NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor_id INTEGER, action TEXT NOT NULL,
  application_id INTEGER, document_id INTEGER, detail TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_docs_app ON documents(application_id, slot, current);
CREATE INDEX IF NOT EXISTS idx_app_borrower ON applications(borrower_id);
CREATE INDEX IF NOT EXISTS idx_audit_app ON audit(application_id, ts);
"""


def load_key(data_dir):
    raw = os.environ.get('NB_ENCRYPTION_KEY', '').strip()
    if raw:
        key = base64.b64decode(raw)
        if len(key) != 32:
            raise ValueError('NB_ENCRYPTION_KEY must be 32 bytes, base64-encoded.')
        return key
    path = Path(data_dir) / '.dev-key'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    log.warning('NB_ENCRYPTION_KEY is not set; using a development key file. Do not use this in production.')
    return path.read_bytes()


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f'scrypt${salt.hex()}${digest.hex()}'


def verify_password(password, stored):
    try:
        _, salt, digest = stored.split('$')
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(candidate.hex(), digest)
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, data_dir, key=None):
        self.dir = Path(data_dir)
        self.files = self.dir / 'files'
        self.files.mkdir(parents=True, exist_ok=True)
        self.aead = AESGCM(key or load_key(self.dir))
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.dir / 'northbridge.db', check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA)
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(applications)')}
        if 'stages' not in columns:  # migration for databases created before the pipeline view
            self.db.execute("ALTER TABLE applications ADD COLUMN stages TEXT NOT NULL DEFAULT '[]'")
        doc_columns = {row[1] for row in self.db.execute('PRAGMA table_info(documents)')}
        if 'filename_blob' not in doc_columns:  # encrypted original upload name for borrower document list
            self.db.execute('ALTER TABLE documents ADD COLUMN filename_blob BLOB')

    # ---- encryption -------------------------------------------------------
    def encrypt(self, data, context):
        nonce = secrets.token_bytes(12)
        return nonce + self.aead.encrypt(nonce, data, context.encode())

    def decrypt(self, blob, context):
        return self.aead.decrypt(blob[:12], blob[12:], context.encode())

    def put_file(self, data):
        """Encrypt and write a document. Returns its storage key (random, meaningless name)."""
        key = secrets.token_hex(16)
        (self.files / f'{key}.bin').write_bytes(self.encrypt(data, f'file:{key}'))
        return key

    def get_file(self, key):
        if not all(c in '0123456789abcdef' for c in key) or len(key) != 32:
            raise ValueError('bad storage key')
        return self.decrypt((self.files / f'{key}.bin').read_bytes(), f'file:{key}')

    # ---- queries ----------------------------------------------------------
    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchone()

    def run(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).lastrowid

    def audit(self, action, actor_id=None, application_id=None, document_id=None, detail='{}'):
        """IDs and action names only — never names, field values or document text."""
        self.run('INSERT INTO audit(ts,actor_id,action,application_id,document_id,detail) VALUES(?,?,?,?,?,?)',
                 (time.time(), actor_id, action, application_id, document_id, detail))

    # ---- users and sessions -----------------------------------------------
    def create_user(self, email, name, password, role):
        return self.run('INSERT INTO users(email,name,role,pw_hash,created) VALUES(?,?,?,?,?)',
                        (email.lower(), name, role, hash_password(password), time.time()))

    def new_session(self, user_id, ttl=8 * 3600):
        token = secrets.token_urlsafe(32)
        self.run('INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)', (token_hash(token), user_id, time.time() + ttl))
        return token

    def session_user(self, token):
        if not token:
            return None
        return self.one('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?',
                        (token_hash(token), time.time()))

    def end_session(self, token):
        self.run('DELETE FROM sessions WHERE token_hash=?', (token_hash(token or ''),))
