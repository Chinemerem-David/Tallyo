"""SQLite connection helpers."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "instance" / "tallyo.db"
SCHEMA_FILE = BASE_DIR / "schema.sql"


def connect(path=DEFAULT_DB):
    """Open a connection. Scripts and background threads (like the Arduino
    reader later) call this directly. A connection must stay in one thread."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)  # we run transactions ourselves
    conn.row_factory = sqlite3.Row            # rows behave like dicts: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite ignores foreign keys unless asked
    conn.execute("PRAGMA journal_mode = WAL") # readers don't block the writer
    return conn


@contextmanager
def transaction(conn):
    """All-or-nothing block. BEGIN IMMEDIATE takes the write lock first, so two
    simultaneous taps can't both read the same state and both write."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _migrate(conn):
    """Bring a database made by an older version up to date (adds new columns)."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "must_change_password" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")


def init_schema(conn):
    conn.executescript(SCHEMA_FILE.read_text())
    _migrate(conn)


def ensure_schema(path):
    conn = connect(path)
    init_schema(conn)
    conn.close()


# ---- Flask integration: one connection per request ---------------------------

def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    app.config.setdefault("DATABASE", os.environ.get("TALLYO_DATABASE", str(DEFAULT_DB)))
    ensure_schema(app.config["DATABASE"])     # creates empty tables on first run
    app.teardown_appcontext(close_db)
