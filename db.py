"""SQLite connection helpers."""
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


def init_schema(conn):
    conn.executescript(SCHEMA_FILE.read_text())


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
    app.config.setdefault("DATABASE", str(DEFAULT_DB))
    ensure_schema(app.config["DATABASE"])     # creates empty tables on first run
    app.teardown_appcontext(close_db)
