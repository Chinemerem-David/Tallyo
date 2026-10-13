"""Admin pages. Built one at a time: Employees first, then Cards, Users, ..."""
from flask import Blueprint, render_template, request

import auth
import db
import queries
from shell import shell_context

bp = Blueprint("admin", __name__, url_prefix="/admin")
admin_only = auth.role_required("admin")


@bp.route("/employees")
@admin_only
def employees():
    q = request.args.get("q", "").strip()[:60]
    dept = request.args.get("dept", "")[:40]
    status = request.args.get("status", "")
    if status not in ("no_card", "inactive"):
        status = ""
    page = request.args.get("page", 1, type=int)

    conn = db.get_db()
    return render_template(
        "admin_employees.html",
        result=queries.list_employees(conn, q, dept, status, page),
        counts=queries.employee_counts(conn), departments=queries.departments(conn),
        q=q, dept=dept, status=status,
        keep={"q": q or None, "dept": dept or None, "status": status or None},
        **shell_context("Employees"))
