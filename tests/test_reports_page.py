"""Reports page and CSV export."""
import csv, io, os, re, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "reppage.db")

import db as dbm        # noqa: E402
import queries          # noqa: E402
import seed as seeder   # noqa: E402

TODAY = date.today()
LAST_MONTH = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)


class Pages(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def login(self, role="admin", user="admin", pw="admin123"):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"csrf_token": tok, "role": role, "username": user, "password": pw})
        return c

    def test_last_month_page_has_numbers_chart_and_tables(self):
        month = LAST_MONTH.strftime("%Y-%m")
        html = self.login().get("/admin/reports?month=" + month).get_data(as_text=True)
        for text in (LAST_MONTH.strftime("%B %Y"), "Attendance rate", "Average arrival", "Late arrivals", "Absences",
                     "Attendance rate by day", "Most late arrivals", "By department", "Print / PDF", "Export CSV"):
            self.assertIn(text, html)
        days = queries.report_month(self.db, LAST_MONTH, TODAY)["days"]
        self.assertEqual(html.count('class="bar-col"'), len(days))
        self.assertIn("month=%s" % ((LAST_MONTH - timedelta(days=1)).strftime("%Y-%m")), html)     # previous link
        self.assertNotIn("is-disabled", html)                                                      # a later month exists

    def test_current_month_has_no_next_link_and_junk_months_fall_back(self):
        c = self.login()
        html = c.get("/admin/reports").get_data(as_text=True)
        self.assertIn(TODAY.strftime("%B %Y"), html)
        self.assertIn("is-disabled", html)
        for url in ("/admin/reports?month=junk", "/admin/reports?month=2099-01", "/admin/reports?month=2026-13",
                    "/admin/reports?month=%3Cscript%3E"):
            r = c.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn(TODAY.strftime("%B %Y"), r.get_data(as_text=True))

    def test_month_without_data_shows_a_message(self):
        html = self.login().get("/admin/reports?month=2020-01").get_data(as_text=True)
        self.assertIn("no finished working days with records in January 2020", html)
        self.assertNotIn('class="bar-col"', html)

    def test_csv_export(self):
        c = self.login()
        r = c.get("/admin/reports/export?month=" + LAST_MONTH.strftime("%Y-%m"))
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("tallyo-report-%s.csv" % LAST_MONTH.strftime("%Y-%m"), r.headers["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["date", "expected", "present", "late", "absent", "attendance_rate_percent"])
        rep = queries.report_month(self.db, LAST_MONTH, TODAY)
        self.assertEqual(len(rows) - 1, len(rep["days"]))
        self.assertEqual(sum(int(x[2]) for x in rows[1:]), rep["totals"]["present"])
        self.assertEqual(sum(int(x[3]) for x in rows[1:]), rep["totals"]["late"])
        r.close()

    def test_access_rules(self):
        hr = self.login("hr", "chiamaka", "hr123")
        for url in ("/admin/reports", "/admin/reports/export"):
            self.assertEqual(hr.get(url).status_code, 403, url)
            self.assertEqual(self.app.test_client().get(url).status_code, 302, url)
        self.assertIn('href="/admin/reports"', self.login().get("/admin").get_data(as_text=True))   # menu link is live


if __name__ == "__main__":
    unittest.main()
