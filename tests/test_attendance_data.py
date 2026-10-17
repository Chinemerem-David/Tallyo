"""Attendance data layer: derived absences, filters, range rules, edit validation."""
import os, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "att.db")

import attendance_service as att      # noqa: E402
import db as dbm                      # noqa: E402
import queries                        # noqa: E402
import seed as seeder                 # noqa: E402
from employee_service import ValidationError   # noqa: E402

TODAY = date.today()


class Range(unittest.TestCase):
    def test_defaults_bad_input_cap_and_future(self):
        s, e, trimmed = queries.resolve_range(None, None, TODAY)
        self.assertEqual((e, (e - s).days, trimmed), (TODAY, 29, False))
        self.assertEqual(queries.resolve_range("junk", "also junk", TODAY)[:2], (TODAY - timedelta(days=29), TODAY))
        s, e, trimmed = queries.resolve_range("2020-01-01", TODAY.isoformat(), TODAY)
        self.assertEqual(((e - s).days + 1, trimmed), (92, True))
        self.assertEqual(queries.resolve_range(None, "2099-01-01", TODAY)[1], TODAY)       # no future end
        s, e, _ = queries.resolve_range(TODAY.isoformat(), "2020-01-01", TODAY)
        self.assertLessEqual(s, e)                                                         # from can't pass to


