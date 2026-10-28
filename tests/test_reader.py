"""Reader status, 'waiting for a card' mode, and the single place a card ID is handled."""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "reader.db")

import db as dbm        # noqa: E402
import reader           # noqa: E402
import seed as seeder   # noqa: E402

T0 = datetime(2030, 1, 7, 8, 40, 0)
AMARA, KOFI_LOST, SPARE, NEW = "A3 4F 91 0C", "B2 6D 05 F1", "C9 18 7E 40", "11 22 33 44"


class Base(unittest.TestCase):
    def setUp(self):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])


class Status(Base):
    def test_no_signal_means_not_connected(self):
        s = reader.status(self.db, T0)
        self.assertEqual((s["connected"], s["state"], s["label"]), (False, "disconnected", "Reader not connected"))

    def test_fresh_ok_is_connected_and_shows_the_port(self):
        reader.heartbeat(self.db, "ok", "COM3", "rc522 v0x92", T0)
        s = reader.status(self.db, T0 + timedelta(seconds=3))
        self.assertEqual((s["connected"], s["label"], s["port"]), (True, "Reader connected", "COM3"))

    def test_old_signal_goes_stale(self):
        reader.heartbeat(self.db, "ok", "COM3", None, T0)
        self.assertTrue(reader.status(self.db, T0 + timedelta(seconds=reader.STALE_SECONDS))["connected"])
        self.assertFalse(reader.status(self.db, T0 + timedelta(seconds=reader.STALE_SECONDS + 1))["connected"])

    def test_fault_and_unplugged_are_told_apart(self):
        reader.heartbeat(self.db, "fault", "COM3", None, T0)
        f = reader.status(self.db, T0)
        self.assertEqual((f["connected"], f["state"], f["label"]), (False, "fault", "Reader fault: check wiring"))
        reader.heartbeat(self.db, "disconnected", None, "USB cable unplugged?", T0)
        u = reader.status(self.db, T0)
        self.assertEqual((u["state"], u["detail"]), ("disconnected", "USB cable unplugged?"))


class Listening(Base):
    def test_waiting_captures_the_next_tap_and_records_no_attendance(self):
        before = (self.db.execute("SELECT COUNT(*) FROM tap_log").fetchone()[0], self.db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0])
        reader.start_listening(self.db, "register", now=T0)
        self.assertTrue(reader.listening(self.db, T0)["waiting"])
        out = reader.handle_uid(self.db, "11:22:33:44", T0 + timedelta(seconds=5))
        self.assertEqual((out["mode"], out["uid"], out["feedback"]), ("captured", NEW, "ok"))
        state = reader.listening(self.db, T0 + timedelta(seconds=6))
        self.assertEqual((state["waiting"], state["uid"], state["purpose"]), (False, NEW, "register"))
        after = (self.db.execute("SELECT COUNT(*) FROM tap_log").fetchone()[0], self.db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0])
        self.assertEqual(before, after)                                          # nothing recorded
        self.assertEqual(reader.handle_uid(self.db, AMARA, T0 + timedelta(seconds=7))["mode"], "tap")   # back to normal

    def test_window_expires_cancel_works_and_bad_purpose_is_refused(self):
        reader.start_listening(self.db, "test", seconds=30, now=T0)
        self.assertEqual(reader.listening(self.db, T0 + timedelta(seconds=10))["seconds_left"], 20)
        self.assertFalse(reader.listening(self.db, T0 + timedelta(seconds=31))["waiting"])       # expired
        self.assertEqual(reader.handle_uid(self.db, AMARA, T0 + timedelta(seconds=40))["mode"], "tap")
        reader.start_listening(self.db, "register", now=T0)
        reader.stop_listening(self.db)
        self.assertEqual(reader.listening(self.db, T0)["waiting"], False)
        with self.assertRaises(ValueError):
            reader.start_listening(self.db, "anything-else")

    def test_a_new_listen_forgets_the_previous_capture(self):
        reader.start_listening(self.db, "register", now=T0)
        reader.handle_uid(self.db, NEW, T0)
        reader.start_listening(self.db, "register", now=T0 + timedelta(seconds=1))
        self.assertEqual(reader.listening(self.db, T0 + timedelta(seconds=2))["uid"], None)

    def test_notes_for_every_kind_of_card(self):
        note = lambda uid: reader.card_note(self.db, uid)
        self.assertIn("new card", note(NEW))
        self.assertIn("spare", note(SPARE))
        self.assertIn("Amara Okafor (EMP-0142)", note(AMARA))
        self.assertIn("lost", note(KOFI_LOST))
        self.assertIn("Kofi Asante", note(KOFI_LOST))


class HandleUid(Base):
    def test_normal_taps_and_their_feedback(self):
        cases = [(AMARA, "tap", "check_in", "ok"), (AMARA, "tap", "duplicate", "again"),
                 ("FF EE DD CC", "tap", "unregistered", "denied"), (KOFI_LOST, "tap", "inactive_card", "denied")]
        for uid, mode, result, feedback in cases:
            t = T0 + timedelta(seconds=cases.index((uid, mode, result, feedback)))
            out = reader.handle_uid(self.db, uid, t)
            self.assertEqual((out["mode"], out["result"], out["feedback"]), (mode, result, feedback), result)

    def test_garbage_is_refused_without_touching_the_database(self):
        n = self.db.execute("SELECT COUNT(*) FROM tap_log").fetchone()[0]
        for junk in ("", "hello", "ZZ ZZ ZZ ZZ", "A3 4F", None):
            self.assertEqual(reader.handle_uid(self.db, junk, T0), {"mode": "invalid", "feedback": "denied"})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tap_log").fetchone()[0], n)

    def test_old_databases_get_the_table_automatically(self):
        path = os.path.join(_TMP.name, "olddb.db")
        import sqlite3
        c = sqlite3.connect(path); c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"); c.close()
        dbm.ensure_schema(path)
        self.assertEqual(sqlite3.connect(path).execute("SELECT id, state FROM reader_status").fetchall(), [(1, "disconnected")])


if __name__ == "__main__":
    unittest.main()
