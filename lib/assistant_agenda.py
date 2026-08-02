from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from lib.ics_calendar import cache_needs_refresh, load_cached_events, load_config
from lib.planner import list_items

_DEFAULT_TIMEZONE = "Asia/Singapore"
_DAY_ALIASES = {
    "today": 0,
    "tomorrow": 1,
    "tommorrow": 1,
    "tmr": 1,
    "yesterday": -1,
    "day after tomorrow": 2,
    "day after tommorrow": 2,
}
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_DATE_QUESTION_RE = re.compile(
    r"\b(?:what|which|do you know)\b.*\b(?:date|day)\b.*\b(?:today|now)\b|"
    r"\b(?:today'?s date|current date)\b",
    re.IGNORECASE,
)
_FUTURE_EVENTS_RE = re.compile(
    r"\b(?:list|show|what(?:'s| is| are)?|all)\b.*\b(?:future|upcoming)\b.*\b(?:event|events|calendar|schedule)\b|"
    r"\b(?:future|upcoming)\b.*\b(?:event|events|calendar|schedule)\b",
    re.IGNORECASE,
)
_SCHEDULE_CUE_RE = re.compile(
    r"\b(?:schedule|scheduled|calendar|event|events|task|tasks|todo|todos|plan|plans|free|busy|"
    r"what\s+(?:do\s+)?i\s+have|do\s+i\s+have|i\s+(?:do\s+)?have|have\s+anything|have\s+something|"
    r"what\s+(?:do\s+)?i\s+(?:need|have)\s+to\s+do)\b",
    re.IGNORECASE,
)


def _timezone_name() -> str:
    configured = str(current_app.config.get("APP_TIMEZONE") or "").strip()
    calendar_config = load_config()
    if calendar_config and calendar_config.get("timezone"):
        return str(calendar_config["timezone"])
    return configured or _DEFAULT_TIMEZONE


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        try:
            return ZoneInfo(_DEFAULT_TIMEZONE)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


def calendar_clock(now: datetime | None = None) -> dict[str, str]:
    """Return the authoritative local calendar clock used by Tamestorage."""
    timezone_name = _timezone_name()
    zone = _zone(timezone_name)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    today = local_now.date()
    return {
        "timezone": timezone_name,
        "local_datetime": local_now.isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "today_weekday": today.strftime("%A"),
        "tomorrow": (today + timedelta(days=1)).isoformat(),
        "tomorrow_weekday": (today + timedelta(days=1)).strftime("%A"),
        "yesterday": (today - timedelta(days=1)).isoformat(),
        "day_after_tomorrow": (today + timedelta(days=2)).isoformat(),
    }


def _normalized_phrase(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def resolve_day(value: str | None, *, today: date | None = None) -> tuple[date, str]:
    """Resolve an ISO date or a relative day phrase using the calendar timezone."""
    base_day = today or date.fromisoformat(calendar_clock()["today"])
    phrase = _normalized_phrase(value) or "today"

    if phrase in _DAY_ALIASES:
        offset = _DAY_ALIASES[phrase]
        return base_day + timedelta(days=offset), phrase

    try:
        return date.fromisoformat(phrase), phrase
    except ValueError:
        pass

    days_match = re.fullmatch(r"in\s+(\d{1,3})\s+days?", phrase)
    if days_match:
        return base_day + timedelta(days=int(days_match.group(1))), phrase

    weekday_match = re.fullmatch(r"(?:(next)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", phrase)
    if weekday_match:
        force_next = bool(weekday_match.group(1))
        target_weekday = _WEEKDAYS[weekday_match.group(2)]
        offset = (target_weekday - base_day.weekday()) % 7
        if force_next:
            offset = offset + 7 if offset else 7
        return base_day + timedelta(days=offset), phrase

    raise ValueError(
        "Use an ISO date or a relative day such as `today`, `tomorrow`, `day after tomorrow`, or `next Monday`."
    )


def resolve_range(
    period: str | None = "",
    start_date: str | None = "",
    end_date: str | None = "",
    *,
    today: date | None = None,
) -> tuple[date, date, str]:
    """Resolve an inclusive calendar range."""
    base_day = today or date.fromisoformat(calendar_clock()["today"])
    raw_start = str(start_date or "").strip()
    raw_end = str(end_date or "").strip()
    normalized_period = _normalized_phrase(period)

    if raw_start:
        first_day, _ = resolve_day(raw_start, today=base_day)
        last_day, _ = resolve_day(raw_end, today=base_day) if raw_end else first_day + timedelta(days=30)
        return first_day, last_day, "explicit"

    if normalized_period in {"today", "tomorrow", "tommorrow", "tmr", "yesterday", "day after tomorrow"}:
        target, label = resolve_day(normalized_period, today=base_day)
        return target, target, label

    if normalized_period in {"this week", "this_week"}:
        return base_day, base_day + timedelta(days=6 - base_day.weekday()), "this_week"

    if normalized_period in {"next week", "next_week"}:
        next_monday = base_day + timedelta(days=(7 - base_day.weekday()))
        return next_monday, next_monday + timedelta(days=6), "next_week"

    if normalized_period in {"future", "upcoming", "all future", "all upcoming"}:
        return base_day, base_day + timedelta(days=366), "future"

    if normalized_period in {"next 7 days", "next_7_days"}:
        return base_day, base_day + timedelta(days=7), "next_7_days"

    if normalized_period in {"next 30 days", "next_30_days", ""}:
        return base_day, base_day + timedelta(days=30), "next_30_days"

    target, label = resolve_day(normalized_period, today=base_day)
    return target, target, label


def event_overlaps(item: dict[str, Any], first_day: date, exclusive_end: date) -> bool:
    try:
        event_start = date.fromisoformat(str(item.get("date") or item.get("start_date") or ""))
        event_end = date.fromisoformat(
            str(item.get("end_date") or item.get("date") or item.get("start_date") or "")
        )
    except ValueError:
        return False
    return event_start < exclusive_end and event_end >= first_day


def _normalized_event(item: dict[str, Any]) -> dict[str, Any]:
    source = "published" if item.get("source") == "ics" else "local"
    return {
        "event_id": str(item.get("id") or "") if source == "local" else "",
        "editable": source == "local",
        "title": str(item.get("title") or "Untitled event"),
        "start_date": str(item.get("date") or item.get("start_date") or ""),
        "end_date": str(item.get("end_date") or item.get("date") or item.get("start_date") or ""),
        "all_day": bool(item.get("all_day")),
        "start_time": str(item.get("start_time") or ""),
        "end_time": str(item.get("end_time") or ""),
        "location": str(item.get("location") or ""),
        "notes": str(item.get("notes") or "")[:1200],
        "source": source,
    }


def read_calendar_range(username: str, first_day: date, last_day: date, *, limit: int = 100) -> dict[str, Any]:
    """Read events overlapping an inclusive date range."""
    if last_day < first_day:
        raise ValueError("The calendar end date must not be before the start date.")
    if (last_day - first_day).days > 366:
        raise ValueError("Calendar inspection is limited to 367 days per request.")

    exclusive_end = last_day + timedelta(days=1)
    planner_items = list_items(username)
    events = [
        dict(item)
        for item in planner_items.get("events", [])
        if isinstance(item, dict) and event_overlaps(item, first_day, exclusive_end)
    ]

    published_stale = False
    if username == "Admin":
        published, _cache = load_cached_events(first_day, exclusive_end)
        events.extend(dict(item) for item in published if isinstance(item, dict))
        published_stale = cache_needs_refresh(first_day, exclusive_end)

    unique: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in events:
        normalized = _normalized_event(item)
        key = (
            normalized["source"],
            normalized["event_id"] or normalized["title"],
            normalized["start_date"],
            normalized["start_time"],
            normalized["end_date"],
        )
        unique[key] = normalized

    ordered = list(unique.values())
    ordered.sort(
        key=lambda item: (
            item["start_date"],
            item["all_day"] is not True,
            item["start_time"] or "00:00",
            item["title"].casefold(),
        )
    )
    safe_limit = max(1, min(200, int(limit or 100)))
    visible = ordered[:safe_limit]
    clock = calendar_clock()
    return {
        **clock,
        "range_start": first_day.isoformat(),
        "range_end": last_day.isoformat(),
        "range_end_is_inclusive": True,
        "events": visible,
        "returned_events": len(visible),
        "total_events": len(ordered),
        "truncated": len(ordered) > safe_limit,
        "published_calendar_stale": published_stale if username == "Admin" else None,
    }


def read_task_range(
    username: str,
    *,
    state: str = "open",
    first_day: date | None = None,
    last_day: date | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    normalized_state = str(state or "open").strip().lower()
    if normalized_state not in {"open", "done", "all"}:
        raise ValueError("Task state must be `open`, `done`, or `all`.")
    if first_day and last_day and last_day < first_day:
        raise ValueError("The task due-date end must not be before the start.")

    tasks: list[dict[str, Any]] = []
    undated_open_count = 0
    for item in list_items(username).get("todos", []):
        if not isinstance(item, dict):
            continue
        completed = bool(item.get("completed"))
        if not completed and not str(item.get("due_date") or ""):
            undated_open_count += 1
        if normalized_state == "open" and completed:
            continue
        if normalized_state == "done" and not completed:
            continue

        raw_due = str(item.get("due_date") or "")
        if first_day or last_day:
            if not raw_due:
                continue
            try:
                due_date = date.fromisoformat(raw_due)
            except ValueError:
                continue
            if first_day and due_date < first_day:
                continue
            if last_day and due_date > last_day:
                continue

        tasks.append(
            {
                "title": str(item.get("title") or "Untitled task"),
                "completed": completed,
                "due_date": raw_due,
                "due_time": str(item.get("due_time") or ""),
                "priority": str(item.get("priority") or "none"),
            }
        )

    tasks.sort(
        key=lambda item: (
            item["completed"],
            item["due_date"] or "9999-12-31",
            item["due_time"] or "23:59",
            {"high": 0, "medium": 1, "low": 2, "none": 3}.get(item["priority"], 3),
            item["title"].casefold(),
        )
    )
    safe_limit = max(1, min(200, int(limit or 100)))
    visible = tasks[:safe_limit]
    return {
        **calendar_clock(),
        "state": normalized_state,
        "due_start": first_day.isoformat() if first_day else "",
        "due_end": last_day.isoformat() if last_day else "",
        "tasks": visible,
        "returned_tasks": len(visible),
        "total_tasks": len(tasks),
        "truncated": len(tasks) > safe_limit,
        "open_undated_tasks": undated_open_count,
        "undated_tasks_are_not_scheduled": True,
    }


def read_day_agenda(username: str, day: str = "today") -> dict[str, Any]:
    clock = calendar_clock()
    today = date.fromisoformat(clock["today"])
    target, resolved_from = resolve_day(day, today=today)
    calendar_payload = read_calendar_range(username, target, target, limit=200)
    task_payload = read_task_range(username, state="open", first_day=target, last_day=target, limit=200)
    return {
        **clock,
        "requested_day": str(day or "today"),
        "resolved_from": resolved_from,
        "resolved_date": target.isoformat(),
        "resolved_weekday": target.strftime("%A"),
        "events": calendar_payload["events"],
        "due_tasks": task_payload["tasks"],
        "open_undated_tasks": task_payload["open_undated_tasks"],
        "has_scheduled_items": bool(calendar_payload["events"] or task_payload["tasks"]),
        "published_calendar_stale": calendar_payload["published_calendar_stale"],
        "rule": "An event is scheduled on every date from start_date through end_date, inclusive. Undated tasks are not scheduled for this day.",
    }


def temporal_grounding_text() -> str:
    clock = calendar_clock()
    return (
        "Authoritative Tamestorage calendar clock:\n"
        f"- Timezone: {clock['timezone']}\n"
        f"- Local datetime: {clock['local_datetime']}\n"
        f"- Today: {clock['today']} ({clock['today_weekday']})\n"
        f"- Tomorrow: {clock['tomorrow']} ({clock['tomorrow_weekday']})\n"
        "Temporal rules:\n"
        "- These dates override any conflicting date stated earlier in the conversation.\n"
        "- Never calculate relative dates from a previous assistant message. Use current_datetime or read_agenda.\n"
        "- A multi-day event applies on every date from start_date through end_date, inclusive.\n"
        "- An open task without a due date is not scheduled for today or tomorrow."
    )


def _find_relative_phrase(message: str) -> str | None:
    lowered = _normalized_phrase(message)
    for phrase in sorted(_DAY_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", lowered):
            return phrase
    return None


def _human_date(value: str, include_weekday: bool = False) -> str:
    parsed = date.fromisoformat(value)
    pattern = "%A, %B %d, %Y" if include_weekday else "%B %d, %Y"
    return parsed.strftime(pattern).replace(" 0", " ")


def _event_line(item: dict[str, Any]) -> str:
    start = str(item.get("start_date") or "")
    end = str(item.get("end_date") or start)
    date_label = _human_date(start)
    if end and end != start:
        date_label += " to " + _human_date(end)
    if item.get("all_day"):
        time_label = "All day"
    else:
        start_time = str(item.get("start_time") or "")
        end_time = str(item.get("end_time") or "")
        time_label = start_time + (f" to {end_time}" if end_time else "")
    location = f" · {item['location']}" if item.get("location") else ""
    return f"- **{item.get('title') or 'Untitled event'}** · {date_label} · {time_label}{location}"


def _task_line(item: dict[str, Any]) -> str:
    time_label = f" at {item['due_time']}" if item.get("due_time") else ""
    priority = str(item.get("priority") or "none")
    priority_label = f" · {priority.title()} priority" if priority != "none" else ""
    return f"- **{item.get('title') or 'Untitled task'}**{time_label}{priority_label}"


def direct_temporal_answer(username: str | None, message: str) -> str | None:
    """Return deterministic answers for direct date and agenda questions."""
    if not username:
        return None
    text = str(message or "").strip()
    if not text:
        return None

    clock = calendar_clock()
    if _DATE_QUESTION_RE.search(text):
        return f"Today is {_human_date(clock['today'], include_weekday=True)}."

    lowered = _normalized_phrase(text)
    future_event_query = (
        any(word in lowered for word in ("future", "upcoming"))
        and any(word in lowered for word in ("event", "events", "calendar", "schedule"))
    )
    if _FUTURE_EVENTS_RE.search(text) or future_event_query:
        today = date.fromisoformat(clock["today"])
        payload = read_calendar_range(username, today, today + timedelta(days=366), limit=200)
        events = payload["events"]
        if not events:
            return f"You have no calendar events from {_human_date(clock['today'])} onward."
        lines = [f"Your upcoming events from {_human_date(clock['today'])}:", ""]
        lines.extend(_event_line(item) for item in events)
        if payload["truncated"]:
            lines.append("\nOnly the first 200 events are shown.")
        return "\n".join(lines)

    relative_phrase = _find_relative_phrase(text)
    if not relative_phrase or not _SCHEDULE_CUE_RE.search(text):
        return None

    agenda = read_day_agenda(username, relative_phrase)
    date_label = _human_date(agenda["resolved_date"], include_weekday=True)
    events = agenda["events"]
    tasks = agenda["due_tasks"]
    asks_about_tasks_only = (
        any(word in lowered for word in ("task", "tasks", "todo", "todos"))
        and not any(word in lowered for word in ("event", "events", "calendar", "schedule", "free", "busy", "plan", "plans"))
    )
    asks_if_free = "free" in lowered
    asks_if_busy = "busy" in lowered
    is_confirmation = bool(re.search(r"\b(?:so|therefore|then)\b|\bi\s+do\s+have\b", text, re.IGNORECASE))

    if asks_about_tasks_only:
        if not tasks:
            return f"You have no tasks due on {date_label}."
        lines = [f"Tasks due on {date_label}:"]
        lines.extend(_task_line(item) for item in tasks)
        return "\n".join(lines)

    if not events and not tasks:
        if asks_if_free:
            return f"Yes. You have no calendar events or tasks due on {date_label}."
        if asks_if_busy:
            return f"No. You have no calendar events or tasks due on {date_label}."
        return f"{date_label} has no calendar events or tasks due."

    if asks_if_free:
        lead = f"No. You are not free on {date_label}."
    elif asks_if_busy:
        lead = f"Yes. You have scheduled items on {date_label}."
    elif is_confirmation and events:
        event_names = ", ".join(f"**{item['title']}**" for item in events)
        lead = f"Yes. {date_label} has {event_names} scheduled."
    else:
        lead = f"For {date_label}, you have:"

    lines = [lead]
    if events:
        lines.extend(["", "Calendar:"])
        lines.extend(_event_line(item) for item in events)
    if tasks:
        lines.extend(["", "Tasks due:"])
        lines.extend(_task_line(item) for item in tasks)
    return "\n".join(lines)
