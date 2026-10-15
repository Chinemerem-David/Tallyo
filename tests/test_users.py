"""Users page, forced password change, resets and the safety rules."""
import os, re, sqlite3, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "users.db")

import db as dbm                   # noqa: E402
import seed as seeder              # noqa: E402
import user_service as us          # noqa: E402
from employee_service import ValidationError   # noqa: E402

DB = lambda: dbm.connect(os.environ["TALLYO_DATABASE"])


class Migration(unittest.TestCase):
    def test_old_database_gets_the_new_column_and_keeps_its_users(self):
        path = os.path.join(_TMP.name, "old.db")
        old = sqlite3.connect(path)
        old.executescript("""CREATE TABLE employees (id INTEGER PRIMARY KEY, employee_code TEXT, full_name TEXT,
            department TEXT, email TEXT, joined_on TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            full_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, employee_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1, last_login_at TEXT);
            INSERT INTO users (username, full_name, role, password_hash) VALUES ('admin','Admin','admin','x');""")
        old.close()
        dbm.ensure_schema(path)
        row = sqlite3.connect(path).execute("SELECT username, must_change_password FROM users").fetchone()
        self.assertEqual(row, ("admin", 0))


class Rules(unittest.TestCase):
    def setUp(self):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = DB()
        self.admin_id = self.db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]

    def invalid(self, **over):
        form = {"full_name": "Zed Zulu", "username": "zed", "role": "hr", **over}
        with self.assertRaises(ValidationError) as cm:
            us.validate_new(self.db, form)
        return cm.exception.errors

    def test_new_user_validation(self):
        self.assertIn("full_name", self.invalid(full_name="Z"))
        self.assertIn("username", self.invalid(username="Zed Zulu!"))
        self.assertIn("username", self.invalid(username="admin"))          # taken
        self.assertIn("username", self.invalid(username="emp-0142"))       # clashes with an employee ID
        self.assertIn("role", self.invalid(role="employee"))               # only staff roles here

    def test_create_user_has_a_temporary_password_that_must_be_changed(self):
        clean = us.validate_new(self.db, {"full_name": "Zed  Zulu", "username": "ZED", "role": "hr"})
        uid, pw = us.create_user(self.db, clean)
        row = self.db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        self.assertEqual((row["username"], row["full_name"], row["must_change_password"]), ("zed", "Zed Zulu", 1))
        self.assertEqual(len(pw), 8)

    def test_cannot_deactivate_yourself_or_the_last_admin(self):
        with self.assertRaisesRegex(us.UserError, "your own account"):
            us.update_user(self.db, self.admin_id, "Admin", False, acting_user_id=self.admin_id)
        other = self.db.execute("SELECT id FROM users WHERE username='chiamaka'").fetchone()[0]
        with self.assertRaisesRegex(us.UserError, "at least one active admin"):
            us.update_user(self.db, self.admin_id, "Admin", False, acting_user_id=other)   # only admin left
        clean = us.validate_new(self.db, {"full_name": "Second Admin", "username": "admin2", "role": "admin"})
        second, _ = us.create_user(self.db, clean)
        us.update_user(self.db, self.admin_id, "Admin", False, acting_user_id=second)      # fine with two
        self.assertEqual(self.db.execute("SELECT is_active FROM users WHERE id=?", (self.admin_id,)).fetchone()[0], 0)

    def test_change_password_rules(self):
        def bad(cur, new, conf):
            with self.assertRaises(ValidationError) as cm:
                us.change_password(self.db, self.admin_id, cur, new, conf)
            return cm.exception.errors
        self.assertIn("current", bad("wrong", "long-enough-1", "long-enough-1"))
        self.assertIn("new", bad("admin123", "short", "short"))
        self.assertIn("new", bad("admin123", "admin123", "admin123"))
        self.assertIn("new", bad("admin123", "ADMIN", "ADMIN"))
        self.assertIn("confirm", bad("admin123", "long-enough-1", "different-1"))
        us.change_password(self.db, self.admin_id, "admin123", "long-enough-1", "long-enough-1")
        self.assertEqual(self.db.execute("SELECT must_change_password FROM users WHERE id=?", (self.admin_id,)).fetchone()[0], 0)


