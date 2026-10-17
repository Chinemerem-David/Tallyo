import os
from datetime import datetime, timedelta

from flask import Flask, abort, redirect, render_template, request, session, url_for

try:                                  # .env support is optional
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import account
import admin
import auth
import backup_service
import db
import queries
import reader
from shell import day_label, friendly_when, safe_url, shell_context

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
app.register_blueprint(account.bp)
app.register_blueprint(admin.bp)
app.add_template_filter(friendly_when, "when")
app.add_template_filter(day_label, "day")
db.init_app(app)

# The product name lives in ONE place.
APP_NAME = "Tallyo"
APP_TAGLINE = "RFID attendance for teams"


@app.context_processor
def inject_globals():
    """Available in every template."""
    return {"APP_NAME": APP_NAME, "APP_TAGLINE": APP_TAGLINE,
            "csrf_token": auth.csrf_token, "safe_url": safe_url,
            "reader": reader.status()}


@app.after_request
def no_store_when_signed_in(response):
    """Stops the Back button from showing a signed-in page after sign-out."""
    if "user" in session and request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


# ---- Placeholder pages (HR and Employee, until they are built) ---------------

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
    attention = queries.attention_items(
        conn, today, backup=backup_service.status(app.config["DATABASE"]))
    return render_template(
        "admin_overview.html",
        stats=queries.overview_stats(conn, today),
        taps=queries.latest_taps(conn, today),
        attention=attention,
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


def startup_checks():
    """Friendly hints printed when you run `python app.py`."""
    if app.config["SECRET_KEY"] == "dev-only-change-me":
        print("NOTE: using the development secret key. Put TALLYO_SECRET_KEY in .env before real use.")
    conn = db.connect(app.config["DATABASE"])
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        print("NOTE: no users yet. Run  python seed.py  to create the demo data.")
    conn.close()


if __name__ == "__main__":
    startup_checks()
    app.run(debug=True)
