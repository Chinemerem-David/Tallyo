"""Admin pages. Built one at a time: Employees first, then Cards, Users, ..."""
import csv
import io
from datetime import date

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template,
                   request, send_file, session, url_for)

import attendance_service
import auth
import backup_service
import card_service
import db
import employee_service
import export_service
import queries
import reader
import settings_service
import user_service
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


# ---- Cards for one employee (Part 3) -----------------------------------------

def _card_page(employee, error=None, picked="", manual="", status=200):
    conn = db.get_db()
    return render_template(
        "admin_employee_card.html", employee=employee, error=error, picked=picked, manual=manual,
        current=card_service.current_card(conn, employee["id"]),
        history=card_service.past_cards(conn, employee["id"]),
        spares=card_service.available_cards(conn),
        recent=card_service.recent_unregistered(conn),
        today=date.today().isoformat(), **shell_context("Employees")), status


@bp.route("/employees/<int:employee_id>/card", methods=["GET", "POST"])
@admin_only
def employee_card(employee_id):
    conn = db.get_db()
    employee = queries.get_employee(conn, employee_id)
    if employee is None:
        abort(404)
    if request.method == "POST":
        auth.verify_csrf()
        picked = request.form.get("uid_pick", "")
        manual = request.form.get("uid_manual", "").strip()
        try:
            if not (manual or picked):
                raise card_service.CardError("Pick a card or type a card ID.")
            uid = card_service.assign_card(conn, employee_id, manual or picked,
                                           request.form.get("replace_old_as") or None)
        except card_service.CardError as err:
            return _card_page(employee, str(err), picked, manual, 422)
        flash("Card %s assigned to %s." % (uid, employee["full_name"]), "success")
        return redirect(url_for("admin.employees"))
    return _card_page(employee)


@bp.route("/employees/<int:employee_id>/card/retire", methods=["POST"])
@admin_only
def employee_card_retire(employee_id):
    conn = db.get_db()
    employee = queries.get_employee(conn, employee_id)
    if employee is None:
        abort(404)
    auth.verify_csrf()
    status = request.form.get("status", "")
    try:
        uid = card_service.retire_card(conn, employee_id, status)
    except card_service.CardError as err:
        flash(str(err), "error")
    else:
        flash("Card %s marked as %s. It can no longer be used." % (uid, status), "success")
    return redirect(url_for("admin.employee_card", employee_id=employee_id))


# ---- Cards (full list, register, assign a spare) ------------------------------

@bp.route("/cards")
@admin_only
def cards():
    q = request.args.get("q", "").strip()[:60]
    status = request.args.get("status", "")
    if status not in queries.CARD_STATUSES:
        status = ""
    conn = db.get_db()
    return render_template(
        "admin_cards.html", result=queries.list_cards(conn, q, status, request.args.get("page", 1, type=int)),
        counts=queries.card_counts(conn), recent=card_service.recent_unregistered(conn),
        q=q, status=status, keep={"q": q or None, "status": status or None},
        today=date.today().isoformat(), **shell_context("Cards"))


@bp.route("/cards/register", methods=["POST"])
@admin_only
def card_register():
    auth.verify_csrf()
    try:
        uid = card_service.register_card(db.get_db(), request.form.get("uid", ""))
    except card_service.CardError as err:
        flash(str(err), "error")
    else:
        flash("Card %s registered. It is ready to assign." % uid, "success")
    return redirect(url_for("admin.cards"))


@bp.route("/cards/<int:card_id>/assign", methods=["GET", "POST"])
@admin_only
def card_assign(card_id):
    conn = db.get_db()
    card = queries.get_card(conn, card_id)
    if card is None:
        abort(404)
    if card["status"] != "available":
        flash("Only spare cards can be assigned here. Open the employee's page to replace a card.", "error")
        return redirect(url_for("admin.cards"))
    people = card_service.employees_needing_card(conn)

    def page(error=None, chosen="", status=200):
        return render_template("admin_card_assign.html", card=card, people=people, error=error,
                               chosen=chosen, **shell_context("Cards")), status

    if request.method == "POST":
        auth.verify_csrf()
        chosen = request.form.get("employee_id", "")
        try:
            if not chosen.isdigit():
                raise card_service.CardError("Choose an employee.")
            uid = card_service.assign_card(conn, int(chosen), card["uid"])
        except card_service.CardError as err:
            return page(str(err), chosen, 422)
        name = next((p["full_name"] for p in people if str(p["id"]) == chosen), "the employee")
        flash("Card %s assigned to %s." % (uid, name), "success")
        return redirect(url_for("admin.cards"))
    return page()


# ---- Settings: work rules, reader, backups --------------------------------------

def _settings_page(values, errors=None, status=200):
    conn = db.get_db()
    path = current_app.config["DATABASE"]
    return render_template(
        "admin_settings.html", values=values, errors=errors or {},
        reader_state=reader.status(), last_tap=queries.last_tap(conn),
        records=queries.attendance_count(conn), backup=backup_service.status(path),
        today=date.today().isoformat(), **shell_context("Settings")), status


