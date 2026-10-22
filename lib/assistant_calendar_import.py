"""Validate calendar entries extracted from an attached image before writing them."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from flask import g

from lib.activity import log_activity
from lib.assistant_agenda import read_calendar_range
from lib.calendar_conflicts import classify_event_match, possibly_same_title, refresh_for_write, title_key
from lib.planner import PlannerValidationError, create_event_once, list_items


_MONTH_ALIASES = {
    1: ("january", "jan", "มกราคม", "มค"),
    2: ("february", "feb", "กุมภาพันธ์", "กพ"),
    3: ("march", "mar", "มีนาคม", "มีค"),
    4: ("april", "apr", "เมษายน", "เมย"),
    5: ("may", "พฤษภาคม", "พค"),
    6: ("june", "jun", "มิถุนายน", "มิย"),
    7: ("july", "jul", "กรกฎาคม", "กค"),
    8: ("august", "aug", "สิงหาคม", "สค"),
    9: ("september", "sept", "sep", "กันยายน", "กย"),
    10: ("october", "oct", "ตุลาคม", "ตค"),
    11: ("november", "nov", "พฤศจิกายน", "พย"),
    12: ("december", "dec", "ธันวาคม", "ธค"),
}
_MONTH_LOOKUP = {alias: month for month, aliases in _MONTH_ALIASES.items() for alias in aliases}
_MONTH_PATTERN = re.compile(
    r"(?<![A-Za-zก-๙])(?:" + "|".join(
        re.escape(alias) for alias in sorted(_MONTH_LOOKUP, key=len, reverse=True)
    ) + r")(?![A-Za-zก-๙])",
    re.IGNORECASE,
)
_WEEKDAY_ALIASES = {
    0: ("monday", "mon", "จันทร์"),
    1: ("tuesday", "tues", "tue", "อังคาร"),
    2: ("wednesday", "wed", "พุธ"),
    3: ("thursday", "thurs", "thur", "thu", "พฤหัสบดี"),
    4: ("friday", "fri", "ศุกร์"),
    5: ("saturday", "sat", "เสาร์"),
    6: ("sunday", "sun", "อาทิตย์"),
}
_WEEKDAY_LOOKUP = {alias: weekday for weekday, aliases in _WEEKDAY_ALIASES.items() for alias in aliases}
_WEEKDAY_PATTERN = re.compile(
    r"(?<![A-Za-zก-๙])(?:วัน)?(" + "|".join(
        re.escape(alias) for alias in sorted(_WEEKDAY_LOOKUP, key=len, reverse=True)
    ) + r")(?![A-Za-zก-๙])",
    re.IGNORECASE,
)


def _calendar_year(value: str) -> int | None:
    if not value:
        return None
    number = int(value)
    if 2400 <= number <= 2700:
        return number - 543
    if 0 <= number <= 99:
        return 2000 + number
    return number


def _evidence_matches_date(evidence: str, expected: date, year_hint: int | None) -> bool:
    """Require an unambiguous visible day/month, not just a plausible model date."""
    visible = unicodedata.normalize("NFKC", evidence).casefold()
    visible = "".join(str(unicodedata.digit(ch)) if ch.isdecimal() else ch for ch in visible)
    weekdays = {_WEEKDAY_LOOKUP[match[1]] for match in _WEEKDAY_PATTERN.finditer(visible)}
    years = {
        _calendar_year(match.group())
        for match in re.finditer(r"(?<!\d)(?:20\d{2}|21\d{2}|24\d{2}|25\d{2}|26\d{2})(?!\d)", visible)
    }
    dates: set[tuple[int, int]] = set()
    ambiguous_numeric = False
    iso_spans: list[tuple[int, int]] = []
    for match in re.finditer(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", visible):
        iso_spans.append(match.span())
        years.add(_calendar_year(match[1]))
        if _calendar_year(match[1]) == expected.year:
            dates.add((int(match[2]), int(match[3])))
    for match in re.finditer(r"(?<!\d)(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?(?!\d)", visible):
        if any(match.start() < end and match.end() > start for start, end in iso_spans):
            continue
        first, second = int(match[1]), int(match[2])
        year = _calendar_year(match[3] or "")
        if year is not None:
            years.add(year)
        if year is not None and year != expected.year:
            continue
        if first <= 12 and second <= 12:
            ambiguous_numeric = True
        elif first > 12 and second <= 12:
            dates.add((second, first))
        elif second > 12 and first <= 12:
            dates.add((first, second))

    named = _MONTH_PATTERN.sub(lambda match: f" @{_MONTH_LOOKUP[match.group().replace('.', '')]} ", visible.replace(".", ""))
    named = re.sub(r"[,|:]", " ", named)
    named_spans: list[tuple[int, int]] = []
    for pattern, month_first in (
        (r"(?<!\d)(\d{1,2})\s+@(\d{1,2})(?:\s+(\d{2,4}))?(?!\d)", False),
        (r"(?<!\d)@(\d{1,2})\s+(\d{1,2})(?:\s+(\d{2,4}))?(?!\d)", True),
    ):
        for match in re.finditer(pattern, named):
            if any(match.start() < end and match.end() > start for start, end in named_spans):
                continue
            named_spans.append(match.span())
            year = _calendar_year(match[3] or "")
            if year is not None:
                years.add(year)
            if year is None or year == expected.year:
                month = int(match[1] if month_first else match[2])
                day = int(match[2] if month_first else match[1])
                dates.add((month, day))
    return (
        not ambiguous_numeric
        and (not weekdays or weekdays == {expected.weekday()})
        and len(years) <= 1
        and (years == {expected.year} or (not years and expected.year == year_hint))
        and dates == {(expected.month, expected.day)}
    )


def _title_key(value: str) -> str:
    return title_key(value)


def _visible_times(evidence: str) -> set[str]:
    """Return unambiguous clock times present in the model's visible-text citation."""
    text = unicodedata.normalize("NFKC", evidence).casefold()
    text = "".join(str(unicodedata.digit(ch)) if ch.isdecimal() else ch for ch in text)
    found: set[str] = set()
    for match in re.finditer(r"(?<!\d)(\d{1,2})[:.](\d{2})\s*(a\.?m\.?|p\.?m\.?)?(?!\d)", text):
        hour, minute = int(match[1]), int(match[2])
        if minute > 59:
            continue
        meridiem = (match[3] or "").replace(".", "")
        if meridiem:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        elif hour > 23:
            continue
        found.add(f"{hour:02d}:{minute:02d}")
    return found


