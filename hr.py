"""HR pages: read-only views of attendance, reports and employees.
HR never sees card details and can't change anything."""
from datetime import date, timedelta

from flask import Blueprint, abort, render_template, request

import auth
import db
import queries
import reporting
from services import get_rules
from shell import shell_context

bp = Blueprint("hr", __name__, url_prefix="/hr")
hr_only = auth.role_required("hr")


@bp.route("")
@hr_only
def today():
    conn, day = db.get_db(), date.today()
    return render_template("hr_today.html", data=queries.hr_today(conn, day),
                           late_after=get_rules(conn)["late_after"],
                           date_label="%s %d %s %d" % (day.strftime("%A"), day.day, day.strftime("%B"), day.year),
                           **shell_context("Today"))


@bp.route("/today/export")
@hr_only
def today_export():
    data = queries.hr_today(db.get_db(), date.today())
    body = [["name", "employee_id", "department", "status", "check_in"]]
    body += [[p["name"], p["code"], p["department"], p["status"], p["time"]] for p in data["everyone"]]
    return reporting._csv_response(body, "tallyo-today-%s.csv" % date.today().isoformat())


@bp.route("/attendance")
@hr_only
def attendance():
    return render_template("attendance_log.html", list_endpoint="hr.attendance",
                           export_endpoint="hr.attendance_export", can_edit=False,
                           **reporting.attendance_context(db.get_db(), request.args),
                           **shell_context("Attendance"))


@bp.route("/attendance/export")
@hr_only
def attendance_export():
    return reporting.attendance_csv(db.get_db(), request.args)


@bp.route("/reports")
@hr_only
def reports():
    return render_template("monthly_report.html", report_endpoint="hr.reports",
                           export_endpoint="hr.reports_export",
                           **reporting.report_context(db.get_db(), request.args), **shell_context("Reports"))


@bp.route("/reports/export")
@hr_only
def reports_export():
    return reporting.report_csv(db.get_db(), request.args)


@bp.route("/employees")
@hr_only
def employees():
    q = request.args.get("q", "").strip()[:60]
    dept = request.args.get("dept", "")[:40]
    status = "inactive" if request.args.get("status") == "inactive" else ""      # HR has no "no card" view
    conn = db.get_db()
    result = queries.list_employees(conn, q, dept, status, request.args.get("page", 1, type=int),
                                    with_cards=False)                            # card IDs never leave the database
    return render_template(
        "hr_employees.html", result=result, counts=queries.employee_counts(conn),
        rates=queries.employee_month_rates(conn, [r["id"] for r in result["rows"]], date.today()),
        departments=queries.departments(conn), q=q, dept=dept, status=status,
        keep={"q": q or None, "dept": dept or None, "status": status or None}, **shell_context("Employees"))


@bp.route("/employees/<int:employee_id>")
@hr_only
def employee(employee_id):
    conn = db.get_db()
    person = queries.get_employee(conn, employee_id)
    if person is None:
        abort(404)
    today_ = date.today()
    result = queries.list_attendance(conn, today_ - timedelta(days=29), today_, today_,
                                     page=request.args.get("page", 1, type=int), employee_id=employee_id)
    return render_template("hr_employee.html", person=person, result=result, today=today_.isoformat(),
                           rate=queries.employee_month_rates(conn, [employee_id], today_).get(employee_id),
                           **shell_context("Employees"))
