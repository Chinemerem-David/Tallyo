"""HR dashboard: Today, Attendance, Reports, Employees. Read-only, no card details."""
import csv, io, os, re, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "hr.db")

import db as dbm        # noqa: E402
import queries          # noqa: E402
import seed as seeder   # noqa: E402

TODAY = date.today()
LAST_WORKDAY = TODAY - timedelta(days=1)
while LAST_WORKDAY.weekday() >= 5:
    LAST_WORKDAY -= timedelta(days=1)
LAST_SATURDAY = TODAY - timedelta(days=(TODAY.weekday() - 5) % 7 or 7)


class TodayNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        cls.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def test_matches_an_independent_count(self):
        d = LAST_WORKDAY.isoformat()
        people = self.db.execute("SELECT id FROM employees WHERE is_active = 1 AND joined_on <= ?", (d,)).fetchall()
        recs = {r[0]: r[1] for r in self.db.execute("SELECT employee_id, status FROM attendance WHERE work_date = ?", (d,))}
        res = queries.hr_today(self.db, LAST_WORKDAY)
        self.assertEqual(res["total"], len(people))
        self.assertEqual(res["present"], sum(1 for (i,) in people if i in recs))
        self.assertEqual(len(res["late"]), sum(1 for (i,) in people if recs.get(i) == "late"))
        self.assertEqual(len(res["not_in"]), sum(1 for (i,) in people if i not in recs))
        self.assertEqual(sum(x["present"] for x in res["departments"]), res["present"])
        self.assertEqual(sum(x["total"] for x in res["departments"]), res["total"])
        self.assertEqual(len(res["everyone"]), res["total"])
        stamps = [p["stamp"] for p in res["late"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_weekend_expects_nobody(self):
        res = queries.hr_today(self.db, LAST_SATURDAY)
        self.assertEqual((res["working_day"], res["not_in"]), (False, []))
        self.assertTrue(all(p["status"] == "no_work_today" for p in res["everyone"] if not p["time"]))

    def test_month_rate_per_employee(self):
        rates = queries.employee_month_rates(self.db, [1, 2, 3], TODAY)
        if TODAY.day == 1:
            self.assertEqual(rates, {})
            return
        have = {r[0] for r in self.db.execute("SELECT work_date FROM attendance WHERE employee_id = 1")}
        d, present, total = TODAY.replace(day=1), 0, 0
        while d < TODAY:
            if d.weekday() < 5:
                total += 1; present += d.isoformat() in have
            d += timedelta(days=1)
        if total:
            self.assertEqual(rates[1], round(100 * present / total))
        self.assertEqual(queries.employee_month_rates(self.db, [], TODAY), {})


class Pages(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])
        self.hr = self.login("hr", "chiamaka", "hr123")

    def login(self, role="admin", user="admin", pw="admin123"):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        r = c.post("/login", data={"csrf_token": tok, "role": role, "username": user, "password": pw})
        c.last_login = r
        return c

    def test_landing_page_menu_and_sidebar(self):
        self.assertTrue(self.hr.last_login.headers["Location"].endswith("/hr"))
        html = self.hr.get("/hr").get_data(as_text=True)
        for text in ("Late today", "Not in yet", "By department", "Export today", "view and export only"):
            self.assertIn(text, html)
        self.assertNotIn("Reader not connected", html)                    # hardware status is an admin matter
        for href in ("/hr", "/hr/attendance", "/hr/reports", "/hr/employees"):
            self.assertIn('href="%s"' % href, html)

    def test_today_export_lists_everyone(self):
        r = self.hr.get("/hr/today/export")
        rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["name", "employee_id", "department", "status", "check_in"])
        self.assertEqual(len(rows) - 1, 48)
        r.close()

    def test_attendance_is_read_only_and_exports_match_the_admin_ones(self):
        html = self.hr.get("/hr/attendance").get_data(as_text=True)
        for forbidden in (">Edit<", "Add entry", "+ Add manual entry", "/admin/"):
            self.assertNotIn(forbidden, html)
        self.assertIn("Export CSV", html)
        admin = self.login()
        for query in ("", "?status=late", "?q=amara&dept=Operations"):
            a, h = admin.get("/admin/attendance/export" + query), self.hr.get("/hr/attendance/export" + query)
            self.assertEqual(a.data, h.data, query)
            a.close(); h.close()

    def test_reports_show_the_same_numbers_as_the_admin_ones(self):
        month = (TODAY.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        admin = self.login()
        for path in ("/reports?month=" + month, "/reports/export?month=" + month):
            a, h = admin.get("/admin" + path), self.hr.get("/hr" + path)
            self.assertEqual(h.status_code, 200)
            if "export" in path:
                self.assertEqual(a.data, h.data)
            else:
                tiles = lambda r: re.findall(r'tile-value">([^<]+)<', r.get_data(as_text=True))
                self.assertEqual(tiles(a), tiles(h))
                self.assertNotIn("/admin/", h.get_data(as_text=True))
            a.close(); h.close()

    def test_employees_search_filter_and_detail(self):
        html = self.hr.get("/hr/employees?q=amara").get_data(as_text=True)
        self.assertIn("Amara Okafor", html)
        self.assertNotIn("Daniel Mensah", html)
        self.assertIn("Finance", self.hr.get("/hr/employees?dept=Finance").get_data(as_text=True))
        self.assertIn("Inactive 0", self.hr.get("/hr/employees").get_data(as_text=True))
        page2 = self.hr.get("/hr/employees?page=2").get_data(as_text=True)
        self.assertIn("Showing 11–20 of 48", page2)
        detail = self.hr.get("/hr/employees/1").get_data(as_text=True)
        for text in ("Amara Okafor", "amara.okafor@example.com", "The last 30 days", "On time"):
            self.assertIn(text, detail)
        self.assertEqual(self.hr.get("/hr/employees/9999").status_code, 404)

    def test_hr_never_sees_card_details(self):
        uids = [r[0] for r in self.db.execute("SELECT uid FROM cards")]
        rows = queries.list_employees(self.db, with_cards=False)["rows"]
        self.assertTrue(all(r["card_uid"] is None for r in rows))
        self.assertTrue(any(r["card_uid"] for r in queries.list_employees(self.db)["rows"]))      # admin still does
        pages = ["/hr", "/hr/attendance", "/hr/reports", "/hr/employees", "/hr/employees?status=no_card", "/hr/employees/1",
                 "/hr/employees/3", "/hr/attendance/export", "/hr/today/export"]
        for url in pages:
            r = self.hr.get(url)
            text = r.data.decode("utf-8-sig")
            for uid in uids:
                self.assertNotIn(uid, text, (url, uid))
            for word in ("Assign card", "No card", "Manage card"):
                self.assertNotIn(word, text, (url, word))
            r.close()
        self.assertIn("All 48", self.hr.get("/hr/employees?status=no_card").get_data(as_text=True))   # filter ignored

    def test_access_rules(self):
        admin, emp, anon = self.login(), self.login("employee", "EMP-0142", "emp123"), self.app.test_client()
        for url in ("/hr", "/hr/attendance", "/hr/reports", "/hr/employees", "/hr/employees/1", "/hr/today/export",
                    "/hr/attendance/export", "/hr/reports/export"):
            self.assertEqual(admin.get(url).status_code, 403, url)
            self.assertEqual(emp.get(url).status_code, 403, url)
            self.assertEqual(anon.get(url).status_code, 302, url)
        for url in ("/admin", "/admin/employees", "/admin/attendance", "/admin/reports", "/admin/attendance/new"):
            self.assertEqual(self.hr.get(url).status_code, 403, url)


if __name__ == "__main__":
    unittest.main()
