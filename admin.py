"""Admin pages. Built one at a time: Employees first, then Cards, Users, ..."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import auth
import db
import employee_service
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


# ---- Add / edit an employee (Part 2) -----------------------------------------

def _form(creating, values, errors, employee_id=None, status=200):
    return render_template(
        "admin_employee_form.html", creating=creating, values=values, errors=errors,
        employee_id=employee_id, departments=queries.departments(db.get_db()),
        **shell_context("Employees")), status


@bp.route("/employees/new", methods=["GET", "POST"])
@admin_only
def employee_new():
    conn = db.get_db()
    if request.method == "POST":
        auth.verify_csrf()
        try:
            clean = employee_service.validate(conn, request.form, creating=True)
            _, password = employee_service.create_employee(conn, clean)
        except employee_service.ValidationError as err:
            return _form(True, err.values, err.errors, status=422)
        flash("%s added. Sign-in ID: %s. Temporary password: %s. It is shown only once, "
              "so write it down." % (clean["full_name"], clean["employee_code"], password),
              "success")
        return redirect(url_for("admin.employees"))
    return _form(True, {"employee_code": employee_service.next_code(conn), "full_name": "",
                        "department": "", "email": "", "is_active": True,
                        "joined_on": date.today().isoformat()}, {})


@bp.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
@admin_only
def employee_edit(employee_id):
    conn = db.get_db()
    row = queries.get_employee(conn, employee_id)
    if row is None:
        abort(404)
    if request.method == "POST":
        auth.verify_csrf()
        try:
            clean = employee_service.validate(conn, request.form, creating=False)
        except employee_service.ValidationError as err:
            err.values["employee_code"] = row["employee_code"]      # the ID can't be edited
            return _form(False, err.values, err.errors, employee_id, 422)
        employee_service.update_employee(conn, employee_id, clean)
        flash("Changes to %s saved." % clean["full_name"], "success")
        return redirect(url_for("admin.employees"))
    return _form(False, {"employee_code": row["employee_code"], "full_name": row["full_name"],
                         "department": row["department"], "email": row["email"] or "",
                         "joined_on": row["joined_on"], "is_active": bool(row["is_active"])},
                 {}, employee_id)