class Pages(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])

    def token(self, c, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url, follow_redirects=True).get_data(as_text=True)).group(1)

    def login(self, role="admin", user="admin", pw="admin123"):
        c = self.app.test_client()
        c.post("/login", data={"csrf_token": self.token(c, "/login"), "role": role, "username": user, "password": pw})
        return c

    def add_hr(self, admin, username="zed"):
        r = admin.post("/admin/users/new", data={"csrf_token": self.token(admin, "/admin/users/new"), "full_name": "Zed Zulu",
                                                 "username": username, "role": "hr"}, follow_redirects=True)
        return re.search(r"Temporary password: (\w{8})\.", r.get_data(as_text=True)).group(1)

    def test_users_page_lists_staff_and_hides_employee_accounts(self):
        html = self.login().get("/admin/users").get_data(as_text=True)
        for text in ("Chiamaka Eze", "Tunde Bakare", "(you)", "48 employee accounts", "What each role can do"):
            self.assertIn(text, html)
        self.assertNotIn("EMP-0142", html)

    def test_new_hr_user_must_change_the_temporary_password_before_anything_else(self):
        admin = self.login()
        pw = self.add_hr(admin)
        zed = self.login("hr", "zed", pw)
        r = zed.get("/hr")
        self.assertTrue(r.headers["Location"].endswith("/account/password"))           # forced
        page = zed.get("/account/password").get_data(as_text=True)
        self.assertIn("temporary password", page)
        # wrong current password, then weak, then success
        url = "/account/password"
        r = zed.post(url, data={"csrf_token": self.token(zed, url), "current": "nope", "new": "brand-new-pw1", "confirm": "brand-new-pw1"})
        self.assertEqual(r.status_code, 422)
        r = zed.post(url, data={"csrf_token": self.token(zed, url), "current": pw, "new": "short", "confirm": "short"})
        self.assertIn("Use 8 to 128 characters", r.get_data(as_text=True))
        r = zed.post(url, data={"csrf_token": self.token(zed, url), "current": pw, "new": "brand-new-pw1", "confirm": "brand-new-pw1"},
                     follow_redirects=True)
        self.assertIn("Your password was changed", r.get_data(as_text=True))
        self.assertEqual(zed.get("/hr").status_code, 200)                              # now free to work
        self.assertEqual(self.login("hr", "zed", pw).get("/hr").status_code, 302)      # old password dead
        self.assertEqual(self.login("hr", "zed", "brand-new-pw1").get("/hr").status_code, 200)

    def test_admin_reset_forces_a_new_change_and_kills_the_old_password(self):
        admin = self.login()
        chiamaka = self.login("hr", "chiamaka", "hr123")
        self.assertEqual(chiamaka.get("/hr").status_code, 200)
        uid = DB().execute("SELECT id FROM users WHERE username='chiamaka'").fetchone()[0]
        r = admin.post("/admin/users/%d/reset-password" % uid, data={"csrf_token": self.token(admin, "/admin/users")}, follow_redirects=True)
        new_pw = re.search(r"Temporary password: (\w{8})\.", r.get_data(as_text=True)).group(1)
        self.assertEqual(chiamaka.get("/hr").headers["Location"].endswith("/account/password"), True)   # live session forced too
        self.assertEqual(self.login("hr", "chiamaka", "hr123").get("/hr").status_code, 302)             # old password fails
        self.assertTrue(self.login("hr", "chiamaka", new_pw).get("/hr").headers["Location"].endswith("/account/password"))

    def test_employee_added_by_admin_and_employee_reset_both_force_a_change(self):
        admin = self.login()
        r = admin.post("/admin/employees/new", data={"csrf_token": self.token(admin, "/admin/employees/new"), "employee_code": "EMP-0160",
                       "full_name": "Ada Lovelace", "department": "Support", "joined_on": "2026-01-05"}, follow_redirects=True)
        pw = re.search(r"Temporary password: (\w{8})\.", r.get_data(as_text=True)).group(1)
        ada = self.login("employee", "EMP-0160", pw)
        self.assertTrue(ada.get("/portal").headers["Location"].endswith("/account/password"))
        r = admin.post("/admin/employees/1/reset-password", data={"csrf_token": self.token(admin, "/admin/employees/1/edit")}, follow_redirects=True)
        self.assertIn("Password reset for Amara Okafor", r.get_data(as_text=True))
        self.assertEqual(self.login("employee", "EMP-0142", "emp123").get("/portal").status_code, 302)

    def test_edit_user_and_safety_messages_on_the_page(self):
        admin = self.login()
        tid = DB().execute("SELECT id FROM users WHERE username='tunde'").fetchone()[0]
        url = "/admin/users/%d/edit" % tid
        r = admin.post(url, data={"csrf_token": self.token(admin, url), "full_name": "Tunde B. Bakare", "is_active": "on"}, follow_redirects=True)
        self.assertIn("Tunde B. Bakare", r.get_data(as_text=True))
        own = "/admin/users/%d/edit" % DB().execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        r = admin.post(own, data={"csrf_token": self.token(admin, own), "full_name": "Admin"})          # unticked = deactivate
        self.assertEqual(r.status_code, 422)
        self.assertIn("can&#39;t deactivate your own account", r.get_data(as_text=True))
        # an inactive HR user is signed out at once
        hr = self.login("hr", "tunde", "hr123")
        self.assertEqual(hr.get("/hr").status_code, 200)
        admin.post(url, data={"csrf_token": self.token(admin, url), "full_name": "Tunde Bakare"})
        self.assertEqual(hr.get("/hr").status_code, 302)

    def test_access_rules(self):
        hr = self.login("hr", "chiamaka", "hr123")
        for url in ("/admin/users", "/admin/users/new", "/admin/users/1/edit"):
            self.assertEqual(hr.get(url).status_code, 403, url)
        self.assertEqual(hr.post("/admin/users/1/reset-password", data={}).status_code, 403)
        self.assertEqual(self.login().post("/admin/users/new", data={"full_name": "x"}).status_code, 400)   # no CSRF token
        self.assertEqual(self.login().get("/admin/users/9999/edit").status_code, 404)
        emp = self.login("employee", "EMP-0142", "emp123")
        self.assertEqual(emp.get("/account/password").status_code, 200)        # every role can change its password
        self.assertEqual(self.app.test_client().get("/account/password").status_code, 302)
        self.assertIn("Ask your administrator", self.app.test_client().get("/login?role=employee").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
