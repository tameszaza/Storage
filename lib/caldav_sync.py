"""Small CalDAV bridge for the Tames planner.

The planner remains the browser UI; this module makes the same events available
to a standard CalDAV client such as GNOME Evolution.  It deliberately uses only
the Python standard library so the always-on sync worker stays very light.
"""
from __future__ import annotations

import base64
import logging
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as clock_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from flask import current_app

from lib.planner import list_items, replace_events, update_event_metadata

LOGGER = logging.getLogger(__name__)
DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"


class CalendarSyncError(RuntimeError):
    """Raised for a reachable but unusable CalDAV service."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _settings() -> tuple[str, str, str, ZoneInfo]:
    url = str(current_app.config.get("CALDAV_INTERNAL_URL") or "").strip().rstrip("/") + "/"
    username = str(current_app.config.get("CALDAV_USERNAME") or "").strip()
    password = str(current_app.config.get("CALDAV_PASSWORD") or "")
    if not url or not username or not password:
        raise CalendarSyncError("Calendar sync is not configured.")
    try:
        zone = ZoneInfo(str(current_app.config.get("APP_TIMEZONE") or "Asia/Singapore"))
    except Exception:
        zone = ZoneInfo("Asia/Singapore")
    return url, username, password, zone


def _request(method: str, url: str, username: str, password: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    request_headers = {"Authorization": f"Basic {token}", "User-Agent": "Tamestorage-CalDAV/1.0"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read()
    except urllib.error.URLError as exc:
        raise CalendarSyncError("Calendar service is unavailable.") from exc


def _calendar_creation_xml() -> bytes:
    return b'''<?xml version="1.0" encoding="UTF-8"?>
<D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set><D:prop><D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
  <D:displayname>Tames schedule</D:displayname>
  <C:supported-calendar-component-set><C:comp name="VEVENT"/></C:supported-calendar-component-set>
  </D:prop></D:set>
</D:mkcol>'''


def ensure_calendar() -> None:
    url, username, password, _ = _settings()
    status, _, _ = _request("MKCOL", url, username, password, body=_calendar_creation_xml(), headers={"Content-Type": "application/xml; charset=utf-8"})
    if status not in {200, 201, 204, 405}:
        raise CalendarSyncError(f"Calendar setup failed (HTTP {status}).")


def _escape(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _unescape(value: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda match: "\n" if match.group(1).lower() == "n" else match.group(1), value)


def _fold(line: str) -> str:
    # RFC 5545 folding; titles here are short but folding keeps interoperability.
    chunks, current, size = [], [], 0
    for character in line:
        width = len(character.encode("utf-8"))
        if current and size + width > 73:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(character)
        size += width
    chunks.append("".join(current))
    return "\r\n ".join(chunks)


def _event_ics(item: dict[str, Any], uid: str, zone: ZoneInfo) -> bytes:
    event_date = date.fromisoformat(str(item["date"]))
    end_date = date.fromisoformat(str(item.get("end_date") or item["date"]))
    if end_date < event_date:
        end_date = event_date
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Tamestorage//Planner//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT", f"UID:{_escape(uid)}", f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}", f"SUMMARY:{_escape(item.get('title'))}"]
    if item.get("all_day"):
        lines.extend((f"DTSTART;VALUE=DATE:{event_date:%Y%m%d}", f"DTEND;VALUE=DATE:{(end_date + timedelta(days=1)):%Y%m%d}"))
    else:
        start_at = clock_time.fromisoformat(str(item.get("start_time") or "09:00"))
        end_at = clock_time.fromisoformat(str(item.get("end_time") or "")) if item.get("end_time") else None
        starts = datetime.combine(event_date, start_at, tzinfo=zone)
        if end_at:
            ends = datetime.combine(end_date, end_at, tzinfo=zone)
        else:
            ends = starts + timedelta(hours=1)
        lines.extend((f"DTSTART;TZID={zone.key}:{starts:%Y%m%dT%H%M%S}", f"DTEND;TZID={zone.key}:{ends:%Y%m%dT%H%M%S}"))
    if item.get("location"):
        lines.append(f"LOCATION:{_escape(item['location'])}")
    if item.get("notes"):
        lines.append(f"DESCRIPTION:{_escape(item['notes'])}")
    lines.extend((f"X-TAMESTORAGE-ID:{_escape(item.get('id'))}", "END:VEVENT", "END:VCALENDAR", ""))
    return "\r\n".join(_fold(line) for line in lines).encode("utf-8")


def _unfold(content: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", content).replace("\r\n", "\n").split("\n")


def _properties(content: str) -> dict[str, tuple[str, str]]:
    properties: dict[str, tuple[str, str]] = {}
    in_event = False
    for line in _unfold(content):
        if line == "BEGIN:VEVENT":
            in_event = True
            continue
        if line == "END:VEVENT":
            break
        if not in_event or ":" not in line:
            continue
        key, value = line.split(":", 1)
        name = key.split(";", 1)[0].upper()
        properties.setdefault(name, (key, value))
    return properties


def _event_datetime(raw_key: str, raw_value: str, zone: ZoneInfo) -> tuple[str, str]:
    is_date = "VALUE=DATE" in raw_key.upper() or "T" not in raw_value
    if is_date:
        parsed = datetime.strptime(raw_value[:8], "%Y%m%d").date()
        return parsed.isoformat(), ""
    raw = raw_value.rstrip("Z")
    parsed = datetime.strptime(raw[:15], "%Y%m%dT%H%M%S")
    if raw_value.endswith("Z"):
        parsed = parsed.replace(tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)
    return parsed.date().isoformat(), parsed.strftime("%H:%M")


def _remote_event(content: str, href: str, etag: str, zone: ZoneInfo) -> dict[str, Any] | None:
    props = _properties(content)
    if not {"UID", "SUMMARY", "DTSTART"}.issubset(props):
        return None
    try:
        start_date, start_time = _event_datetime(*props["DTSTART"], zone)
        all_day = "VALUE=DATE" in props["DTSTART"][0].upper() or "T" not in props["DTSTART"][1]
        if "DTEND" in props:
            end_date, end_time = _event_datetime(*props["DTEND"], zone)
            if all_day:
                end_date = (date.fromisoformat(end_date) - timedelta(days=1)).isoformat()
        else:
            end_date, end_time = start_date, ""
    except (ValueError, TypeError):
        return None
    return {
        "caldav_uid": _unescape(props["UID"][1]).strip(),
        "caldav_href": href,
        "caldav_etag": etag,
        "title": _unescape(props["SUMMARY"][1]).strip()[:160] or "Untitled event",
        "date": start_date,
        "end_date": end_date or start_date,
        "all_day": all_day,
        "start_time": "" if all_day else start_time,
        "end_time": "" if all_day else end_time,
        "location": _unescape(props.get("LOCATION", ("", ""))[1]).strip()[:180],
        "notes": _unescape(props.get("DESCRIPTION", ("", ""))[1]).strip()[:1000],
        "source": "caldav",
        "updated_at": _now(),
    }


def _remote_events() -> dict[str, dict[str, Any]]:
    url, username, password, zone = _settings()
    report = b'''<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
 <D:prop><D:getetag/><C:calendar-data/></D:prop>
 <C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT"/></C:comp-filter></C:filter>
</C:calendar-query>'''
    status, _, body = _request("REPORT", url, username, password, body=report, headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
    if status not in {200, 207}:
        raise CalendarSyncError(f"Calendar read failed (HTTP {status}).")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise CalendarSyncError("Calendar returned an invalid response.") from exc
    found: dict[str, dict[str, Any]] = {}
    for response in root.findall(f"{{{DAV}}}response"):
        href = response.findtext(f"{{{DAV}}}href") or ""
        data = response.findtext(f".//{{{CALDAV}}}calendar-data") or ""
        etag = response.findtext(f".//{{{DAV}}}getetag") or ""
        event = _remote_event(data, href, etag, zone)
        if event and event["caldav_uid"]:
            found[event["caldav_uid"]] = event
    return found


def upsert_event(username: str, item: dict[str, Any]) -> dict[str, Any]:
    """Write one web event to CalDAV and persist its remote identity."""
    if username != "Admin":
        return item
    ensure_calendar()
    url, auth_user, password, zone = _settings()
    uid = str(item.get("caldav_uid") or "").strip() or f"tames-{secrets.token_urlsafe(18)}"
    resource = url + urllib.parse.quote(uid, safe="@._-") + ".ics"
    status, headers, _ = _request("PUT", resource, auth_user, password, body=_event_ics(item, uid, zone), headers={"Content-Type": "text/calendar; charset=utf-8"})
    if status not in {200, 201, 204}:
        raise CalendarSyncError(f"Calendar update failed (HTTP {status}).")
    metadata = {"caldav_uid": uid, "caldav_href": urllib.parse.urlparse(resource).path, "caldav_etag": headers.get("ETag", ""), "source": "caldav"}
    updated = update_event_metadata(username, str(item.get("id") or ""), metadata)
    return updated or {**item, **metadata}


def delete_remote_event(username: str, item: dict[str, Any]) -> None:
    if username != "Admin" or not item.get("caldav_uid"):
        return
    url, auth_user, password, _ = _settings()
    resource = url + urllib.parse.quote(str(item["caldav_uid"]), safe="@._-") + ".ics"
    status, _, _ = _request("DELETE", resource, auth_user, password)
    if status not in {200, 204, 404}:
        raise CalendarSyncError(f"Calendar deletion failed (HTTP {status}).")


def sync_user_events(username: str = "Admin") -> dict[str, int]:
    """Merge the local planner cache with CalDAV. Remote changes win conflicts."""
    if username != "Admin":
        return {"pushed": 0, "pulled": 0, "removed": 0}
    ensure_calendar()
    remote = _remote_events()
    events = list_items(username)["events"]
    fresh: set[str] = set()
    pushed = 0
    for item in events:
        if not item.get("caldav_uid"):
            updated = upsert_event(username, item)
            fresh.add(str(updated.get("caldav_uid") or ""))
            pushed += 1
    if fresh:
        events = list_items(username)["events"]
    local_by_uid = {str(item.get("caldav_uid")): item for item in events if item.get("caldav_uid")}
    merged: list[dict[str, Any]] = []
    pulled = 0
    for uid, remote_item in remote.items():
        local = local_by_uid.pop(uid, None)
        if local:
            merged.append({**local, **remote_item, "id": local.get("id"), "created_at": local.get("created_at") or _now()})
        else:
            merged.append({"id": secrets.token_urlsafe(12), "created_at": _now(), **remote_item})
            pulled += 1
    # Events written during this sync are not in the original REPORT response.
    for uid, item in local_by_uid.items():
        if uid in fresh:
            merged.append(item)
    replace_events(username, merged)
    return {"pushed": pushed, "pulled": pulled, "removed": len(local_by_uid) - len(fresh)}


def status() -> dict[str, str | bool]:
    try:
        public_url = str(current_app.config.get("CALDAV_PUBLIC_URL") or "").strip()
        return {"configured": bool(public_url and current_app.config.get("CALDAV_USERNAME")), "url": public_url}
    except RuntimeError:
        return {"configured": False, "url": ""}