@bp.route("/settings", methods=["GET", "POST"])
@admin_only
def settings():
    conn = db.get_db()
    if request.method == "POST":
        auth.verify_csrf()
        try:
            clean = settings_service.validate(request.form)
        except settings_service.SettingsError as err:
            return _settings_page(err.values, err.errors, 422)
        settings_service.save_settings(conn, clean)
        flash("Work rules saved. They apply to the next card tap.", "success")
        return redirect(url_for("admin.settings"))
    return _settings_page(settings_service.get_settings(conn))


@bp.route("/settings/backup", methods=["POST"])
@admin_only
def settings_backup():
    auth.verify_csrf()
    dest = backup_service.create_backup(db.get_db(), current_app.config["DATABASE"])
    flash("Backup created: %s." % dest.name, "success")
    return redirect(url_for("admin.settings"))


@bp.route("/settings/backup/download")
@admin_only
def settings_backup_download():
    latest = backup_service.status(current_app.config["DATABASE"])["latest"]
    if latest is None:
        abort(404)
    return send_file(latest["path"], as_attachment=True, download_name=latest["name"])


@bp.route("/settings/export")
@admin_only
def settings_export():
    """Everything as a ZIP of CSV files (employees, cards, attendance, taps, settings).
    Passwords are never included."""
    data = export_service.export_zip(db.get_db())
    return send_file(io.BytesIO(data), mimetype="application/zip", as_attachment=True,
                     download_name="tallyo-export-%s.zip" % date.today().strftime("%Y%m%d"))


# ---- Users: admin and HR accounts ---------------------------------------------

def _temp_password_message(prefix, username, password):
    return ("%s Username: %s. Temporary password: %s. It is shown only once, and they must "
            "choose a new password when they next sign in." % (prefix, username, password))


@bp.route("/users")
@admin_only
def users():
    conn = db.get_db()
    return render_template(
        "admin_users.html", staff=queries.staff_accounts(conn),
        employee_accounts=queries.employee_account_count(conn),
        me_id=session["user"]["id"], today=date.today().isoformat(), **shell_context("Users"))


def _user_form(creating, values, errors, user=None, error=None, status=200):
    return render_template("admin_user_form.html", creating=creating, values=values, errors=errors,
                           user=user, error=error, roles=user_service.STAFF_ROLES,
                           **shell_context("Users")), status


@bp.route("/users/new", methods=["GET", "POST"])
@admin_only
def user_new():
    conn = db.get_db()
    if request.method == "POST":
        auth.verify_csrf()
        try:
            clean = user_service.validate_new(conn, request.form)
            _, password = user_service.create_user(conn, clean)
        except employee_service.ValidationError as err:
            return _user_form(True, err.values, err.errors, status=422)
        flash(_temp_password_message("%s added as %s." % (clean["full_name"], user_service.STAFF_ROLES[clean["role"]]),
                                     clean["username"], password), "success")
        return redirect(url_for("admin.users"))
    return _user_form(True, {"full_name": "", "username": "", "role": "hr"}, {})


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_only
def user_edit(user_id):
    conn = db.get_db()
    user = queries.get_user(conn, user_id)
    if user is None or user["role"] == "employee":
        abort(404)
    values = {"full_name": user["full_name"], "username": user["username"], "role": user["role"],
              "is_active": bool(user["is_active"])}
    if request.method == "POST":
        auth.verify_csrf()
        values["full_name"] = request.form.get("full_name", "")
        values["is_active"] = request.form.get("is_active") == "on"
        try:
            user_service.update_user(conn, user_id, values["full_name"], values["is_active"],
                                     session["user"]["id"])
        except employee_service.ValidationError as err:
            return _user_form(False, values, err.errors, user, status=422)
        except user_service.UserError as err:
            return _user_form(False, values, {}, user, error=str(err), status=422)
        flash("Changes to %s saved." % " ".join(values["full_name"].split()), "success")
        return redirect(url_for("admin.users"))
    return _user_form(False, values, {}, user)


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_only
def user_reset_password(user_id):
    auth.verify_csrf()
    try:
        user, password = user_service.reset_password(db.get_db(), user_id)
    except user_service.UserError as err:
        flash(str(err), "error")
    else:
        flash(_temp_password_message("Password reset for %s." % user["full_name"], user["username"], password),
              "success")
    return redirect(url_for("admin.users"))


@bp.route("/employees/<int:employee_id>/reset-password", methods=["POST"])
@admin_only
def employee_reset_password(employee_id):
    conn = db.get_db()
    if queries.get_employee(conn, employee_id) is None:
        abort(404)
    auth.verify_csrf()
    try:
        user, password = user_service.reset_employee_password(conn, employee_id)
    except user_service.UserError as err:
        flash(str(err), "error")
    else:
        flash(_temp_password_message("Password reset for %s." % user["full_name"], user["username"], password),
              "success")
    return redirect(url_for("admin.employee_edit", employee_id=employee_id))


