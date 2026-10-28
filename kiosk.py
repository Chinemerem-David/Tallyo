"""The kiosk: a public screen next to the reader. No sign-in, but it is only reachable
from the reader computer (localhost), from a browser that knows the secret kiosk key,
or by a signed-in admin."""
import hmac

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)

import db
import kiosk_service

bp = Blueprint("kiosk", __name__, url_prefix="/kiosk")
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _allowed():
    user = session.get("user")
    if user and user.get("role") == "admin":
        return True
    if current_app.config.get("KIOSK_KEY"):
        return bool(session.get("kiosk_ok"))               # earned by visiting /kiosk?key=...
    return request.remote_addr in LOOPBACK                  # no key set: this computer only


@bp.before_request
def guard():
    key, given = current_app.config.get("KIOSK_KEY", ""), request.args.get("key", "")
    if key and given and hmac.compare_digest(given.encode(), key.encode()):
        session["kiosk_ok"] = True                          # remember this browser, then hide the key
        return redirect(url_for(request.endpoint))
    if not _allowed():
        message = ("The kiosk screen only opens on the computer that has the card reader. "
                   "To use it on another device, set TALLYO_KIOSK_KEY in the .env file, restart the app, "
                   "and open /kiosk?key=YOUR_KEY once on that device. A signed-in admin can also open it.")
        return render_template("error.html", code=403, title="The kiosk isn't available on this device",
                               message=message), 403


@bp.route("")
def screen():
    resp = current_app.make_response(render_template("kiosk.html", hold_ms=current_app.config["KIOSK_HOLD_MS"]))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/events")
def events():
    conn = db.get_db()
    newest = kiosk_service.latest_id(conn)
    after = request.args.get("after", type=int)
    if after is None or after < 0 or after > newest:        # first load (or a reset database): start from now
        payload = {"last_id": newest, "events": []}
    else:
        last, found = kiosk_service.events_after(conn, after)
        payload = {"last_id": last, "events": found}
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp
