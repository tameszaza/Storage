"""Shared calendar duplicate checks for assistant-created events."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

from lib.ics_calendar import PublishedCalendarError, cache_needs_refresh, sync_calendar


def title_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"\w+", normalized))


def possibly_same_title(left: str, right: str) -> bool:
    first, second = title_key(left), title_key(right)
    if not first or not second:
        return False
    if first == second:
        return True
    first_numbers = set(re.findall(r"\d+", first))
    second_numbers = set(re.findall(r"\d+", second))
    if first_numbers and second_numbers and not (
        first_numbers <= second_numbers or second_numbers <= first_numbers
    ):
        return False
    return SequenceMatcher(None, first, second).ratio() >= 0.86


def refresh_for_write(username: str, first_day: date, last_day: date) -> str:
    """Return a reason to defer a write if the published calendar is unverified."""
    if username != "Admin":
        return ""
    if (last_day - first_day).days > 366:
        return "the published calendar range is too wide to verify automatically"
    exclusive_end = last_day + timedelta(days=1)
    if cache_needs_refresh(first_day, exclusive_end):
        try:
            sync_calendar(first_day, exclusive_end)
        except PublishedCalendarError:
            return "the published calendar could not be refreshed to check for duplicates"
    return ""


def classify_event_match(
    title: str, first_day: date, last_day: date, all_day: bool,
    start_time: str, end_time: str, events: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    """Return ``existing``, ``conflict``, ``similar``, or ``clear``."""
    wanted = title_key(title)
    similar: dict[str, Any] | None = None
    for item in events:
        try:
            item_start = date.fromisoformat(str(item.get("start_date") or item.get("date") or "")[:10])
            item_end = date.fromisoformat(str(item.get("end_date") or item.get("date") or item.get("start_date") or "")[:10])
        except ValueError:
            continue
        if item_start > last_day or item_end < first_day:
            continue
        current = title_key(item.get("title", ""))
        if not current:
            continue
        if current == wanted:
            if (
                str(item.get("start_date") or item.get("date") or "")[:10] == first_day.isoformat()
                and str(item.get("end_date") or item.get("date") or item.get("start_date") or "")[:10] == last_day.isoformat()
                and bool(item.get("all_day")) == all_day
                and str(item.get("start_time") or "") == start_time
                and str(item.get("end_time") or "") == end_time
            ):
                return "existing", item
            return "conflict", item
        if possibly_same_title(current, wanted):
            similar = item
    return ("similar", similar) if similar else ("clear", None)
