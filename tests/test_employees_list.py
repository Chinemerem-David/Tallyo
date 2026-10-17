"""Part 1: employees list, search, filters, pagination."""
import os, re, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "list.db")

import db as dbm            # noqa: E402
import queries              # noqa: E402
import seed as seeder       # noqa: E402


class ListQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        cls.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def test_default_shows_all_active(self):
        self.assertEqual(queries.list_employees(self.db)["total"], 48)

    def test_search_by_name_and_code_ignores_case(self):
        self.assertEqual([r["full_name"] for r in queries.list_employees(self.db, q="amara")["rows"]], ["Amara Okafor"])
        self.assertEqual(queries.list_employees(self.db, q="emp-0142")["total"], 1)

    def test_percent_sign_is_not_a_wildcard(self):
        self.assertEqual(queries.list_employees(self.db, q="%")["total"], 0)

    def test_sql_injection_text_is_harmless(self):
        self.assertEqual(queries.list_employees(self.db, q="'; DROP TABLE employees;--")["total"], 0)
        self.assertEqual(queries.list_employees(self.db)["total"], 48)

    def test_department_and_status_filters(self):
        self.assertEqual(queries.list_employees(self.db, dept="Finance")["total"], 9)
        no_card = queries.list_employees(self.db, status="no_card")
        self.assertEqual(sorted(r["full_name"] for r in no_card["rows"]), ["Grace Adeyemi", "Peter Nwosu"])
        self.assertEqual(queries.list_employees(self.db, status="inactive")["total"], 0)

    def test_pagination(self):
        p1 = queries.list_employees(self.db, page=1)
        self.assertEqual((len(p1["rows"]), p1["pages"], p1["first"], p1["last"]), (10, 5, 1, 10))
        last = queries.list_employees(self.db, page=99)           # clamped to the last page
        self.assertEqual((last["page"], len(last["rows"])), (5, 8))
        self.assertEqual(queries.page_numbers(1, 5), [1, 2, 3, 4, 5])
        self.assertEqual(queries.page_numbers(5, 12), [1, None, 4, 5, 6, None, 12])

    def test_counts(self):
        self.assertEqual(queries.employee_counts(self.db), {"active": 48, "inactive": 0, "no_card": 2})


class ListPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["TESTING"] = True
        cls.app = app

    def login(self, role, user, pw):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"csrf_token": tok, "role": role, "username": user, "password": pw})
        return c

    def test_admin_sees_page_hr_is_refused(self):
        admin = self.login("admin", "admin", "admin123")
        html = admin.get("/admin/employees").get_data(as_text=True)
        self.assertIn("Employees", html)
        self.assertIn("48 people", html)
        self.assertEqual(self.login("hr", "chiamaka", "hr123").get("/admin/employees").status_code, 403)
        self.assertEqual(self.app.test_client().get("/admin/employees").status_code, 302)

    def test_search_and_filter_through_the_url(self):
        admin = self.login("admin", "admin", "admin123")
        html = admin.get("/admin/employees?q=grace&status=no_card").get_data(as_text=True)
        self.assertIn("Grace Adeyemi", html)
        self.assertNotIn("Amara Okafor", html)
        self.assertIn("No employees match", admin.get("/admin/employees?q=zzzz").get_data(as_text=True))

    def test_junk_parameters_do_not_crash(self):
        admin = self.login("admin", "admin", "admin123")
        for url in ("/admin/employees?page=abc", "/admin/employees?page=-5", "/admin/employees?status=hack"):
            self.assertEqual(admin.get(url).status_code, 200, url)

    def test_menu_link_is_live_and_unbuilt_pages_stay_dead(self):
        html = self.login("admin", "admin", "admin123").get("/admin").get_data(as_text=True)
        self.assertIn('href="/admin/employees"', html)
        self.assertIn('href="/admin/cards"', html)
        self.assertIn('href="/admin/users"', html)
        self.assertIn('href="/admin/attendance"', html)
        self.assertIn('href="/admin/reports"', html)
        from shell import safe_url
        with self.app.test_request_context():
            self.assertEqual(safe_url("admin.page_that_does_not_exist"), "#")     # unbuilt pages stay harmless


if __name__ == "__main__":
    unittest.main()
