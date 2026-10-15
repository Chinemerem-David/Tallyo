"""Admin pages. Built one at a time: Employees first, then Cards, Users, ..."""
from datetime import date

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)

import auth
import backup_service
import card_service
import db
import employee_service
import queries
import reader
import settings_service
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