class Absences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        cls.db = dbm.connect(os.environ["TALLYO_DATABASE"])
        cls.start = TODAY - timedelta(days=29)

    def expected_absences(self):
        """Independent Python version of the SQL rule."""
        have = {(r[0], r[1]) for r in self.db.execute("SELECT employee_id, work_date FROM attendance")}
        count = 0
        for emp_id, joined in self.db.execute("SELECT id, joined_on FROM employees WHERE is_active = 1"):
            d = self.start
            while d < TODAY:
                if d.weekday() < 5 and d.isoformat() >= joined and (emp_id, d.isoformat()) not in have:
                    count += 1
                d += timedelta(days=1)
        return count

    def test_sql_absences_match_the_python_calculation(self):
        res = queries.list_attendance(self.db, self.start, TODAY, TODAY)
        self.assertEqual(res["counts"]["absent"], self.expected_absences())
        self.assertGreater(res["counts"]["absent"], 0)

    def test_people_who_have_not_arrived_today_are_not_called_absent(self):
        if TODAY.weekday() >= 5:
            self.skipTest("weekend")
        res = queries.list_attendance(self.db, TODAY, TODAY, TODAY, status="absent")
        self.assertEqual(res["total"], 0)

    def test_counts_add_up_and_filters_work(self):
        res = queries.list_attendance(self.db, self.start, TODAY, TODAY)
        c = res["counts"]
        self.assertEqual(c["all"], c["on_time"] + c["late"] + c["absent"])
        late = queries.list_attendance(self.db, self.start, TODAY, TODAY, status="late")
        self.assertTrue(all(r["status"] == "late" for r in late["rows"]))
        self.assertEqual(late["total"], c["late"])
        self.assertEqual(late["counts"]["all"], c["all"])               # chips show the full picture
        fin = queries.list_attendance(self.db, self.start, TODAY, TODAY, dept="Finance")
        self.assertTrue(all(r["department"] == "Finance" for r in fin["rows"]))
        amara = queries.list_attendance(self.db, self.start, TODAY, TODAY, q="emp-0142")
        self.assertTrue(amara["total"] > 0 and all(r["employee_code"] == "EMP-0142" for r in amara["rows"]))
        self.assertEqual(queries.list_attendance(self.db, self.start, TODAY, TODAY, q="%")["total"], 0)

    def test_pagination_and_ordering(self):
        p1 = queries.list_attendance(self.db, self.start, TODAY, TODAY)
        self.assertEqual(len(p1["rows"]), 12)
        dates = [r["work_date"] for r in p1["rows"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        everything = queries.list_attendance(self.db, self.start, TODAY, TODAY, paginate=False)
        self.assertEqual(len(everything["rows"]), p1["total"])

    def test_row_text_hours_missing_and_still_in(self):
        row = queries._decorate({"full_name": "Amara Okafor", "work_date": "2026-10-09", "check_in": "2026-10-09 08:35:00",
                                 "check_out": "2026-10-09 17:06:30"}, "2026-10-13")
        self.assertEqual((row["initials"], row["in_time"], row["out_time"], row["hours"]), ("AO", "08:35", "17:06", "8h 31m"))
        forgot = queries._decorate({"full_name": "A B", "work_date": "2026-10-09", "check_in": "2026-10-09 08:35:00", "check_out": None}, "2026-10-13")
        today_ = queries._decorate({"full_name": "A B", "work_date": "2026-10-13", "check_in": "2026-10-13 08:35:00", "check_out": None}, "2026-10-13")
        self.assertEqual((forgot["out_time"], today_["out_time"]), ("Missing", "Still in"))


class Editing(unittest.TestCase):
    def setUp(self):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def form(self, **over):
        return {"employee_id": "3", "work_date": "2026-10-05", "check_in": "08:40", "check_out": "17:00",
                "reason": "Forgot card", **over}

    def errors(self, existing=None, **over):
        with self.assertRaises(ValidationError) as cm:
            att.validate(self.db, self.form(**over), existing, today=date(2026, 10, 14))
        return cm.exception.errors

    def test_rules(self):
        self.assertIn("employee_id", self.errors(employee_id="9999"))
        self.assertIn("work_date", self.errors(work_date="2026-10-15"))           # future
        self.assertIn("check_in", self.errors(check_in="8:40"))
        self.assertIn("check_out", self.errors(check_out="08:00"))                # before check-in
        self.assertIn("check_out", self.errors(check_out="25:00"))
        self.assertIn("reason", self.errors(reason=" "))

    def test_status_follows_the_late_rule_and_checkout_is_optional(self):
        on_time = att.validate(self.db, self.form(check_out=""), today=date(2026, 10, 14))
        self.assertEqual((on_time["status"], on_time["check_out"]), ("on_time", None))
        self.assertEqual(att.validate(self.db, self.form(check_in="09:01"), today=date(2026, 10, 14))["status"], "late")

    def test_create_then_duplicate_is_refused_and_inactive_employee_too(self):
        saturday = self.form(work_date="2026-10-10")                       # no seeded record on a Saturday
        clean = att.validate(self.db, saturday, today=date(2026, 10, 14))
        att.create_record(self.db, clean)
        row = self.db.execute("SELECT source, note FROM attendance WHERE employee_id=3 AND work_date='2026-10-10'").fetchone()
        self.assertEqual(tuple(row), ("manual", "Forgot card"))
        with self.assertRaises(ValidationError) as cm:
            att.create_record(self.db, clean)
        self.assertIn("already has a record", cm.exception.errors["employee_id"])
        self.db.execute("UPDATE employees SET is_active = 0 WHERE id = 3")
        self.assertIn("employee_id", self.errors(work_date="2026-10-10"))

    def test_update_marks_it_manual_and_recomputes_status(self):
        rec = self.db.execute("SELECT * FROM attendance WHERE employee_id = 1 ORDER BY work_date DESC LIMIT 1").fetchone()
        form = self.form(check_in="09:30", check_out="17:30", reason="Corrected from the sign-in sheet")
        clean = att.validate(self.db, form, existing=rec, today=date.today())
        att.update_record(self.db, rec["id"], clean)
        row = self.db.execute("SELECT * FROM attendance WHERE id = ?", (rec["id"],)).fetchone()
        self.assertEqual((row["status"], row["source"], row["check_in"][11:16], row["note"]),
                         ("late", "manual", "09:30", "Corrected from the sign-in sheet"))
        self.assertEqual(row["work_date"], rec["work_date"])                      # the date can't be moved
        att.delete_record(self.db, rec["id"])
        self.assertIsNone(self.db.execute("SELECT 1 FROM attendance WHERE id = ?", (rec["id"],)).fetchone())


if __name__ == "__main__":
    unittest.main()
