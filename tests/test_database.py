"""Run from the project folder:  python -m unittest discover -s tests -v"""
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "web.db")   # before importing app

import db as dbm                      # noqa: E402
import seed as seeder                 # noqa: E402
from services import normalize_uid, record_tap   # noqa: E402

AMARA, GRACE_NOCARD, KOFI_LOST = "A3 4F 91 0C", "EMP-0147", "B2 6D 05 F1"
DAY = "2030-01-07"                    # a Monday far from the seeded dates


def at(hhmm, sec=0):
    return datetime.fromisoformat("%s %s:%02d" % (DAY, hhmm, sec))


class TapRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(_TMP.name, "taps.db")
        seeder.seed(cls.path)

    def setUp(self):
        self.db = dbm.connect(self.path)
        self.db.execute("DELETE FROM attendance WHERE work_date = ?", (DAY,))
        self.db.execute("DELETE FROM tap_log WHERE tapped_at LIKE ?", (DAY + "%",))

    def tearDown(self):
        self.db.close()

    def rows(self):
        return self.db.execute("SELECT * FROM attendance WHERE work_date = ?", (DAY,)).fetchall()

    def test_unregistered_card_is_rejected_and_logged(self):
        r = record_tap(self.db, "FF FF FF FF", at("08:00"))
        self.assertEqual(r["result"], "unregistered")
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tap_log WHERE result='unregistered' AND tapped_at LIKE ?",
                                         (DAY + "%",)).fetchone()[0], 1)

    def test_lost_card_is_rejected(self):
        self.assertEqual(record_tap(self.db, KOFI_LOST, at("08:00"))["result"], "inactive_card")
        self.assertEqual(self.rows(), [])

    def test_check_in_on_time_then_late(self):
        r = record_tap(self.db, AMARA, at("08:40"))
        self.assertEqual((r["result"], r["status"]), ("check_in", "on_time"))
        other = self.db.execute("SELECT uid FROM cards WHERE status='active' AND uid <> ? LIMIT 1", (AMARA,)).fetchone()[0]
        r = record_tap(self.db, other, at("09:05"))
        self.assertEqual((r["result"], r["status"]), ("check_in", "late"))

    def test_double_tap_inside_window_is_duplicate(self):
        record_tap(self.db, AMARA, at("08:40", 0))
        r = record_tap(self.db, AMARA, at("08:40", 20))
        self.assertEqual(r["result"], "duplicate")
        self.assertIsNone(self.rows()[0]["check_out"])

    def test_second_tap_checks_out_third_is_already_scanned(self):
        record_tap(self.db, AMARA, at("08:40"))
        out = record_tap(self.db, AMARA, at("17:02"))
        self.assertEqual((out["result"], out["hours"]), ("check_out", "8h 22m"))
        again = record_tap(self.db, AMARA, at("17:30"))
        self.assertEqual(again["result"], "already_scanned")
        self.assertEqual(len(self.rows()), 1)           # still ONE row for the day

    def test_card_format_variants(self):
        for raw in ("a3:4f:91:0c", "A34F910C", "a3 4f 91 0c", "A3-4F-91-0C"):
            self.assertEqual(normalize_uid(raw), AMARA)
        for bad in ("", "XYZ", "A3 4F", "A3 4F 91 0C 11"):
            with self.assertRaises(ValueError):
                normalize_uid(bad)


class SchemaRules(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(os.path.join(_TMP.name, "schema.db"))
        dbm.init_schema(self.db)
        self.db.execute("INSERT OR IGNORE INTO employees (id, employee_code, full_name, department, joined_on)"
                        " VALUES (1,'EMP-1','Test One','Sales','2024-01-01')")

    def tearDown(self):
        self.db.close()
        for s in ("", "-wal", "-shm"):
            Path(os.path.join(_TMP.name, "schema.db" + s)).unlink(missing_ok=True)

    def test_one_active_card_per_employee(self):
        self.db.execute("INSERT INTO cards (uid, employee_id, status) VALUES ('AA AA AA AA',1,'active')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO cards (uid, employee_id, status) VALUES ('BB BB BB BB',1,'active')")

    def test_active_card_needs_an_owner(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO cards (uid, status) VALUES ('CC CC CC CC','active')")

    def test_no_duplicate_attendance_row(self):
        q = "INSERT INTO attendance (employee_id, work_date, check_in, status) VALUES (1,'2030-01-07','2030-01-07 08:00:00','on_time')"
        self.db.execute(q)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(q)

    def test_checkout_must_be_after_checkin(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO attendance (employee_id, work_date, check_in, check_out, status)"
                            " VALUES (1,'2030-01-08','2030-01-08 09:00:00','2030-01-08 08:00:00','on_time')")


class LoginUsesDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        from app import app
        app.config["TESTING"] = True
        cls.app = app

    def login(self, role, user, pw):
        c = self.app.test_client()
        token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        return c, c.post("/login", data={"csrf_token": token, "role": role, "username": user, "password": pw})

    def test_each_role_signs_in_to_its_own_home(self):
        for role, user, pw, home in (("admin", "admin", "admin123", "/admin"),
                                     ("hr", "Chiamaka", "hr123", "/hr"),
                                     ("employee", "emp-0142", "emp123", "/portal")):
            _, r = self.login(role, user, pw)
            self.assertTrue(r.headers["Location"].endswith(home), (role, r.status_code))

    def test_wrong_password_and_wrong_role_are_refused(self):
        self.assertEqual(self.login("admin", "admin", "nope")[1].status_code, 401)
        self.assertEqual(self.login("hr", "admin", "admin123")[1].status_code, 401)

    def test_hr_cannot_open_admin(self):
        c, _ = self.login("hr", "chiamaka", "hr123")
        self.assertEqual(c.get("/admin").status_code, 403)


class AdminOverview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(_TMP.name, "overview.db")
        seeder.seed(cls.path)
        cls.db = dbm.connect(cls.path)
        cls.today = datetime.now().strftime("%Y-%m-%d")

    def setUp(self):
        if datetime.now().weekday() >= 5:
            self.skipTest("seed data has no attendance on weekends")

    def test_tiles_add_up(self):
        import queries
        s = queries.overview_stats(self.db, self.today)
        self.assertEqual((s["total"], s["present"], s["late"], s["not_in"], s["no_card"]),
                         (48, 45, 4, 3, 2))

    def test_attention_lists_stranger_card_and_missing_cards(self):
        import queries
        titles = [i["title"] for i in queries.attention_items(self.db, self.today)]
        self.assertEqual(titles, ["Unregistered card tapped", "2 employees have no card"])

    def test_latest_taps_newest_first(self):
        import queries
        taps = queries.latest_taps(self.db, self.today)
        times = [t["time"] for t in taps]
        self.assertEqual(times, sorted(times, reverse=True))
        # Peter has no card (manual entry), so the newest CARD tap is Ruth at 09:08
        self.assertEqual(taps[0]["name"], "Ruth Mwangi")


class AdminPage(LoginUsesDatabase):
    def test_overview_page_renders_for_admin_only(self):
        c, _ = self.login("admin", "admin", "admin123")
        r = c.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Needs your attention", r.get_data(as_text=True))
        hr, _ = self.login("hr", "chiamaka", "hr123")
        self.assertEqual(hr.get("/admin").status_code, 403)


if __name__ == "__main__":
    unittest.main()
