"""Pages every signed-in person can use, whatever their role."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import auth
import db
import user_service
from employee_service import ValidationError
from shell import shell_context

bp = Blueprint("account", __name__, url_prefix="/account")


@bp.route("/password", methods=["GET", "POST"])
@auth.login_required
def password():
    conn = db.get_db()
    user = session["user"]
    forced = bool(conn.execute("SELECT must_change_password FROM users WHERE id = ?",
                               (user["id"],)).fetchone()[0])

    def page(errors=None, status=200):
        return render_template("account_password.html", forced=forced, errors=errors or {},
                               **shell_context()), status

    if request.method == "POST":
        auth.verify_csrf()
        try:
            user_service.change_password(conn, user["id"], request.form.get("current", ""),
                                         request.form.get("new", ""), request.form.get("confirm", ""))
        except ValidationError as err:
            return page(err.errors, 422)
        flash("Your password was changed.", "success")
        return redirect(url_for(auth.ROLE_HOME[user["role"]]))
    return page()
