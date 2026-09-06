from __future__ import annotations

import calendar
import time
from datetime import date, timedelta
from pathlib import Path

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for

from lib.activity import log_activity
from lib.assistant_agenda import calendar_clock
from lib.ics_calendar import (
    PublishedCalendarError,
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
    toggle_todo_canceled,
    update_event,
)
from lib.security import admin_required, login_required


def _asset_version() -> int:
    static_root = Path(current_app.static_folder or "static")
    paths = (static_root / "css" / "planner.css", static_root / "js" / "planner.js")
    timestamps = [int(path.stat().st_mtime) for path in paths if path.is_file()]
    return max(timestamps, default=int(time.time()))


def _month_value(raw: str | None) -> tuple[int, int]:
    today = date.fromisoformat(calendar_clock()["today"])
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


def _event_span(item: dict, range_start: date, range_end: date) -> tuple[date, date] | None:
    try:
        first_day = date.fromisoformat(str(item.get("date") or ""))
        last_day = date.fromisoformat(str(item.get("end_date") or item.get("date") or ""))
    except ValueError:
        return None
    if last_day < first_day:
        last_day = first_day
    visible_first = max(first_day, range_start)
    visible_last = min(last_day, range_end - timedelta(days=1))
    if visible_last < visible_first:
        return None
    return visible_first, visible_last


