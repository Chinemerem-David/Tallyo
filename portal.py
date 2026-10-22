"""Employee portal: a read-only month calendar of the signed-in person's own attendance.
There is no employee ID in the URL, so nobody can ask for someone else's records."""
from datetime import date

from flask import Blueprint, abort, render_template, request, session

import auth
import db
import queries
from shell import shell_context

bp = Blueprint("portal", __name__, url_prefix="/portal")
employee_only = auth.role_required("employee")


@bp.route("")
@employee_only
def home():
    conn, today = db.get_db(), date.today()
    employee_id = session["user"].get("employee_id")
    person = queries.get_employee(conn, employee_id) if employee_id else None
    if person is None:
        abort(403)

    joined_first = date.fromisoformat(person["joined_on"]).replace(day=1)
    first = max(queries.resolve_month(request.args.get("month"), today), min(joined_first, today.replace(day=1)))
    prev, nxt = queries.month_neighbours(first, today)
    if prev < joined_first:
        prev = None                                    # nothing exists before they joined
    return render_template(
        "portal_month.html", cal=queries.employee_calendar(conn, person["id"], first, today),
        first=first, month_label=first.strftime("%B %Y"), prev=prev, next_month=nxt,
        months=queries.month_options(joined_first, today), is_current=first == today.replace(day=1),
        first_name=person["full_name"].split()[0], person=person, **shell_context("This month"))
