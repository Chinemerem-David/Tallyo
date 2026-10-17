"""Attendance pages: log, filters, CSV export, manual entry, edit, delete, access."""
import csv, io, os, re, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "attpage.db")

import db as dbm        # noqa: E402
import seed as seeder   # noqa: E402

TODAY = date.today()
SATURDAY = TODAY - timedelta(days=(TODAY.weekday() - 5) % 7 or 7)       # most recent Saturday: no seeded records


class Pages(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def login(self, role="admin", user="admin", pw="admin123"):
        c = self.app.test_client()
        c.post("/login", data={"csrf_token": self.token(c, "/login"), "role": role, "username": user, "password": pw})
        return c

    def token(self, c, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)

    def test_log_page_shows_summary_filters_and_rows(self):
        html = self.login().get("/admin/attendance").get_data(as_text=True)
        for text in ("Attendance", "records", "of arrivals on time", "Export CSV", "+ Add manual entry", "Apply",
                     "Absent means a working day"):
            self.assertIn(text, html)
        self.assertRegex(html, r"All \d+")
        absent = self.login().get("/admin/attendance?status=absent").get_data(as_text=True)
        self.assertIn("Add entry", absent)                             # absent rows offer a fix

    def test_filters_through_the_url_and_junk_is_safe(self):
        c = self.login()
        late = c.get("/admin/attendance?status=late").get_data(as_text=True)
        self.assertIn("Late", late)
        self.assertNotIn(">On time</span></td>", late.split("<tbody>")[1].split("</tbody>")[0])
        self.assertIn("No records match", c.get("/admin/attendance?q=zzzz").get_data(as_text=True))
        for url in ("/admin/attendance?from=junk&to=junk", "/admin/attendance?page=abc", "/admin/attendance?status=hack",
                    "/admin/attendance?from=2000-01-01", "/admin/attendance?to=2099-01-01"):
            self.assertEqual(c.get(url).status_code, 200, url)
        self.assertIn("latest 92 days", c.get("/admin/attendance?from=2000-01-01").get_data(as_text=True))

    def test_csv_export_matches_the_filters_and_defuses_formulas(self):
        c = self.login()
        self.db.execute("UPDATE attendance SET note = '=HYPERLINK(\"http://x\")' WHERE id = (SELECT id FROM attendance ORDER BY work_date DESC, id LIMIT 1)")
        r = c.get("/admin/attendance/export?status=late")
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("tallyo-attendance-", r.headers["Content-Disposition"])
        text = r.data.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], ["date", "employee_id", "name", "department", "check_in", "check_out", "hours", "status", "source", "note"])
        self.assertTrue(all(row[7] == "late" for row in rows[1:]))
        from queries import list_attendance, resolve_range
        s, e, _ = resolve_range(None, None, TODAY)
        self.assertEqual(len(rows) - 1, list_attendance(self.db, s, e, TODAY, status="late")["total"])   # every row, not one page
        everything = c.get("/admin/attendance/export").data.decode("utf-8-sig")
        self.assertNotIn(",=HYPERLINK", everything)
        self.assertIn("'=HYPERLINK", everything)
        r.close()

    def test_add_manual_entry_from_an_absent_row(self):
        c = self.login()
        absent = self.db.execute("""SELECT e.id, d FROM employees e, (SELECT date('now','localtime','-1 day') AS d) WHERE 0""").fetchall()
        form_url = "/admin/attendance/new?employee=3&date=%s" % SATURDAY.isoformat()
        page = c.get(form_url).get_data(as_text=True)
        self.assertIn('value="%s"' % SATURDAY.isoformat(), page)
        r = c.post("/admin/attendance/new", data={"csrf_token": self.token(c, form_url), "employee_id": "3",
                   "work_date": SATURDAY.isoformat(), "check_in": "09:20", "check_out": "", "reason": "Weekend shift, forgot card"},
                   follow_redirects=True)
        self.assertIn("Entry added for Grace Adeyemi", r.get_data(as_text=True))
        row = self.db.execute("SELECT status, source, note FROM attendance WHERE employee_id=3 AND work_date=?", (SATURDAY.isoformat(),)).fetchone()
        self.assertEqual(tuple(row), ("late", "manual", "Weekend shift, forgot card"))

    def test_invalid_entry_keeps_input_and_duplicate_is_refused(self):
        c = self.login(); url = "/admin/attendance/new"
        r = c.post(url, data={"csrf_token": self.token(c, url), "employee_id": "3", "work_date": SATURDAY.isoformat(),
                              "check_in": "bad", "check_out": "", "reason": ""})
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 422)
        self.assertIn("Enter the check-in time", html)
        self.assertIn("Say why", html)
        self.assertIn('value="%s"' % SATURDAY.isoformat(), html)
        good = {"csrf_token": self.token(c, url), "employee_id": "3", "work_date": SATURDAY.isoformat(),
                "check_in": "09:00", "check_out": "", "reason": "test"}
        c.post(url, data=good)
        again = c.post(url, data={**good, "csrf_token": self.token(c, url)})
        self.assertEqual(again.status_code, 422)
        self.assertIn("already has a record", again.get_data(as_text=True))

    def test_edit_record_requires_a_reason_and_updates_it(self):
        c = self.login()
        rec = self.db.execute("SELECT id FROM attendance WHERE employee_id = 1 ORDER BY work_date DESC LIMIT 1").fetchone()[0]
        url = "/admin/attendance/%d/edit" % rec
        self.assertIn("Delete this record", c.get(url).get_data(as_text=True))
        r = c.post(url, data={"csrf_token": self.token(c, url), "check_in": "08:00", "check_out": "17:00", "reason": ""})
        self.assertEqual(r.status_code, 422)
        r = c.post(url, data={"csrf_token": self.token(c, url), "check_in": "09:45", "check_out": "17:10", "reason": "Fixed from sheet"},
                   follow_redirects=True)
        self.assertIn("updated", r.get_data(as_text=True))
        row = self.db.execute("SELECT status, source, note FROM attendance WHERE id=?", (rec,)).fetchone()
        self.assertEqual(tuple(row), ("late", "manual", "Fixed from sheet"))
        self.assertIn("Manual", c.get("/admin/attendance?q=emp-0142").get_data(as_text=True))

    def test_delete_record_makes_that_day_absent_again(self):
        c = self.login()
        past = self.db.execute("""SELECT id, work_date FROM attendance WHERE employee_id = 1 AND work_date < ?
                                  AND strftime('%w', work_date) NOT IN ('0','6') ORDER BY work_date DESC LIMIT 1""", (TODAY.isoformat(),)).fetchone()
        url = "/admin/attendance/%d/edit" % past["id"]
        r = c.post("/admin/attendance/%d/delete" % past["id"], data={"csrf_token": self.token(c, url)}, follow_redirects=True)
        self.assertIn("deleted", r.get_data(as_text=True))
        from queries import list_attendance
        d = date.fromisoformat(past["work_date"])
        absent = list_attendance(self.db, d, d, TODAY, q="emp-0142", status="absent")
        self.assertEqual(absent["total"], 1)

    def test_access_rules(self):
        c = self.login()
        hr = self.login("hr", "chiamaka", "hr123")
        for url in ("/admin/attendance", "/admin/attendance/new", "/admin/attendance/1/edit", "/admin/attendance/export"):
            self.assertEqual(hr.get(url).status_code, 403, url)
        for url in ("/admin/attendance/new", "/admin/attendance/1/edit", "/admin/attendance/1/delete"):
            self.assertEqual(c.post(url, data={}).status_code, 400, url)                # no CSRF token
            self.assertEqual(hr.post(url, data={}).status_code, 403, url)
        self.assertEqual(c.get("/admin/attendance/999999/edit").status_code, 404)
        self.assertEqual(self.app.test_client().get("/admin/attendance").status_code, 302)


if __name__ == "__main__":
    unittest.main()