def _clean_candidate(raw: Any, *, year_hint: int | None = None) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw, dict):
        return None, "An extracted entry was not an event."
    title = " ".join(str(raw.get("title") or "").split())[:160]
    date_text = str(raw.get("date") or "").strip()
    evidence = " ".join(str(raw.get("date_evidence") or "").split())
    if not title or not date_text or not evidence:
        return None, f"{title or 'An entry'} needs a readable title and a date with visible evidence."
    try:
        event_date = date.fromisoformat(date_text)
    except ValueError:
        return None, f"{title}: the date {date_text!r} is invalid."
    if not 2000 <= event_date.year <= 2100:
        return None, f"{title}: the year needs checking."
    if not _evidence_matches_date(evidence, event_date, year_hint):
        return None, f"{title}: the date does not unambiguously match the visible evidence."
    if not isinstance(raw.get("all_day", True), bool):
        return None, f"{title}: whether this is an all-day event needs checking."
    all_day = raw.get("all_day", True)
    start_time = str(raw.get("start_time") or "").strip()
    end_time = str(raw.get("end_time") or "").strip()
    time_evidence = " ".join(str(raw.get("time_evidence") or "").split())
    if not all_day and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time):
        return None, f"{title}: the start time needs checking."
    if end_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end_time):
        return None, f"{title}: the end time needs checking."
    if end_time and start_time and end_time <= start_time:
        return None, f"{title}: the end time must be after the start time."
    if all_day and (start_time or end_time or time_evidence):
        return None, f"{title}: the image checks disagree on whether this is an all-day event."
    if not all_day:
        visible_times = _visible_times(time_evidence)
        if start_time not in visible_times or (end_time and end_time not in visible_times):
            return None, f"{title}: the time does not match readable time evidence in the image."
    return {
        "title": title,
        "date": event_date.isoformat(),
        "all_day": all_day,
        "start_time": "" if all_day else start_time,
        "end_time": "" if all_day else end_time,
        "date_evidence": evidence,
        "time_evidence": time_evidence,
    }, ""


def _signature(item: dict[str, Any]) -> tuple[str, str, bool, str, str]:
    return (
        _title_key(item.get("title", "")),
        str(item.get("date") or item.get("start_date") or ""),
        bool(item.get("all_day", True)),
        str(item.get("start_time") or ""),
        str(item.get("end_time") or ""),
    )


