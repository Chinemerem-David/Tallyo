"""Employee portal page: own data only, read-only, month navigation."""
import os, re, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "portal.db")

import db as dbm        # noqa: E402
import queries          # noqa: E402
import seed as seeder   # noqa: E402

TODAY = date.today()
THIS = TODAY.replace(day=1)
LAST = (THIS - timedelta(days=1)).replace(day=1)


class Portal(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])
        self.amara = self.login("employee", "EMP-0142", "emp123")

    def login(self, role, user, pw):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"csrf_token": tok, "role": role, "username": user, "password": pw})
        return c

    def html(self, url="/portal"):
        return self.amara.get(url).get_data(as_text=True)

    def test_page_shows_greeting_stats_calendar_and_toggle(self):
        h = self.html()
        for text in ("Hello,", "Amara.", "Days present", "Late arrivals", "Hours worked", TODAY.strftime("%B %Y"),
                     'class="cal-dow"', "On time", "Absent", "Light", "Dark"):
            self.assertIn(text, h)
        days = (THIS.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        self.assertEqual(h.count('role="gridcell"'), days.day)

    def test_numbers_on_the_page_match_the_calendar(self):
        cal = queries.employee_calendar(self.db, 1, LAST, TODAY)
        h = self.html("/portal?month=" + LAST.strftime("%Y-%m"))
        self.assertEqual(re.findall(r'stat-value">([^<]+)<', h), [str(cal["present"]), str(cal["late"]), cal["hours"]])
        self.assertIn(LAST.strftime("%B %Y"), h)

    def test_month_navigation_and_bad_input(self):
        current = self.html()
        self.assertIn("is-disabled", current)                                   # no later month, Today already here
        self.assertIn("month=%s" % LAST.strftime("%Y-%m"), current)             # previous link
        past = self.html("/portal?month=" + LAST.strftime("%Y-%m"))
        self.assertIn('aria-label="Next month"', past)
        self.assertIn('href="/portal"', past)                                   # the Today button is live
        for url in ("/portal?month=junk", "/portal?month=2099-01", "/portal?month=2026-13", "/portal?month=%3Cb%3E"):
            r = self.amara.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn(TODAY.strftime("%B %Y"), r.get_data(as_text=True))

    def test_cannot_browse_before_joining(self):
        self.db.execute("UPDATE employees SET joined_on = ? WHERE id = 1", (LAST.replace(day=10).isoformat(),))
        h = self.html("/portal?month=2020-01")
        self.assertIn(LAST.strftime("%B %Y"), h)                                # clamped to the month they joined
        self.assertIn("You joined on %s" % LAST.replace(day=10).isoformat(), h)
        self.assertEqual(h.count('aria-label="Previous month"'), 0)             # no earlier month to go to
        self.assertNotIn(">2019<", h)

    def test_shows_only_the_signed_in_persons_data(self):
        others = [r for r in self.db.execute("SELECT full_name, employee_code FROM employees WHERE id <> 1")]
        base = self.html()
        for url in ("/portal", "/portal?employee=2", "/portal?id=2&employee_id=2&user=chiamaka"):
            h = self.html(url)
            for name, code in others[:12]:
                self.assertNotIn(name, h, url)
                self.assertNotIn(code, h, url)
        self.assertEqual(re.findall(r'stat-value">([^<]+)<', self.html("/portal?employee=2")),
                         re.findall(r'stat-value">([^<]+)<', base))             # extra parameters change nothing

    def test_read_only_and_nothing_from_other_roles_leaks(self):
        h = self.html()
        uids = [r[0] for r in self.db.execute("SELECT uid FROM cards")]
        for forbidden in ("/admin", "/hr", "Edit", "Add entry", "Assign card", "Export", "<form class=\"card"):
            self.assertNotIn(forbidden, h, forbidden)
        for uid in uids:
            self.assertNotIn(uid, h)
        self.assertIn("view only", h)
        self.assertEqual(h.count('class="nav-link"') + h.count('class="nav-link" aria-current'), 1)    # one menu item
        for method in (self.amara.post, self.amara.put, self.amara.delete):
            self.assertEqual(method("/portal").status_code, 405)                # nothing can be changed here

    def test_access_rules(self):
        for role, user, pw in (("admin", "admin", "admin123"), ("hr", "chiamaka", "hr123")):
            self.assertEqual(self.login(role, user, pw).get("/portal").status_code, 403, role)
        self.assertEqual(self.app.test_client().get("/portal").status_code, 302)
        for url in ("/admin", "/admin/employees", "/hr", "/hr/employees", "/admin/attendance"):
            self.assertEqual(self.amara.get(url).status_code, 403, url)

    def test_employee_lands_on_the_portal_and_must_change_a_temporary_password_first(self):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        r = c.post("/login", data={"csrf_token": tok, "role": "employee", "username": "EMP-0142", "password": "emp123"})
        self.assertTrue(r.headers["Location"].endswith("/portal"))
        self.db.execute("UPDATE users SET must_change_password = 1 WHERE username = 'EMP-0142'")
        self.assertTrue(c.get("/portal").headers["Location"].endswith("/account/password"))


if __name__ == "__main__":
    unittest.main()
