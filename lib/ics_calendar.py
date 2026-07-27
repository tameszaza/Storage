from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from flask import current_app

from lib.json_store import read_json, write_json

_ALLOWED_HOSTS = {"outlook.office365.com", "outlook.office.com"}
_MAX_FEED_BYTES = 10 * 1024 * 1024
_WINDOWS_TIMEZONES = {
    "UTC": "UTC",
    "Coordinated Universal Time": "UTC",
    "Singapore Standard Time": "Asia/Singapore",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Taipei Standard Time": "Asia/Taipei",
    "China Standard Time": "Asia/Shanghai",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "India Standard Time": "Asia/Kolkata",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
}


class PublishedCalendarError(RuntimeError):
    pass


def _config_path() -> Path:
    return Path(current_app.config["ICS_CALENDAR_CONFIG_PATH"])


def _cache_path() -> str:
    return current_app.config["ICS_CALENDAR_CACHE_FILE"]


def _zone(name: str | None, fallback: str = "Asia/Singapore") -> ZoneInfo:
    requested = _WINDOWS_TIMEZONES.get(str(name or "").strip(), str(name or "").strip()) or fallback
    try:
        return ZoneInfo(requested)
    except ZoneInfoNotFoundError:
        try:
            return ZoneInfo(fallback)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


