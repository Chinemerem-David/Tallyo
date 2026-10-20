"""Helpers shared by the Admin and HR pages, so both always show the same numbers."""
import csv
import io
from datetime import date

from flask import Response

import export_service
import queries


def attendance_filters(args, today=None):
    today = today or date.today()
    start, end, trimmed = queries.resolve_range(args.get("from"), args.get("to"), today)
    status = args.get("status", "")
    return {"start": start, "end": end, "trimmed": trimmed, "today": today,
            "q": args.get("q", "").strip()[:60], "dept": args.get("dept", "")[:40],
            "status": status if status in queries.ATTENDANCE_STATUSES else ""}


def attendance_context(conn, args):
    """Everything the attendance log template needs."""
    f = attendance_filters(args)
    result = queries.list_attendance(conn, f["start"], f["end"], f["today"], f["q"], f["dept"],
                                     f["status"], args.get("page", 1, type=int))
    base = {"from": f["start"].isoformat(), "to": f["end"].isoformat(),
            "q": f["q"] or None, "dept": f["dept"] or None}
    c = result["counts"]
    arrivals = c["on_time"] + c["late"]
    return {"result": result, "f": f, "base": base, "keep": {**base, "status": f["status"] or None},
            "departments": queries.departments(conn),
            "on_time_pct": round(100 * c["on_time"] / arrivals) if arrivals else None}


def _csv_response(rows, filename):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    for row in rows:
        writer.writerow([export_service.safe_cell(v) for v in row])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % filename})


def attendance_csv(conn, args):
    """Same filters as the page, but every matching row."""
    f = attendance_filters(args)
    rows = queries.list_attendance(conn, f["start"], f["end"], f["today"], f["q"], f["dept"],
                                   f["status"], paginate=False)["rows"]
    body = [["date", "employee_id", "name", "department", "check_in", "check_out", "hours",
             "status", "source", "note"]]
    for r in rows:
        body.append([r["work_date"], r["employee_code"], r["full_name"], r["department"],
                     r["check_in"][11:16] if r["check_in"] else "",
                     r["check_out"][11:16] if r["check_out"] else "",
                     r["hours"] if r["hours"] != "–" else "", r["status"], r["source"] or "", r["note"] or ""])
    return _csv_response(body, "tallyo-attendance-%s-to-%s.csv" % (f["start"], f["end"]))


def report_context(conn, args):
    today = date.today()
    first = queries.resolve_month(args.get("month"), today)
    prev, nxt = queries.month_neighbours(first, today)
    return {"report": queries.report_month(conn, first, today), "first": first,
            "month_label": first.strftime("%B %Y"), "prev": prev, "next_month": nxt,
            "low_rate": queries.LOW_RATE}


def report_csv(conn, args):
    today = date.today()
    first = queries.resolve_month(args.get("month"), today)
    report = queries.report_month(conn, first, today)
    body = [["date", "expected", "present", "late", "absent", "attendance_rate_percent"]]
    body += [[d["date"], d["total"], d["present"], d["late"], d["absent"], d["rate"]] for d in report["days"]]
    return _csv_response(body, "tallyo-report-%s.csv" % first.strftime("%Y-%m"))
