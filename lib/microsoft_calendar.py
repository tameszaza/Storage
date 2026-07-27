from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from flask import current_app

from lib.json_store import read_json, write_json

_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_TOKEN_LOCK = threading.RLock()
_TOKEN_CACHE: dict[str, Any] = {"access_token": "", "expires_at": 0.0, "key": ""}


class MicrosoftCalendarError(RuntimeError):
    pass


def _config_path() -> Path:
    return Path(current_app.config["MICROSOFT_CALENDAR_CONFIG_PATH"])


def _cache_path() -> str:
    return current_app.config["MICROSOFT_CALENDAR_CACHE_FILE"]


def load_config() -> dict[str, str] | None:
    path = _config_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    result = {
        "tenant_id": str(payload.get("tenant_id") or "").strip(),
        "client_id": str(payload.get("client_id") or "").strip(),
        "client_secret": str(payload.get("client_secret") or "").strip(),
        "user_id": str(payload.get("user_id") or payload.get("user_principal_name") or "").strip(),
        "timezone": str(payload.get("timezone") or "Singapore Standard Time").strip(),
    }
    if not all(result[key] for key in ("tenant_id", "client_id", "client_secret", "user_id")):
        return None
    return result


def config_status() -> dict[str, Any]:
    config = load_config()
    return {
        "configured": config is not None,
        "path": str(_config_path()),
        "user_id": config.get("user_id", "") if config else "",
    }


def _json_request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, form: dict[str, str] | None = None) -> dict[str, Any]:
    body = urlencode(form).encode("utf-8") if form is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if form is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = payload.get("error_description") or payload.get("error", {}).get("message") or ""
        except (json.JSONDecodeError, AttributeError, OSError):
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise MicrosoftCalendarError(f"Microsoft Calendar request failed ({exc.code}){suffix}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise MicrosoftCalendarError("Microsoft Calendar could not be reached.") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MicrosoftCalendarError("Microsoft Calendar returned an invalid response.") from exc
    if not isinstance(payload, dict):
        raise MicrosoftCalendarError("Microsoft Calendar returned an unexpected response.")
    return payload


def _access_token(config: dict[str, str]) -> str:
    cache_key = f"{config['tenant_id']}:{config['client_id']}:{hash(config['client_secret'])}"
    now = time.time()
    with _TOKEN_LOCK:
        if _TOKEN_CACHE.get("key") == cache_key and _TOKEN_CACHE.get("expires_at", 0) > now + 60:
            return str(_TOKEN_CACHE["access_token"])

        tenant = quote(config["tenant_id"], safe="")
        payload = _json_request(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            method="POST",
            form={
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        token = str(payload.get("access_token") or "")
        if not token:
            raise MicrosoftCalendarError("Microsoft did not return an access token.")
        expires_in = max(300, int(payload.get("expires_in") or 3600))
        _TOKEN_CACHE.update({"access_token": token, "expires_at": now + expires_in, "key": cache_key})
        return token


def _event_date_time(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", ""
    raw = str(value.get("dateTime") or "").strip()
    if not raw:
        return "", ""
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.date().isoformat(), parsed.strftime("%H:%M")
    except ValueError:
        return raw[:10], raw[11:16] if len(raw) >= 16 else ""


def _normalize_event(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("isCancelled"):
        return None
    event_date, start_time = _event_date_time(item.get("start"))
    _, end_time = _event_date_time(item.get("end"))
    if not event_date:
        return None
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    return {
        "id": f"ms:{item.get('id', '')}",
        "title": str(item.get("subject") or "Untitled event")[:200],
        "date": event_date,
        "all_day": bool(item.get("isAllDay")),
        "start_time": "" if item.get("isAllDay") else start_time,
        "end_time": "" if item.get("isAllDay") else end_time,
        "location": str(location.get("displayName") or "")[:240],
        "notes": "",
        "source": "microsoft",
        "readonly": True,
        "web_link": str(item.get("webLink") or ""),
        "show_as": str(item.get("showAs") or ""),
        "updated_at": str(item.get("lastModifiedDateTime") or ""),
    }


def sync_calendar(start_date: date, end_date: date) -> dict[str, Any]:
    config = load_config()
    if config is None:
        raise MicrosoftCalendarError(f"Microsoft Calendar credentials are missing from {_config_path()}.")
    if end_date <= start_date:
        raise MicrosoftCalendarError("The calendar sync range is invalid.")

    token = _access_token(config)
    user = quote(config["user_id"], safe="")
    query = urlencode(
        {
            "startDateTime": f"{start_date.isoformat()}T00:00:00+08:00",
            "endDateTime": f"{end_date.isoformat()}T00:00:00+08:00",
            "$select": "id,subject,start,end,isAllDay,location,webLink,showAs,isCancelled,lastModifiedDateTime",
            "$orderby": "start/dateTime",
            "$top": "200",
        }
    )
    next_url = f"{_GRAPH_ROOT}/users/{user}/calendarView?{query}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": f'outlook.timezone="{config["timezone"]}"',
    }
    events: list[dict[str, Any]] = []
    while next_url and len(events) < 1000:
        if not next_url.startswith(_GRAPH_ROOT):
            raise MicrosoftCalendarError("Microsoft Calendar returned an unsafe continuation URL.")
        payload = _json_request(next_url, headers=headers)
        for raw_item in payload.get("value", []):
            if not isinstance(raw_item, dict):
                continue
            normalized = _normalize_event(raw_item)
            if normalized:
                events.append(normalized)
        next_value = payload.get("@odata.nextLink")
        next_url = str(next_value) if next_value else ""

    cache = {
        "last_sync": datetime.now().astimezone().isoformat(timespec="seconds"),
        "range_start": start_date.isoformat(),
        "range_end": end_date.isoformat(),
        "user_id": config["user_id"],
        "events": events,
    }
    write_json(_cache_path(), cache)
    return cache


def load_cached_events(start_date: date, end_date: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = read_json(_cache_path(), {})
    if not isinstance(payload, dict):
        payload = {}
    raw_events = payload.get("events") if isinstance(payload.get("events"), list) else []
    events = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        try:
            item_date = date.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            continue
        if start_date <= item_date < end_date:
            events.append(dict(item))
    events.sort(key=lambda item: (item.get("date", ""), item.get("start_time") or "", item.get("title", "").casefold()))
    return events, payload


def cache_needs_refresh(start_date: date, end_date: date, *, max_age_seconds: int = 900) -> bool:
    payload = read_json(_cache_path(), {})
    if not isinstance(payload, dict):
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
    return (datetime.now().astimezone() - synced_at) > timedelta(seconds=max_age_seconds)
