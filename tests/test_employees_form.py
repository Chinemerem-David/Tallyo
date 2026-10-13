"""Part 2: add and edit employees."""
import os, re, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "form.db")

import db as dbm                  # noqa: E402
import employee_service as svc    # noqa: E402
import seed as seeder             # noqa: E402

GOOD = {"employee_code": "emp-0160", "full_name": "  Ada   Lovelace ", "department": "Support",
        "email": "ADA@Example.com", "joined_on": "2026-01-05"}


def form(**over):
    return {**GOOD, **over}


class Service(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        cls.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def errors(self, creating=True, **over):
        with self.assertRaises(svc.ValidationError) as cm:
            svc.validate(self.db, form(**over), creating)
        return cm.exception.errors

    def test_next_code(self):
        self.assertEqual(svc.next_code(self.db), "EMP-0160")

    def test_clean_values_are_tidied(self):
        c = svc.validate(self.db, form(), True)
        self.assertEqual((c["employee_code"], c["full_name"], c["email"]), ("EMP-0160", "Ada Lovelace", "ada@example.com"))

    def test_each_rule(self):
        self.assertIn("full_name", self.errors(full_name=" "))
        self.assertIn("department", self.errors(department=""))
        self.assertIn("email", self.errors(email="not-an-email"))
        self.assertIn("joined_on", self.errors(joined_on="2026-13-45"))
        future = (date.today() + timedelta(days=3)).isoformat()
        self.assertIn("joined_on", self.errors(joined_on=future))
        self.assertIn("employee_code", self.errors(employee_code="x y"))
        self.assertIn("employee_code", self.errors(employee_code="EMP-0142"))      # taken
        self.assertIn("employee_code", self.errors(employee_code="admin"))         # clashes with a login

    def test_email_is_optional_and_code_is_not_checked_when_editing(self):
        c = svc.validate(self.db, form(email="", employee_code="EMP-0142"), creating=False)
        self.assertIsNone(c["email"])


class Pages(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])         # fresh data for every test

    def login(self, role="admin", user="admin", pw="admin123"):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"csrf_token": tok, "role": role, "username": user, "password": pw})
        return c

    def token(self, c, url="/admin/employees/new"):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)

    def test_new_form_suggests_next_code(self):
        html = self.login().get("/admin/employees/new").get_data(as_text=True)
        self.assertIn('value="EMP-0160"', html)

    def test_invalid_post_keeps_what_was_typed_and_shows_errors(self):
        c = self.login()
        r = c.post("/admin/employees/new", data={**form(full_name="", email="bad"), "csrf_token": self.token(c)})
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 422)
        self.assertIn("Enter the full name", html)
        self.assertIn("doesn&#39;t look like an email", html)
        self.assertIn('value="Support"', html)

    def test_valid_post_creates_employee_and_a_working_login(self):
        c = self.login()
        r = c.post("/admin/employees/new", data={**form(), "csrf_token": self.token(c)}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("Ada Lovelace added", html)
        password = re.search(r"Temporary password: (\w{8})\.", html).group(1)
        conn = dbm.connect(os.environ["TALLYO_DATABASE"])
        user = conn.execute("SELECT * FROM users WHERE username = 'EMP-0160'").fetchone()
        self.assertEqual((user["role"], user["is_active"]), ("employee", 1))
        # the new employee can really sign in, on the Employee tab
        e = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', e.get("/login").get_data(as_text=True)).group(1)
        r = e.post("/login", data={"csrf_token": tok, "role": "employee", "username": "EMP-0160", "password": password})
        self.assertTrue(r.headers["Location"].endswith("/portal"))

    def test_duplicate_id_is_refused(self):
        c = self.login()
        r = c.post("/admin/employees/new", data={**form(employee_code="EMP-0142"), "csrf_token": self.token(c)})
        self.assertEqual(r.status_code, 422)
        self.assertIn("already in use", r.get_data(as_text=True))

    def test_post_without_csrf_token_is_rejected(self):
        self.assertEqual(self.login().post("/admin/employees/new", data=form()).status_code, 400)

    def test_edit_changes_details_but_never_the_id(self):
        c = self.login()
        url = "/admin/employees/1/edit"
        self.assertIn('value="EMP-0142"', c.get(url).get_data(as_text=True))
        c.post(url, data={**form(full_name="Amara O. Okafor", department="Finance", employee_code="HACKED",
                                 is_active="on"), "csrf_token": self.token(c, url)})
        row = dbm.connect(os.environ["TALLYO_DATABASE"]).execute(
            "SELECT e.employee_code, e.full_name, e.department, u.full_name AS uname FROM employees e "
            "JOIN users u ON u.employee_id = e.id WHERE e.id = 1").fetchone()
        self.assertEqual(tuple(row), ("EMP-0142", "Amara O. Okafor", "Finance", "Amara O. Okafor"))

    def test_deactivating_blocks_login_and_kicks_out_a_signed_in_session(self):
        admin = self.login()
        amara = self.login("employee", "EMP-0142", "emp123")
        self.assertEqual(amara.get("/portal").status_code, 200)             # signed in
        url = "/admin/employees/1/edit"                                      # (no "is_active" = off)
        admin.post(url, data={**form(), "csrf_token": self.token(admin, url)})
        self.assertEqual(amara.get("/portal").status_code, 302)             # signed out at once
        self.assertEqual(self.login("employee", "EMP-0142", "emp123").get("/portal").status_code, 302)
        self.assertIn("Inactive 1", admin.get("/admin/employees").get_data(as_text=True))
        # tapping their card no longer works either
        from services import record_tap
        conn = dbm.connect(os.environ["TALLYO_DATABASE"])
        self.assertEqual(record_tap(conn, "A3 4F 91 0C")["result"], "inactive_card")

    def test_hr_is_refused_and_unknown_employee_is_404(self):
        hr = self.login("hr", "chiamaka", "hr123")
        self.assertEqual(hr.get("/admin/employees/new").status_code, 403)
        self.assertEqual(hr.get("/admin/employees/1/edit").status_code, 403)
        self.assertEqual(self.login().get("/admin/employees/9999/edit").status_code, 404)


if __name__ == "__main__":
    unittest.main()