def prepare_verified_events(
    extracted: list[Any], verified: list[Any], *, year_hint: int | None = None,
) -> dict[str, Any]:
    """Prepare only events independently agreed by both image reads.

    This stage never writes the calendar. The user can review the proposed
    dates against the source image before a separate confirmation step.
    """
    if not isinstance(extracted, list) or not isinstance(verified, list):
        raise ValueError("The image result was incomplete; no events were added.")
    if len(extracted) > 50 or len(verified) > 50:
        raise ValueError("Too many entries were detected in one image; no events were added.")

    verified_candidates: dict[tuple[str, str, bool, str, str], dict[str, Any]] = {}
    for raw in verified:
        candidate, _ = _clean_candidate(raw, year_hint=year_hint)
        if candidate:
            verified_candidates.setdefault(_signature(candidate), candidate)

    candidates: list[dict[str, Any]] = []
    review: list[str] = []
    seen: set[tuple[str, str, bool, str, str]] = set()
    for raw in extracted:
        candidate, error = _clean_candidate(raw, year_hint=year_hint)
        if error:
            review.append(error)
            continue
        assert candidate is not None
        signature = _signature(candidate)
        if signature in seen:
            continue
        seen.add(signature)
        if signature not in verified_candidates:
            candidate["verified"] = False
            candidate["image_read"] = "first"
            candidate["review_reason"] = "Not confirmed by the second image check"
            candidates.append(candidate)
            review.append(f"{candidate['title']} ({candidate['date']}): the image checks disagreed; please check it.")
            continue
        candidate["verified"] = True
        candidate["image_read"] = "both"
        candidates.append(candidate)
    for signature, candidate in verified_candidates.items():
        if signature in seen:
            continue
        if len(candidates) >= 50:
            review.append("Additional entries from the second image check could not be shown; please inspect the photo for omissions.")
            break
        seen.add(signature)
        candidate["verified"] = False
        candidate["image_read"] = "second"
        candidate["review_reason"] = "Found only by the second image check"
        candidates.append(candidate)
        review.append(f"{candidate['title']} ({candidate['date']}): the image checks disagreed; found only in the second check.")
    # A read may contain both a mistaken date and the date the other read
    # confirms. Treat nearby versions as alternatives even when one is verified;
    # otherwise both checkboxes could be submitted as separate events.
    parents = list(range(len(candidates)))

    def root(index: int) -> int:
        while parents[index] != index:
            index = parents[index]
        return index

    for index, candidate in enumerate(candidates):
        for other_index in range(index + 1, len(candidates)):
            other = candidates[other_index]
            if (candidate["image_read"] == other["image_read"]
                    or candidate["date"] == other["date"]
                    or not possibly_same_title(candidate["title"], other["title"])):
                continue
            parents[root(other_index)] = root(index)
    group_sizes: dict[int, int] = {}
    for index in range(len(candidates)):
        group_sizes[root(index)] = group_sizes.get(root(index), 0) + 1
    for index, candidate in enumerate(candidates):
        group = root(index)
        if group_sizes[group] > 1:
            candidate["conflict_group"] = f"image-disagreement:{group}"
            candidate["review_reason"] = "Two possible dates; select only the correct one"
        candidate.pop("image_read", None)
    return {"candidates": candidates, "review": review}


def _clean_confirmed_candidate(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """Validate a row explicitly corrected or accepted on the review page."""
    if not isinstance(raw, dict):
        return None, "An event row was invalid."
    title = " ".join(str(raw.get("title") or "").split())[:160]
    if not title:
        return None, "An event needs a title."
    try:
        event_date = date.fromisoformat(str(raw.get("date") or ""))
    except ValueError:
        return None, f"{title}: enter a valid date."
    if not 2000 <= event_date.year <= 2100:
        return None, f"{title}: the year needs checking."
    all_day = raw.get("all_day")
    if not isinstance(all_day, bool):
        return None, f"{title}: choose all day or a start time."
    start_time = str(raw.get("start_time") or "").strip()
    end_time = str(raw.get("end_time") or "").strip()
    if not all_day:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time):
            return None, f"{title}: enter a valid start time."
        if end_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end_time):
            return None, f"{title}: enter a valid end time."
        if end_time and end_time <= start_time:
            return None, f"{title}: the end time must be after the start time."
    return {
        "title": title, "date": event_date.isoformat(), "all_day": all_day,
        "start_time": "" if all_day else start_time,
        "end_time": "" if all_day else end_time,
        "conflict_group": str(raw.get("conflict_group") or "")[:200],
    }, ""