def _validated_feed_url(raw_url: str) -> str:
    value = raw_url.strip()
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or hostname not in _ALLOWED_HOSTS:
        raise PublishedCalendarError("The published calendar URL must be an Outlook HTTPS calendar feed.")
    if not parsed.path.lower().endswith(".ics"):
        raise PublishedCalendarError("The published calendar URL must end with .ics.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PublishedCalendarError("The published calendar URL is not valid.")
    return value


def load_config() -> dict[str, Any] | None:
    path = _config_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    raw_url = str(payload.get("url") or payload.get("ics_url") or "").strip()
    if not raw_url:
        return None
    try:
        feed_url = _validated_feed_url(raw_url)
    except PublishedCalendarError:
        return None

    timezone_name = str(payload.get("timezone") or "Asia/Singapore").strip() or "Asia/Singapore"
    try:
        refresh_minutes = int(payload.get("refresh_minutes") or 15)
    except (TypeError, ValueError):
        refresh_minutes = 15
    return {
        "url": feed_url,
        "timezone": timezone_name,
        "refresh_minutes": min(1440, max(5, refresh_minutes)),
    }


def config_status() -> dict[str, Any]:
    config = load_config()
    host = urlparse(config["url"]).hostname if config else ""
    return {
        "configured": config is not None,
        "path": str(_config_path()),
        "host": host or "",
    }


def _fetch_feed(url: str) -> str:
    request = Request(
        url,
        headers={
            "Accept": "text/calendar, text/plain;q=0.9, */*;q=0.1",
            "User-Agent": "Tamestorage-Calendar/1.0",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=25) as response:
            final_url = _validated_feed_url(response.geturl())
            if final_url != response.geturl():
                raise PublishedCalendarError("The published calendar redirected to an invalid address.")
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > _MAX_FEED_BYTES:
                        raise PublishedCalendarError("The published calendar is too large to import.")
                except ValueError:
                    pass
            raw = response.read(_MAX_FEED_BYTES + 1)
            if len(raw) > _MAX_FEED_BYTES:
                raise PublishedCalendarError("The published calendar is too large to import.")
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        if exc.code in {401, 403, 404, 410}:
            message = "The published calendar link is unavailable or no longer shared."
        else:
            message = f"Published calendar request failed ({exc.code})."
        raise PublishedCalendarError(message) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PublishedCalendarError("The published calendar could not be reached.") from exc

    try:
        text = raw.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8-sig", errors="replace")
    if "BEGIN:VCALENDAR" not in text.upper():
        raise PublishedCalendarError("The published calendar returned invalid ICS data.")
    return text


def _unfold_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    unfolded: list[str] = []
    for raw_line in normalized.split("\n"):
        if raw_line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw_line[1:]
        else:
            unfolded.append(raw_line)
    return unfolded


def _split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    quoted = False
    separator = -1
    for index, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            separator = index
            break
    if separator < 1:
        return None

    left, value = line[:separator], line[separator + 1 :]
    pieces = left.split(";")
    name = pieces[0].strip().upper()
    if not name:
        return None
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, marker, raw_value = piece.partition("=")
        if marker:
            params[key.strip().upper()] = raw_value.strip().strip('"')
    return name, params, value


def _parse_events(text: str) -> list[dict[str, list[tuple[dict[str, str], str]]]]:
    events: list[dict[str, list[tuple[dict[str, str], str]]]] = []
    current: dict[str, list[tuple[dict[str, str], str]]] | None = None
    for line in _unfold_lines(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            current = defaultdict(list)
            continue
        if upper == "END:VEVENT":
            if current is not None:
                events.append(dict(current))
            current = None
            continue
        if current is None:
            continue
        parsed = _split_property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        current[name].append((params, value))
    return events


def _first(properties: dict[str, list[tuple[dict[str, str], str]]], name: str) -> tuple[dict[str, str], str] | None:
    values = properties.get(name, [])
    return values[0] if values else None


def _text_value(properties: dict[str, list[tuple[dict[str, str], str]]], name: str) -> str:
    item = _first(properties, name)
    return _unescape_text(item[1]) if item else ""


def _unescape_text(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            following = value[index + 1]
            if following in {"n", "N"}:
                result.append("\n")
            elif following in {"\\", ",", ";"}:
                result.append(following)
            else:
                result.append(following)
            index += 2
            continue
        result.append(value[index])
        index += 1
    return "".join(result).strip()


def _parse_datetime(value: str, params: dict[str, str], display_zone: ZoneInfo) -> date | datetime:
    raw = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or (len(raw) == 8 and raw.isdigit()):
        try:
            return datetime.strptime(raw[:8], "%Y%m%d").date()
        except ValueError as exc:
            raise PublishedCalendarError("The published calendar contains an invalid date.") from exc

    is_utc = raw.endswith("Z")
    if is_utc:
        raw = raw[:-1]
    raw = raw.split(".", 1)[0]
    formats = ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M")
    parsed: datetime | None = None
    for date_format in formats:
        try:
            parsed = datetime.strptime(raw, date_format)
            break
        except ValueError:
            continue
    if parsed is None:
        raise PublishedCalendarError("The published calendar contains an invalid time.")

    if is_utc:
        return parsed.replace(tzinfo=timezone.utc).astimezone(display_zone)
    source_zone = _zone(params.get("TZID"), fallback=display_zone.key)
    return parsed.replace(tzinfo=source_zone).astimezone(display_zone)


def _parse_duration(value: str) -> timedelta | None:
    raw = value.strip().upper()
    if not raw.startswith("P"):
        return None
    raw = raw[1:]
    days = hours = minutes = seconds = 0
    date_part, marker, time_part = raw.partition("T")
    if date_part.endswith("D"):
        try:
            days = int(date_part[:-1] or 0)
        except ValueError:
            return None
    if marker:
        number = ""
        for character in time_part:
            if character.isdigit():
                number += character
                continue
            if not number:
                return None
            amount = int(number)
            number = ""
            if character == "H":
                hours = amount
            elif character == "M":
                minutes = amount
            elif character == "S":
                seconds = amount
            else:
                return None
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _occurrence_key(value: date | datetime) -> str:
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return "T:" + aware.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return "D:" + value.isoformat()


def _event_spec(properties: dict[str, list[tuple[dict[str, str], str]]], display_zone: ZoneInfo) -> dict[str, Any] | None:
    start_property = _first(properties, "DTSTART")
    if start_property is None:
        return None
    start = _parse_datetime(start_property[1], start_property[0], display_zone)

    end: date | datetime | None = None
    end_property = _first(properties, "DTEND")
    if end_property is not None:
        end = _parse_datetime(end_property[1], end_property[0], display_zone)
    if end is None:
        duration_property = _first(properties, "DURATION")
        duration = _parse_duration(duration_property[1]) if duration_property else None
        if duration is None:
            duration = timedelta(days=1) if isinstance(start, date) and not isinstance(start, datetime) else timedelta(hours=1)
        end = start + duration

    uid = _text_value(properties, "UID") or hashlib.sha256(repr(properties).encode("utf-8")).hexdigest()
    recurrence_id_property = _first(properties, "RECURRENCE-ID")
    recurrence_id = (
        _parse_datetime(recurrence_id_property[1], recurrence_id_property[0], display_zone)
        if recurrence_id_property
        else None
    )
    exdates: set[str] = set()
    for params, raw_values in properties.get("EXDATE", []):
        for raw_value in raw_values.split(","):
            if not raw_value.strip():
                continue
            exdates.add(_occurrence_key(_parse_datetime(raw_value, params, display_zone)))

    url = _text_value(properties, "URL")
    if url and urlparse(url).scheme not in {"http", "https"}:
        url = ""
    rrule_property = _first(properties, "RRULE")
    return {
        "uid": uid,
        "title": (_text_value(properties, "SUMMARY") or "Untitled event")[:200],
        "location": _text_value(properties, "LOCATION")[:240],
        "notes": _text_value(properties, "DESCRIPTION")[:2000],
        "url": url[:1000],
        "status": _text_value(properties, "STATUS").upper(),
        "updated_at": _text_value(properties, "LAST-MODIFIED") or _text_value(properties, "DTSTAMP"),
        "start": start,
        "end": end,
        "all_day": isinstance(start, date) and not isinstance(start, datetime),
        "rrule": rrule_property[1].strip() if rrule_property else "",
        "recurrence_id": recurrence_id,
        "exdates": exdates,
    }


def _covered_dates(
    occurrence_start: date | datetime,
    occurrence_end: date | datetime,
    all_day: bool,
) -> tuple[date, date]:
    first_day = occurrence_start.date() if isinstance(occurrence_start, datetime) else occurrence_start

    if all_day:
        exclusive_end = occurrence_end.date() if isinstance(occurrence_end, datetime) else occurrence_end
        last_day = exclusive_end - timedelta(days=1) if exclusive_end > first_day else first_day
    elif isinstance(occurrence_end, datetime):
        # An event ending exactly at midnight belongs to the previous calendar day.
        last_instant = occurrence_end - timedelta(microseconds=1) if occurrence_end > occurrence_start else occurrence_start
        last_day = last_instant.date()
    else:
        last_day = occurrence_end

    return first_day, max(first_day, last_day)


def _overlaps_range(
    occurrence_start: date | datetime,
    occurrence_end: date | datetime,
    all_day: bool,
    start_date: date,
    end_date: date,
) -> bool:
    first_day, last_day = _covered_dates(occurrence_start, occurrence_end, all_day)
    return first_day < end_date and last_day >= start_date


def _normal_event(spec: dict[str, Any], occurrence_start: date | datetime, occurrence_end: date | datetime) -> dict[str, Any]:
    all_day = bool(spec["all_day"])
    first_day, last_day = _covered_dates(occurrence_start, occurrence_end, all_day)
    if isinstance(occurrence_start, datetime):
        start_time = occurrence_start.strftime("%H:%M")
        end_time = occurrence_end.strftime("%H:%M") if isinstance(occurrence_end, datetime) else ""
        start_iso = occurrence_start.isoformat(timespec="minutes")
        end_iso = occurrence_end.isoformat(timespec="minutes") if isinstance(occurrence_end, datetime) else ""
    else:
        start_time = ""
        end_time = ""
        start_iso = occurrence_start.isoformat()
        end_iso = occurrence_end.isoformat()
    identity = hashlib.sha256(f"{spec['uid']}|{_occurrence_key(occurrence_start)}".encode("utf-8")).hexdigest()[:28]
    return {
        "id": f"ics:{identity}",
        "title": spec["title"],
        "date": first_day.isoformat(),
        "end_date": last_day.isoformat(),
        "span_days": (last_day - first_day).days + 1,
        "all_day": all_day,
        "start_time": "" if all_day else start_time,
        "end_time": "" if all_day else end_time,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "location": spec["location"],
        "notes": spec["notes"],
        "source": "ics",
        "readonly": True,
        "web_link": spec["url"],
        "updated_at": spec["updated_at"],
    }


def _duration(spec: dict[str, Any]) -> timedelta:
    return spec["end"] - spec["start"]


def _expand_recurring(spec: dict[str, Any], start_date: date, end_date: date, display_zone: ZoneInfo) -> Iterable[tuple[date | datetime, date | datetime]]:
    if spec["all_day"]:
        start_datetime = datetime.combine(spec["start"], dt_time.min, display_zone)
    else:
        start_datetime = spec["start"]
    range_start = datetime.combine(start_date, dt_time.min, display_zone)
    range_end = datetime.combine(end_date, dt_time.min, display_zone)
    try:
        recurrence = rrulestr(spec["rrule"], dtstart=start_datetime, forceset=True, compatible=True)
        occurrences = recurrence.between(range_start - _duration(spec), range_end, inc=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PublishedCalendarError(f"Could not parse recurrence for “{spec['title']}”.") from exc

    for index, occurrence in enumerate(occurrences):
        if index >= 5000:
            raise PublishedCalendarError("The published calendar contains too many recurring events.")
        if spec["all_day"]:
            occurrence_start: date | datetime = occurrence.astimezone(display_zone).date()
        else:
            occurrence_start = occurrence.astimezone(display_zone)
        occurrence_end = occurrence_start + _duration(spec)
        yield occurrence_start, occurrence_end


def parse_ics_events(text: str, start_date: date, end_date: date, timezone_name: str = "Asia/Singapore") -> list[dict[str, Any]]:
    if end_date <= start_date:
        raise PublishedCalendarError("The calendar sync range is invalid.")
    display_zone = _zone(timezone_name)
    raw_events = _parse_events(text)
    specs = [spec for raw in raw_events if (spec := _event_spec(raw, display_zone)) is not None]

    base_events: list[dict[str, Any]] = []
    overrides: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for spec in specs:
        recurrence_id = spec.get("recurrence_id")
        if recurrence_id is None:
            base_events.append(spec)
        else:
            overrides[spec["uid"]][_occurrence_key(recurrence_id)] = spec

    result: list[dict[str, Any]] = []
    used_overrides: set[tuple[str, str]] = set()
    for spec in base_events:
        if spec["status"] == "CANCELLED":
            continue
        if spec["rrule"]:
            occurrences = _expand_recurring(spec, start_date, end_date, display_zone)
        else:
            occurrences = [(spec["start"], spec["end"])]

        for occurrence_start, occurrence_end in occurrences:
            key = _occurrence_key(occurrence_start)
            override = overrides.get(spec["uid"], {}).get(key)
            if override is not None:
                used_overrides.add((spec["uid"], key))
                if override["status"] != "CANCELLED" and _overlaps_range(override["start"], override["end"], bool(override["all_day"]), start_date, end_date):
                    result.append(_normal_event(override, override["start"], override["end"]))
                continue
            if key in spec["exdates"] or not _overlaps_range(occurrence_start, occurrence_end, bool(spec["all_day"]), start_date, end_date):
                continue
            result.append(_normal_event(spec, occurrence_start, occurrence_end))

    for uid, event_overrides in overrides.items():
        for key, override in event_overrides.items():
            if (uid, key) in used_overrides or override["status"] == "CANCELLED":
                continue
            if _overlaps_range(override["start"], override["end"], bool(override["all_day"]), start_date, end_date):
                result.append(_normal_event(override, override["start"], override["end"]))

    unique: dict[str, dict[str, Any]] = {}
    for item in result:
        unique[item["id"]] = item
    events = list(unique.values())
    events.sort(key=lambda item: (item["date"], item["all_day"] is not True, item["start_time"] or "00:00", item["title"].casefold()))
    return events


def sync_calendar(start_date: date, end_date: date) -> dict[str, Any]:
    config = load_config()
    if config is None:
        raise PublishedCalendarError(f"Published calendar settings are missing from {_config_path()}.")
    text = _fetch_feed(config["url"])
    events = parse_ics_events(text, start_date, end_date, str(config["timezone"]))
    cache = {
        "schema_version": 2,
        "last_sync": datetime.now().astimezone().isoformat(timespec="seconds"),
        "range_start": start_date.isoformat(),
        "range_end": end_date.isoformat(),
        "source_host": urlparse(config["url"]).hostname or "",
        "events": events,
    }
    write_json(_cache_path(), cache)
    return cache


def load_cached_events(start_date: date, end_date: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = read_json(_cache_path(), {})
    if not isinstance(payload, dict):
        payload = {}
    raw_events = payload.get("events") if isinstance(payload.get("events"), list) else []
    events: list[dict[str, Any]] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        try:
            item_start = date.fromisoformat(str(item.get("date") or ""))
            item_end = date.fromisoformat(str(item.get("end_date") or item.get("date") or ""))
        except ValueError:
            continue
        if item_start < end_date and item_end >= start_date:
            events.append(dict(item))
    events.sort(key=lambda item: (item.get("date", ""), item.get("all_day") is not True, item.get("start_time") or "", item.get("title", "").casefold()))
    return events, payload


def cache_needs_refresh(start_date: date, end_date: date) -> bool:
    config = load_config()
    if config is None:
        return False
    payload = read_json(_cache_path(), {})
    if not isinstance(payload, dict):
        return True
    if int(payload.get("schema_version") or 0) < 2:
        return True
    try:
        cached_start = date.fromisoformat(str(payload.get("range_start") or ""))
        cached_end = date.fromisoformat(str(payload.get("range_end") or ""))
        synced_at = datetime.fromisoformat(str(payload.get("last_sync") or ""))
    except ValueError:
        return True
    if cached_start > start_date or cached_end < end_date:
        return True
    if synced_at.tzinfo is None:
        synced_at = synced_at.astimezone()
    max_age = timedelta(minutes=int(config["refresh_minutes"]))
    return datetime.now().astimezone() - synced_at > max_age
