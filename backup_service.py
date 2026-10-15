"""Database backups: consistent copies kept next to the database."""
import sqlite3
from datetime import datetime
from pathlib import Path

KEEP = 10                                          # how many backups to keep
TS = "%Y-%m-%d %H:%M:%S"


def backup_dir(db_path):
    return Path(db_path).parent / "backups"


def create_backup(conn, db_path, keep=KEEP, now=None):
    """Copy the live database using SQLite's own backup API, which gives a
    consistent snapshot even while the app is running. Returns the new path."""
    now = now or datetime.now()
    folder = backup_dir(db_path)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / ("tallyo-%s.db" % now.strftime("%Y%m%d-%H%M%S"))
    target = sqlite3.connect(str(dest))
    try:
        conn.backup(target)
    finally:
        target.close()
    for old in sorted(folder.glob("tallyo-*.db"))[:-keep]:     # names sort by time
        old.unlink()
    return dest


def list_backups(db_path):
    """Newest first: [{'name', 'path', 'size_kb', 'taken_at'}]."""
    items = []
    for p in sorted(backup_dir(db_path).glob("tallyo-*.db"), reverse=True):
        stat = p.stat()
        items.append({"name": p.name, "path": p, "size_kb": max(stat.st_size // 1024, 1),
                      "taken_at": datetime.fromtimestamp(stat.st_mtime).strftime(TS)})
    return items


def status(db_path, now=None):
    """{'count', 'latest', 'age_days'} (age_days is None when there is no backup)."""
    items = list_backups(db_path)
    if not items:
        return {"count": 0, "latest": None, "age_days": None}
    now = now or datetime.now()
    taken = datetime.strptime(items[0]["taken_at"], TS)
    return {"count": len(items), "latest": items[0], "age_days": (now - taken).days}