def _events_by_visible_date(
    events: list[dict],
    range_start: date,
    range_end: date,
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in events:
        span = _event_span(item, range_start, range_end)
        if span is None:
            continue
        visible_first, visible_last = span
        actual_first = date.fromisoformat(str(item.get("date")))
        actual_last = date.fromisoformat(str(item.get("end_date") or item.get("date")))
        cursor = visible_first
        while cursor <= visible_last:
            display_item = dict(item)
            if actual_first == actual_last:
                position = "single"
            elif cursor == actual_first:
                position = "start"
            elif cursor == actual_last:
                position = "end"
            else:
                position = "middle"
            display_item["display_date"] = cursor.isoformat()
            display_item["span_position"] = position
            display_item["continues_before"] = cursor > actual_first
            display_item["continues_after"] = cursor < actual_last
            display_item["display_start_time"] = item.get("start_time", "") if cursor == actual_first else ""
            grouped.setdefault(cursor.isoformat(), []).append(display_item)
            cursor += timedelta(days=1)

    for day_events in grouped.values():
        day_events.sort(
            key=lambda item: (
                item.get("all_day") is not True and not item.get("continues_before"),
                item.get("display_start_time") or "00:00",
                item.get("title", "").casefold(),
            )
        )
    return grouped


def _tasks_by_visible_date(
    todos: list[dict],
    range_start: date,
    range_end: date,
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    priority_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    for item in todos:
        raw_due_date = str(item.get("due_date") or "").strip()
        if not raw_due_date:
            continue
        try:
            due_date = date.fromisoformat(raw_due_date)
        except ValueError:
            continue
        if not range_start <= due_date < range_end:
            continue
        display_item = dict(item)
        display_item["display_date"] = due_date.isoformat()
        grouped.setdefault(due_date.isoformat(), []).append(display_item)

    for day_tasks in grouped.values():
        day_tasks.sort(
            key=lambda item: (
                bool(item.get("completed")),
                item.get("due_time") or "23:59",
                priority_order.get(str(item.get("priority") or "none"), 3),
                str(item.get("title") or "").casefold(),
            )
        )
    return grouped


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
        if _event_span(item, range_start, range_end) is not None
    ]

    published_events = []
    published_calendar = {"configured": False, "path": "", "host": "", "last_sync": "", "stale": False}
    if username == "Admin":
        published_calendar.update(config_status())
        published_events, cache = load_cached_events(range_start, range_end)
        published_calendar["last_sync"] = str(cache.get("last_sync") or "")
        published_calendar["stale"] = bool(
            published_calendar["configured"] and cache_needs_refresh(range_start, range_end)
        )

    events = local_events + published_events
    events.sort(
        key=lambda item: (
            item.get("date", ""),
            item.get("all_day") is not True,
            item.get("start_time") or "00:00",
            item.get("title", "").casefold(),
        )
    )
    events_by_date = _events_by_visible_date(events, range_start, range_end)
    tasks_by_date = _tasks_by_visible_date(items["todos"], range_start, range_end)

    previous = _shift_month(year, month, -1)
    following = _shift_month(year, month, 1)
    today = date.fromisoformat(calendar_clock()["today"])
    open_todos = sum(1 for item in items["todos"] if not item.get("completed") and not item.get("canceled"))
    canceled_todos = sum(1 for item in items["todos"] if item.get("canceled"))

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
        tasks_by_date=tasks_by_date,
        todos=items["todos"],
        open_todos=open_todos,
        canceled_todos=canceled_todos,
        published_calendar=published_calendar,
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
def planner_update_event(event_id: str):
    username = session.get("username", "")
    try:
        item = update_event(username, event_id, request.form)
    except PlannerValidationError as exc:
        flash(str(exc), "warning")
    else:
        if item is None:
            flash("Event not found.", "warning")
        else:
            flash("Event updated.", "success")
            log_activity("planner.event.update", event_id, details={"title": item.get("title", "")})
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
    elif item.get("canceled"):
        flash("Restore the canceled task before completing it.", "warning")
    else:
        log_activity("planner.todo.toggle", todo_id, details={"completed": item.get("completed")})
    return redirect(_return_to_month())


@login_required
def planner_cancel_todo(todo_id: str):
    username = session.get("username", "")
    item = toggle_todo_canceled(username, todo_id)
    if item is None:
        flash("Task not found.", "warning")
    else:
        canceled = bool(item.get("canceled"))
        flash("Task canceled." if canceled else "Task restored.", "success")
        log_activity(
            "planner.todo.cancel" if canceled else "planner.todo.restore",
            todo_id,
            details={"canceled": canceled},
        )
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
def planner_calendar_sync():
    year, month = _month_value(request.form.get("month") or request.args.get("month"))
    weeks = month_grid(year, month)
    start_date = weeks[0][0]
    end_date = weeks[-1][-1] + timedelta(days=1)
    wants_json = request.accept_mimetypes.best == "application/json" or request.headers.get("X-Requested-With") == "fetch"
    try:
        cache = sync_calendar(start_date, end_date)
    except PublishedCalendarError as exc:
        if wants_json:
            return jsonify(success=False, error=str(exc)), 400
        flash(str(exc), "danger")
    else:
        count = len(cache.get("events", []))
        log_activity("planner.calendar.sync", details={"count": count, "source": "ics"})
        if wants_json:
            return jsonify(success=True, count=count, last_sync=cache.get("last_sync"))
        flash(f"Calendar refreshed: {count} event(s).", "success")
    return redirect(url_for("planner_page", month=_month_token(year, month)))


def register_routes(app):
    app.add_url_rule("/planner", "planner_page", planner_page)
    app.add_url_rule("/planner/events", "planner_create_event", planner_create_event, methods=["POST"])
    app.add_url_rule("/planner/events/<event_id>/update", "planner_update_event", planner_update_event, methods=["POST"])
    app.add_url_rule("/planner/events/<event_id>/delete", "planner_delete_event", planner_delete_event, methods=["POST"])
    app.add_url_rule("/planner/todos", "planner_create_todo", planner_create_todo, methods=["POST"])
    app.add_url_rule("/planner/todos/<todo_id>/toggle", "planner_toggle_todo", planner_toggle_todo, methods=["POST"])
    app.add_url_rule("/planner/todos/<todo_id>/cancel", "planner_cancel_todo", planner_cancel_todo, methods=["POST"])
    app.add_url_rule("/planner/todos/<todo_id>/delete", "planner_delete_todo", planner_delete_todo, methods=["POST"])
    app.add_url_rule("/planner/calendar/sync", "planner_calendar_sync", planner_calendar_sync, methods=["POST"])