# ---- Attendance: the full log, export, manual entries and corrections ------------

def _attendance_filters():
    today = date.today()
    start, end, trimmed = queries.resolve_range(request.args.get("from"), request.args.get("to"), today)
    status = request.args.get("status", "")
    return {"start": start, "end": end, "trimmed": trimmed, "today": today,
            "q": request.args.get("q", "").strip()[:60], "dept": request.args.get("dept", "")[:40],
            "status": status if status in queries.ATTENDANCE_STATUSES else ""}


@bp.route("/attendance")
@admin_only
def attendance():
    f, conn = _attendance_filters(), db.get_db()
    result = queries.list_attendance(conn, f["start"], f["end"], f["today"], f["q"], f["dept"],
                                     f["status"], request.args.get("page", 1, type=int))
    base = {"from": f["start"].isoformat(), "to": f["end"].isoformat(),
            "q": f["q"] or None, "dept": f["dept"] or None}
    c = result["counts"]
    arrivals = c["on_time"] + c["late"]
    return render_template(
        "admin_attendance.html", result=result, f=f, base=base,
        keep={**base, "status": f["status"] or None}, departments=queries.departments(conn),
        on_time_pct=round(100 * c["on_time"] / arrivals) if arrivals else None,
        **shell_context("Attendance"))


@bp.route("/attendance/export")
@admin_only
def attendance_export():
    """The same filters as the page, but every matching row, as a CSV for Excel."""
    f = _attendance_filters()
    rows = queries.list_attendance(db.get_db(), f["start"], f["end"], f["today"], f["q"], f["dept"],
                                   f["status"], paginate=False)["rows"]
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["date", "employee_id", "name", "department", "check_in", "check_out",
                     "hours", "status", "source", "note"])
    for r in rows:
        writer.writerow([export_service.safe_cell(v) for v in (
            r["work_date"], r["employee_code"], r["full_name"], r["department"],
            r["check_in"][11:16] if r["check_in"] else "", r["check_out"][11:16] if r["check_out"] else "",
            r["hours"] if r["hours"] != "–" else "", r["status"], r["source"] or "", r["note"] or "")])
    name = "tallyo-attendance-%s-to-%s.csv" % (f["start"], f["end"])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name})


def _attendance_form(creating, values, errors, record=None, status=200):
    return render_template(
        "admin_attendance_form.html", creating=creating, values=values, errors=errors, record=record,
        employees=queries.active_employees(db.get_db()) if creating else [],
        **shell_context("Attendance")), status


@bp.route("/attendance/new", methods=["GET", "POST"])
@admin_only
def attendance_new():
    conn = db.get_db()
    if request.method == "POST":
        auth.verify_csrf()
        try:
            clean = attendance_service.validate(conn, request.form)
            attendance_service.create_record(conn, clean)
        except employee_service.ValidationError as err:
            return _attendance_form(True, err.values, err.errors, status=422)
        person = queries.get_employee(conn, clean["employee_id"])
        flash("Entry added for %s on %s." % (person["full_name"], clean["work_date"]), "success")
        return redirect(url_for("admin.attendance"))
    day = request.args.get("date", "")
    try:
        date.fromisoformat(day)
    except ValueError:
        day = date.today().isoformat()
    return _attendance_form(True, {"employee_id": request.args.get("employee", ""), "work_date": day,
                                   "check_in": "", "check_out": "", "reason": ""}, {})


@bp.route("/attendance/<int:record_id>/edit", methods=["GET", "POST"])
@admin_only
def attendance_edit(record_id):
    conn = db.get_db()
    record = queries.get_attendance(conn, record_id)
    if record is None:
        abort(404)
    if request.method == "POST":
        auth.verify_csrf()
        try:
            clean = attendance_service.validate(conn, request.form, existing=record)
        except employee_service.ValidationError as err:
            return _attendance_form(False, err.values, err.errors, record, 422)
        attendance_service.update_record(conn, record_id, clean)
        flash("Record for %s on %s updated." % (record["full_name"], record["work_date"]), "success")
        return redirect(url_for("admin.attendance"))
    return _attendance_form(False, {"check_in": record["check_in"][11:16],
                                    "check_out": (record["check_out"] or "")[11:16],
                                    "reason": record["note"] or ""}, {}, record)


@bp.route("/attendance/<int:record_id>/delete", methods=["POST"])
@admin_only
def attendance_delete(record_id):
    conn = db.get_db()
    record = queries.get_attendance(conn, record_id)
    if record is None:
        abort(404)
    auth.verify_csrf()
    attendance_service.delete_record(conn, record_id)
    flash("Record for %s on %s deleted." % (record["full_name"], record["work_date"]), "success")
    return redirect(url_for("admin.attendance"))