def _commit_candidates(
    username: str, candidates: list[dict[str, Any]], *, source: str,
) -> dict[str, Any]:
    """Recheck live calendars and duplicates immediately before each write."""
    # A stale published calendar can hide an existing event. Refresh once for
    # the full proposed range before any local event is written.
    candidate_days = [date.fromisoformat(candidate["date"]) for candidate in candidates]
    calendar_unavailable = (
        refresh_for_write(username, min(candidate_days), max(candidate_days)) if candidate_days else ""
    )

    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    review: list[str] = []
    seen: set[tuple[str, str, bool, str, str]] = set()
    for candidate in candidates:
        signature = _signature(candidate)
        if signature in seen:
            continue
        seen.add(signature)
        if calendar_unavailable:
            review.append(f"{candidate['title']} ({candidate['date']}): {calendar_unavailable}; this event was not added.")
            continue

        day = date.fromisoformat(candidate["date"])
        local_events = list_items(username)["events"]
        calendar_view = read_calendar_range(username, day, day, limit=200)
        if calendar_view.get("published_calendar_stale") or calendar_view.get("truncated"):
            review.append(f"{candidate['title']} ({candidate['date']}): existing calendar entries could not be fully verified; this event was not added.")
            continue
        remote_events = calendar_view["events"]
        match, _item = classify_event_match(
            candidate["title"], day, day, candidate["all_day"],
            candidate["start_time"], candidate["end_time"], [*local_events, *remote_events],
        )
        if match == "existing":
            existing.append(candidate)
            continue
        if match == "conflict":
            review.append(f"{candidate['title']} ({candidate['date']}): an event with this title already overlaps this date; please check it.")
            continue
        if match == "similar":
            review.append(f"{candidate['title']} ({candidate['date']}): a similarly named event already exists; please check it.")
            continue

        try:
            item, was_created = create_event_once(username, {
                "title": candidate["title"],
                "date": candidate["date"],
                "end_date": candidate["date"],
                "all_day": candidate["all_day"],
                "start_time": candidate["start_time"],
                "end_time": candidate["end_time"],
            })
        except PlannerValidationError as exc:
            review.append(f"{candidate['title']} ({candidate['date']}): {exc}")
            continue
        if not was_created:
            existing.append(candidate)
            continue
        created.append(item)
        g.assistant_actions = getattr(g, "assistant_actions", []) + [
            {"action": "calendar.create", "title": item["title"], "url": "/planner"}
        ]
        log_activity("ai.calendar.create", item["title"], details={"date": item["date"], "source": source})

    return {"created": created, "existing": existing, "review": review}


def import_verified_events(
    username: str, extracted: list[Any], verified: list[Any], *, year_hint: int | None = None,
) -> dict[str, Any]:
    """Compatibility path for previously verified imports and direct callers."""
    prepared = prepare_verified_events(extracted, verified, year_hint=year_hint)
    result = _commit_candidates(
        username, [candidate for candidate in prepared["candidates"] if candidate.get("verified")], source="image",
    )
    result["review"] = prepared["review"] + result["review"]
    return result


def import_confirmed_events(username: str, rows: list[Any]) -> dict[str, Any]:
    """Import user-reviewed rows, still checking all live calendars for duplicates."""
    if not isinstance(rows, list) or len(rows) > 50:
        raise ValueError("Too many events were selected; nothing was added.")
    candidates: list[dict[str, Any]] = []
    review: list[str] = []
    for raw in rows:
        candidate, error = _clean_confirmed_candidate(raw)
        if error:
            review.append(error)
        else:
            assert candidate is not None
            candidates.append(candidate)
    # A malformed selected row must not cause a silent partial import.
    if review:
        raise ValueError(" ".join(review))
    groups: set[str] = set()
    for candidate in candidates:
        group = candidate.get("conflict_group", "")
        if group:
            if group in groups:
                raise ValueError("Two image checks proposed conflicting versions of one event. Select only the correct date, then try again.")
            groups.add(group)
    return _commit_candidates(username, candidates, source="image_confirmed")


def format_import_result(result: dict[str, Any]) -> str:
    lines: list[str] = []
    if result["created"]:
        lines.append(f"Added {len(result['created'])} event(s) to your calendar:")
        lines.extend(f"- {item['date']}: {item['title']}" for item in result["created"])
    if result["existing"]:
        lines.append(f"Skipped {len(result['existing'])} already on your calendar.")
    if result["review"]:
        lines.append("Please review these before adding them:")
        lines.extend(f"- {message}" for message in result["review"])
    return "\n".join(lines) or "I could not read a complete dated event from the image. Please send a clearer image or type the dates."
