import os
from datetime import datetime, timedelta

from flask import Flask, abort, redirect, render_template, request, session, url_for

import auth
import db
import queries

app = Flask(__name__)
app.config.update(
    # Signs the session cookie. In production set TALLYO_SECRET_KEY to a long
    # random value; never ship the fallback below.
    SECRET_KEY=os.environ.get("TALLYO_SECRET_KEY", "dev-only-change-me"),
    SESSION_COOKIE_HTTPONLY=True,       # JavaScript can't read the cookie
    SESSION_COOKIE_SAMESITE="Lax",      # not sent on cross-site form posts
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),   # for "Keep me signed in"
)
app.register_blueprint(auth.bp)
db.init_app(app)

# The product name lives in ONE place.
APP_NAME = "Tallyo"
APP_TAGLINE = "RFID attendance for teams"


@app.context_processor
def inject_globals():
    """Available in every template."""
    return {"APP_NAME": APP_NAME, "APP_TAGLINE": APP_TAGLINE,
            "csrf_token": auth.csrf_token}


@app.after_request
def no_store_when_signed_in(response):
    """Stops the Back button from showing a signed-in page after sign-out."""
    if "user" in session and request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


# ---- Menus and the shared page frame ----------------------------------------

def nav_for(role):
    home = url_for(auth.ROLE_HOME[role])
    if role == "admin":
        return [
            {"label": "Manage", "links": [
                {"name": "Overview", "href": home},
                {"name": "Employees", "href": "#"}, {"name": "Cards", "href": "#"},
                {"name": "Users", "href": "#"}, {"name": "Settings", "href": "#"}]},
            {"label": "Records", "links": [
                {"name": "Attendance", "href": "#"}, {"name": "Reports", "href": "#"}]},
        ]
    if role == "hr":
        return [{"label": "Records", "links": [
            {"name": "Today", "href": home}, {"name": "Attendance", "href": "#"},
            {"name": "Reports", "href": "#"}, {"name": "Employees", "href": "#"}]}]
    return [{"label": "My attendance", "links": [{"name": "This month", "href": home}]}]


ACTIVE_LINK = {"admin": "Overview", "hr": "Today", "employee": "This month"}


def initials(name):
    parts = name.split()
    return (parts[0][0] + parts[1][0] if len(parts) > 1 else name[:2]).upper()


def shell_context():
    user = session["user"]
    role = user["role"]
    return {
        "nav": nav_for(role), "active": ACTIVE_LINK[role],
        "role_label": auth.ROLE_LABELS[role],
        "user_name": user["name"], "user_initials": initials(user["name"]),
    }


def placeholder(title):
    role = session["user"]["role"]
    others = [(label, url_for(auth.ROLE_HOME[key]))
              for key, label in (("admin", "Admin dashboard"), ("hr", "HR dashboard"),
                                 ("employee", "Employee portal")) if key != role]
    return render_template("placeholder.html", title=title, others=others,
                           **shell_context())


# ---- Pages -------------------------------------------------------------------

@app.route("/")
def home():
    user = session.get("user")
    if user:
        return redirect(url_for(auth.ROLE_HOME[user["role"]]))
    return redirect(url_for("auth.login"))


def greeting(now):
    return "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"


@app.route("/admin")
@auth.role_required("admin")
def admin_home():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    conn = db.get_db()
    return render_template(
        "admin_overview.html",
        stats=queries.overview_stats(conn, today),
        taps=queries.latest_taps(conn, today),
        attention=queries.attention_items(conn, today),
        greeting=greeting(now),
        first_name=session["user"]["name"].split()[0],
        date_label="%s %d %s" % (now.strftime("%A"), now.day, now.strftime("%B")),
        **shell_context())


@app.route("/hr")
@auth.role_required("hr")
def hr_home():
    return placeholder("HR dashboard")


@app.route("/portal")
@auth.role_required("employee")
def portal_home():
    return placeholder("Employee portal")


@app.route("/style-guide")
def style_guide():
    if not app.debug:               # developer page only
        abort(404)
    nav = [{"label": "Manage", "links": [
               {"name": "Overview", "href": "#"}, {"name": "Employees", "href": "#"},
               {"name": "Cards", "href": "#"}, {"name": "Users", "href": "#"},
               {"name": "Settings", "href": "#"}]},
           {"label": "Records", "links": [
               {"name": "Attendance", "href": "#"}, {"name": "Reports", "href": "#"}]}]
    return render_template("style_guide.html", nav=nav, active="Overview",
                           role_label="Admin", user_name="Admin", user_initials="AD")


# ---- Friendly error pages ----------------------------------------------------

def error_page(code, title, message):
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(400)
def bad_request(_):
    return error_page(400, "Request not accepted",
                      "Your session expired or the form was invalid. Please go back and try again.")


@app.errorhandler(403)
def forbidden(_):
    return error_page(403, "No access",
                      "Your account doesn't have permission to open this page.")


@app.errorhandler(404)
def not_found(_):
    return error_page(404, "Page not found", "We couldn't find that page.")


if __name__ == "__main__":
    app.run(debug=True)
