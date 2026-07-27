from __future__ import annotations

import calendar
import time
from datetime import date, timedelta
from pathlib import Path

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for

from lib.activity import log_activity
from lib.microsoft_calendar import (
    MicrosoftCalendarError,
    cache_needs_refresh,
    config_status,
    load_cached_events,
    sync_calendar,
)
from lib.planner import (
    PlannerValidationError,
    create_event,
    create_todo,
    delete_event,
    delete_todo,
    list_items,
    month_grid,
    toggle_todo,
)
from lib.security import admin_required, login_required


def _asset_version() -> int:
    static_root = Path(current_app.static_folder or "static")
    paths = (static_root / "css" / "planner.css", static_root / "js" / "planner.js")
    timestamps = [int(path.stat().st_mtime) for path in paths if path.is_file()]
    return max(timestamps, default=int(time.time()))


def _month_value(raw: str | None) -> tuple[int, int]:
    today = date.today()
    if raw:
        try:
            parsed = date.fromisoformat(f"{raw.strip()}-01")
            return parsed.year, parsed.month
        except ValueError:
            pass
    return today.year, today.month


def _month_token(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    zero_based = year * 12 + month - 1 + offset
    return zero_based // 12, zero_based % 12 + 1


def _return_to_month() -> str:
    year, month = _month_value(request.form.get("return_month"))
    return url_for("planner_page", month=_month_token(year, month))


@login_required
def planner_page():
    username = session.get("username", "")
    year, month = _month_value(request.args.get("month"))
    weeks = month_grid(year, month)
    range_start = weeks[0][0]
    range_end = weeks[-1][-1] + timedelta(days=1)

    items = list_items(username)
    local_events = [
        item for item in items["events"]
        if range_start.isoformat() <= item.get("date", "") < range_end.isoformat()
    ]

    microsoft_events = []
    microsoft = {"configured": False, "path": "", "user_id": "", "last_sync": "", "stale": False}
    if username == "Admin":
        microsoft.update(config_status())
        microsoft_events, cache = load_cached_events(range_start, range_end)
        microsoft["last_sync"] = str(cache.get("last_sync") or "")
        microsoft["stale"] = bool(microsoft["configured"] and cache_needs_refresh(range_start, range_end))

    events = local_events + microsoft_events
    events.sort(
        key=lambda item: (
            item.get("date", ""),
            item.get("all_day") is not True,
            item.get("start_time") or "00:00",
            item.get("title", "").casefold(),
        )
    )
    events_by_date: dict[str, list[dict]] = {}
    for item in events:
        events_by_date.setdefault(item.get("date", ""), []).append(item)

    previous = _shift_month(year, month, -1)
    following = _shift_month(year, month, 1)
    today = date.today()
    open_todos = sum(1 for item in items["todos"] if not item.get("completed"))

    return render_template(
        "planner.html",
        weeks=weeks,
        year=year,
        month=month,
        month_token=_month_token(year, month),
        month_label=f"{calendar.month_name[month]} {year}",
        previous_month=_month_token(*previous),
        next_month=_month_token(*following),
        today=today,
        events_by_date=events_by_date,
        todos=items["todos"],
        open_todos=open_todos,
        microsoft=microsoft,
        planner_asset_version=_asset_version(),
    )


@login_required
def planner_create_event():
    username = session.get("username", "")
    try:
        item = create_event(username, request.form)
    except PlannerValidationError as exc:
        flash(str(exc), "warning")
    else:
        flash("Event added.", "success")
        log_activity("planner.event.create", item.get("title", ""))
    return redirect(_return_to_month())


@login_required
def planner_delete_event(event_id: str):
    username = session.get("username", "")
    if delete_event(username, event_id):
        flash("Event deleted.", "success")
        log_activity("planner.event.delete", event_id)
    else:
        flash("Event not found.", "warning")
    return redirect(_return_to_month())


@login_required
def planner_create_todo():
    username = session.get("username", "")
    try:
        item = create_todo(username, request.form)
    except PlannerValidationError as exc:
        flash(str(exc), "warning")
    else:
        flash("Task added.", "success")
        log_activity("planner.todo.create", item.get("title", ""))
    return redirect(_return_to_month())


@login_required
def planner_toggle_todo(todo_id: str):
    username = session.get("username", "")
    item = toggle_todo(username, todo_id)
    if item is None:
        flash("Task not found.", "warning")
    else:
        log_activity("planner.todo.toggle", todo_id, details={"completed": item.get("completed")})
    return redirect(_return_to_month())


@login_required
def planner_delete_todo(todo_id: str):
    username = session.get("username", "")
    if delete_todo(username, todo_id):
        flash("Task deleted.", "success")
        log_activity("planner.todo.delete", todo_id)
    else:
        flash("Task not found.", "warning")
    return redirect(_return_to_month())


@admin_required
def planner_microsoft_sync():
    year, month = _month_value(request.form.get("month") or request.args.get("month"))
    weeks = month_grid(year, month)
    start_date = weeks[0][0]
    end_date = weeks[-1][-1] + timedelta(days=1)
    wants_json = request.accept_mimetypes.best == "application/json" or request.headers.get("X-Requested-With") == "fetch"
    try:
        cache = sync_calendar(start_date, end_date)
    except MicrosoftCalendarError as exc:
        if wants_json:
            return jsonify(success=False, error=str(exc)), 400
        flash(str(exc), "danger")
    else:
        count = len(cache.get("events", []))
        log_activity("planner.microsoft.sync", details={"count": count})
        if wants_json:
            return jsonify(success=True, count=count, last_sync=cache.get("last_sync"))
        flash(f"Microsoft Calendar synced: {count} event(s).", "success")
    return redirect(url_for("planner_page", month=_month_token(year, month)))


def register_routes(app):
    app.add_url_rule("/planner", "planner_page", planner_page)
    app.add_url_rule("/planner/events", "planner_create_event", planner_create_event, methods=["POST"])
    app.add_url_rule("/planner/events/<event_id>/delete", "planner_delete_event", planner_delete_event, methods=["POST"])
    app.add_url_rule("/planner/todos", "planner_create_todo", planner_create_todo, methods=["POST"])
    app.add_url_rule("/planner/todos/<todo_id>/toggle", "planner_toggle_todo", planner_toggle_todo, methods=["POST"])
    app.add_url_rule("/planner/todos/<todo_id>/delete", "planner_delete_todo", planner_delete_todo, methods=["POST"])
    app.add_url_rule("/planner/microsoft/sync", "planner_microsoft_sync", planner_microsoft_sync, methods=["POST"])
