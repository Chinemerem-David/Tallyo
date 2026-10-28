"""Several programs share one database file: the web app, the reader program, the simulator.
A write by one must be visible to the others straight away (this once failed with WAL mode)."""
import os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.TemporaryDirectory()

import db as dbm        # noqa: E402
import reader           # noqa: E402


def run_in_other_process(code, path):
    """Runs a snippet in a separate Python process against the same database file."""
    result = subprocess.run([sys.executable, "-c", code, str(ROOT), path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class Sharing(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(_TMP.name, "share.db")
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(self.path + suffix).unlink(missing_ok=True)
        dbm.ensure_schema(self.path)
        self.conn = dbm.connect(self.path)                       # long-lived, like the reader program

    def test_uses_the_rollback_journal_not_wal(self):
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_an_old_wal_database_is_converted_when_opened(self):
        path = os.path.join(_TMP.name, "oldwal.db")
        c = sqlite3.connect(path); c.execute("PRAGMA journal_mode = WAL"); c.execute("CREATE TABLE t (x)"); c.close()
        self.assertEqual(dbm.connect(path).execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_the_web_apps_write_is_seen_by_a_long_running_reader_program(self):
        self.assertFalse(reader.listening(self.conn)["waiting"])
        run_in_other_process("import sys; sys.path.insert(0, sys.argv[1]); import db, reader; "
                             "reader.start_listening(db.connect(sys.argv[2]), 'register')", self.path)
        self.assertTrue(reader.listening(self.conn)["waiting"])           # the same connection, no reopening

    def test_the_reader_programs_write_is_seen_by_a_separate_web_process(self):
        reader.heartbeat(self.conn, "ok", "COM3")
        out = run_in_other_process("import sys; sys.path.insert(0, sys.argv[1]); import db, reader; "
                                   "print(reader.status(db.connect(sys.argv[2]))['label'])", self.path)
        self.assertEqual(out, "Reader connected")

    def test_a_tap_written_elsewhere_is_seen_by_the_kiosk_feed(self):
        import kiosk_service as ks
        start = ks.latest_id(self.conn)
        run_in_other_process("import sys; sys.path.insert(0, sys.argv[1]); import db, reader; "
                             "reader.handle_uid(db.connect(sys.argv[2]), 'FF EE DD CC')", self.path)
        self.assertGreater(ks.latest_id(self.conn), start)


if __name__ == "__main__":
    unittest.main()
