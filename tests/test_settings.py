"""Settings: work rules, backups, reader status, backup reminder."""
import os, re, sqlite3, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "settings.db")

import backup_service as bs        # noqa: E402
import db as dbm                   # noqa: E402
import queries                     # noqa: E402
import seed as seeder              # noqa: E402
import settings_service as ss      # noqa: E402
from services import record_tap    # noqa: E402

GOOD = {"work_start": "08:30", "late_after": "09:00", "work_end": "17:00", "duplicate_window_seconds": "60"}


class Rules(unittest.TestCase):
    def setUp(self):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def errors(self, **over):
        with self.assertRaises(ss.SettingsError) as cm:
            ss.validate({**GOOD, **over})
        return cm.exception.errors

    def test_defaults_and_good_values(self):
        self.assertEqual(ss.get_settings(self.db), GOOD)
        self.assertEqual(ss.validate(GOOD), GOOD)

    def test_each_rule(self):
        self.assertIn("work_start", self.errors(work_start="8:30"))
        self.assertIn("late_after", self.errors(late_after="25:00"))
        self.assertIn("late_after", self.errors(late_after="08:00"))         # before work starts
        self.assertIn("work_end", self.errors(work_end="09:00"))             # not after the late time
        for bad in ("4", "601", "abc", ""):
            self.assertIn("duplicate_window_seconds", self.errors(duplicate_window_seconds=bad), bad)

    def test_a_saved_rule_changes_the_next_tap(self):
        day = datetime(2030, 1, 7, 8, 40)
        ss.save_settings(self.db, ss.validate({**GOOD, "late_after": "08:35"}))
        r = record_tap(self.db, "A3 4F 91 0C", day)
        self.assertEqual((r["result"], r["status"]), ("check_in", "late"))   # 08:40 is now late


class Backups(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(_TMP.name, "bk", "tallyo.db")
        seeder.seed(self.path)
        self.db = dbm.connect(self.path)
        for p in bs.backup_dir(self.path).glob("*.db"):
            p.unlink()

    def test_backup_is_a_complete_working_copy(self):
        dest = bs.create_backup(self.db, self.path)
        copy = sqlite3.connect(str(dest))
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM employees").fetchone()[0], 48)
        self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        copy.close()

    def test_only_the_newest_ten_are_kept(self):
        start = datetime(2026, 1, 1, 9, 0, 0)
        for i in range(12):
            bs.create_backup(self.db, self.path, now=start + timedelta(minutes=i))
        names = [b["name"] for b in bs.list_backups(self.path)]
        self.assertEqual(len(names), 10)
        self.assertEqual(names[0], "tallyo-20260101-091100.db")            # newest first
        self.assertNotIn("tallyo-20260101-090000.db", names)               # oldest pruned

    def test_status_and_reminder_rules(self):
        self.assertEqual(bs.status(self.path), {"count": 0, "latest": None, "age_days": None})
        titles = lambda st: [i["title"] for i in queries.attention_items(self.db, "2000-01-01", backup=st)]
        self.assertIn("No backup yet", titles(bs.status(self.path)))
        bs.create_backup(self.db, self.path)
        self.assertNotIn("No backup yet", titles(bs.status(self.path)))
        old = bs.status(self.path, now=datetime.now() + timedelta(days=9))
        self.assertTrue(any(t.startswith("Backup is 9 days old") for t in titles(old)))
        self.assertEqual([i["title"] for i in queries.attention_items(self.db, "2000-01-01")],
                         ["2 employees have no card"])                    # no backup arg: unchanged


class Pages(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])
        for p in bs.backup_dir(os.environ["TALLYO_DATABASE"]).glob("*.db"):
            p.unlink()

    def login(self, role="admin", user="admin", pw="admin123"):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"csrf_token": tok, "role": role, "username": user, "password": pw})
        return c

    def token(self, c, url="/admin/settings"):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)

    def test_page_shows_rules_reader_and_database(self):
        html = self.login().get("/admin/settings").get_data(as_text=True)
        for text in ('value="08:30"', 'value="09:00"', 'value="60"', "Not connected", "Back up now", "Never"):
            self.assertIn(text, html)

    def test_save_valid_and_invalid_rules(self):
        c = self.login()
        r = c.post("/admin/settings", data={**GOOD, "late_after": "09:15", "csrf_token": self.token(c)}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("Work rules saved", html)
        self.assertIn('value="09:15"', html)
        r = c.post("/admin/settings", data={**GOOD, "duplicate_window_seconds": "2", "csrf_token": self.token(c)})
        self.assertEqual(r.status_code, 422)
        self.assertIn("from 5 to 600", r.get_data(as_text=True))
        self.assertIn('value="2"', r.get_data(as_text=True))                  # typed value kept

    def test_backup_button_download_and_overview_reminder(self):
        c = self.login()
        self.assertIn("No backup yet", c.get("/admin").get_data(as_text=True))
        self.assertEqual(c.get("/admin/settings/backup/download").status_code, 404)
        r = c.post("/admin/settings/backup", data={"csrf_token": self.token(c)}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("Backup created: tallyo-", html)
        self.assertIn("Download latest", html)
        self.assertNotIn("No backup yet", c.get("/admin").get_data(as_text=True))
        d = c.get("/admin/settings/backup/download")
        self.assertEqual(d.status_code, 200)
        self.assertTrue(d.data.startswith(b"SQLite format 3"))
        d.close()

    def test_sidebar_tells_the_truth_and_access_rules_hold(self):
        c = self.login()
        self.assertIn("Reader not connected", c.get("/admin/employees").get_data(as_text=True))
        self.assertNotIn("Reader connected", c.get("/admin/cards").get_data(as_text=True))
        self.assertEqual(c.post("/admin/settings", data=GOOD).status_code, 400)           # no CSRF token
        self.assertEqual(c.post("/admin/settings/backup", data={}).status_code, 400)
        hr = self.login("hr", "chiamaka", "hr123")
        for url in ("/admin/settings", "/admin/settings/backup/download"):
            self.assertEqual(hr.get(url).status_code, 403, url)
        self.assertEqual(hr.post("/admin/settings/backup", data={}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
