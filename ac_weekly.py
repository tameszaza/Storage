from __future__ import annotations

import json
import math
import os
import threading
from copy import deepcopy
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_KEYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MODE_VALUES = {"off", "1", "2"}
DEFAULT_PRESETS = {
    "1": {"name": "Night", "on_duration_seconds": 600, "off_duration_seconds": 1200},
    "2": {"name": "Day", "on_duration_seconds": 450, "off_duration_seconds": 3150},
}
DEFAULT_WEEKLY_CONFIG = {
    "enabled": False,
    "presets": DEFAULT_PRESETS,
    "days": {
        day: {
            "enabled": False,
            "day_start": "07:30",
            "day_mode": "off",
            "night_start": "22:00",
            "night_mode": "1",
        }
        for day in DAY_KEYS
    },
}


class WeeklyScheduleError(ValueError):
    pass


def _parse_clock(value: Any) -> datetime_time:
    text = str(value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        return datetime_time(hour=hour, minute=minute)
    except (ValueError, TypeError) as error:
        raise WeeklyScheduleError("Times must use HH:MM.") from error


def _duration(value: Any, label: str) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError) as error:
        raise WeeklyScheduleError(f"{label} must be a whole number of seconds.") from error
    if seconds < 5 or seconds > 86400:
        raise WeeklyScheduleError(f"{label} must be between 5 seconds and 24 hours.")
    return seconds


def _mode(value: Any, day: str, period: str) -> str:
    mode = str(value or "off")
    if mode not in MODE_VALUES:
        raise WeeklyScheduleError(f"{day.title()} {period} mode is invalid.")
    return mode


def _migrate_legacy_day(source: dict[str, Any]) -> dict[str, Any]:
    """Convert the earlier one-window day format to day/night modes."""
    if "day_start" in source or "night_start" in source:
        return source
    if "start" not in source or "end" not in source:
        return source

    start_text = str(source.get("start") or "22:00")
    end_text = str(source.get("end") or "07:30")
    start = _parse_clock(start_text)
    end = _parse_clock(end_text)
    preset = str(source.get("preset") or "1")
    if start < end:
        return {
            "enabled": bool(source.get("enabled")),
            "day_start": start_text,
            "day_mode": preset,
            "night_start": end_text,
            "night_mode": "off",
        }
    return {
        "enabled": bool(source.get("enabled")),
        "day_start": end_text,
        "day_mode": "off",
        "night_start": start_text,
        "night_mode": preset,
    }


def validate_weekly_config(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WeeklyScheduleError("Weekly schedule must be an object.")

    raw_presets = raw.get("presets") if isinstance(raw.get("presets"), dict) else {}
    presets: dict[str, dict[str, Any]] = {}
    for preset_id in ("1", "2"):
        source = raw_presets.get(preset_id) if isinstance(raw_presets.get(preset_id), dict) else DEFAULT_PRESETS[preset_id]
        presets[preset_id] = {
            "name": "Night" if preset_id == "1" else "Day",
            "on_duration_seconds": _duration(source.get("on_duration_seconds"), f"Preset {preset_id} ON duration"),
            "off_duration_seconds": _duration(source.get("off_duration_seconds"), f"Preset {preset_id} OFF duration"),
        }

    raw_days = raw.get("days") if isinstance(raw.get("days"), dict) else {}
    days: dict[str, dict[str, Any]] = {}
    for day in DAY_KEYS:
        raw_source = raw_days.get(day) if isinstance(raw_days.get(day), dict) else DEFAULT_WEEKLY_CONFIG["days"][day]
        source = _migrate_legacy_day(raw_source)
        day_start = str(source.get("day_start") or "07:30")
        night_start = str(source.get("night_start") or "22:00")
        day_clock = _parse_clock(day_start)
        night_clock = _parse_clock(night_start)
        if day_clock >= night_clock:
            raise WeeklyScheduleError(f"{day.title()} day start must be before night start.")
        days[day] = {
            "enabled": bool(source.get("enabled")),
            "day_start": day_start,
            "day_mode": _mode(source.get("day_mode"), day, "day"),
            "night_start": night_start,
            "night_mode": _mode(source.get("night_mode") or "1", day, "night"),
        }

    return {"enabled": bool(raw.get("enabled")), "presets": presets, "days": days}


def _clock_seconds(value: str) -> int:
    parsed = _parse_clock(value)
    return parsed.hour * 3600 + parsed.minute * 60


def _round_up_cent(value: float) -> float:
    return math.ceil(max(0.0, value) * 100 - 1e-12) / 100


def _window_estimate(
    window_seconds: int,
    mode: str,
    presets: dict[str, dict[str, Any]],
    rate_per_hour: float,
) -> tuple[float, float, float, float]:
    if mode not in {"1", "2"}:
        return (
            0.0,
            float(window_seconds),
            0.0,
            _round_up_cent(window_seconds / 3600 * rate_per_hour),
        )

    preset = presets[mode]
    on_duration = int(preset["on_duration_seconds"])
    off_duration = int(preset["off_duration_seconds"])
    remaining = float(window_seconds)
    on_seconds = 0.0
    off_seconds = 0.0
    estimated_spent = 0.0
    estimated_saved = 0.0

    while remaining > 0:
        on_segment = min(float(on_duration), remaining)
        on_seconds += on_segment
        estimated_spent += _round_up_cent(on_segment / 3600 * rate_per_hour)
        remaining -= on_segment
        if remaining <= 0:
            break

        off_segment = min(float(off_duration), remaining)
        off_seconds += off_segment
        estimated_saved += _round_up_cent(off_segment / 3600 * rate_per_hour)
        remaining -= off_segment

    return on_seconds, off_seconds, estimated_spent, estimated_saved


def estimate_weekly_cost(raw: dict[str, Any], rate_per_hour: float) -> dict[str, Any]:
    """Estimate weekly ON/OFF hours from the saved day/night plan."""
    config = validate_weekly_config(raw)
    rate = max(0.0, float(rate_per_hour))
    total_on_seconds = 0.0
    total_off_seconds = 0.0
    total_spent = 0.0
    total_saved = 0.0
    active_days = 0
    active_windows = 0

    for day in DAY_KEYS:
        rule = config["days"][day]
        if not rule["enabled"]:
            continue

        active_days += 1
        day_start = _clock_seconds(rule["day_start"])
        night_start = _clock_seconds(rule["night_start"])
        day_seconds = night_start - day_start
        night_seconds = (24 * 60 * 60) - night_start + day_start
        for window_seconds, mode in (
            (day_seconds, rule["day_mode"]),
            (night_seconds, rule["night_mode"]),
        ):
            if mode in {"1", "2"}:
                active_windows += 1
            estimated_on, estimated_off, window_spent, window_saved = _window_estimate(
                window_seconds,
                mode,
                config["presets"],
                rate,
            )
            total_on_seconds += estimated_on
            total_off_seconds += estimated_off
            total_spent += window_spent
            total_saved += window_saved

    return {
        "rate_per_hour": rate,
        "active_days": active_days,
        "active_windows": active_windows,
        "on_seconds": round(total_on_seconds, 2),
        "off_seconds": round(total_off_seconds, 2),
        "on_hours": round(total_on_seconds / 3600, 2),
        "off_hours": round(total_off_seconds / 3600, 2),
        "estimated_spent": round(total_spent, 4),
        "estimated_saved": round(total_saved, 4),
    }


class WeeklyScheduleManager:
    def __init__(
        self,
        path: str,
        timezone_name: str,
        target_callback: Callable[[dict[str, Any] | None], None],
        interval_seconds: float = 15.0,
        default_preset1: dict[str, Any] | None = None,
    ):
        self._path = Path(path).expanduser()
        try:
            self._timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            self._timezone = ZoneInfo("UTC")
        self._target_callback = target_callback
        self._interval_seconds = max(2.0, float(interval_seconds))
        self._condition = threading.Condition(threading.RLock())
        self._callback_lock = threading.Lock()
        self._config = self._load(default_preset1)
        self._last_error: str | None = None
        self._thread = threading.Thread(target=self._run, name="tamestorage-ac-weekly", daemon=True)
        self._thread.start()

    def _default_config(self, default_preset1: dict[str, Any] | None = None) -> dict[str, Any]:
        config = deepcopy(DEFAULT_WEEKLY_CONFIG)
        if default_preset1:
            try:
                config["presets"]["1"]["on_duration_seconds"] = _duration(default_preset1.get("on_duration_seconds"), "Preset 1 ON duration")
                config["presets"]["1"]["off_duration_seconds"] = _duration(default_preset1.get("off_duration_seconds"), "Preset 1 OFF duration")
            except WeeklyScheduleError:
                pass
        return config

    def _load(self, default_preset1: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._path.is_file():
            return self._default_config(default_preset1)
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return validate_weekly_config(payload)
        except (OSError, json.JSONDecodeError, WeeklyScheduleError):
            return self._default_config(default_preset1)

    def _save(self, config: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path)

    def get_config(self) -> dict[str, Any]:
        with self._condition:
            return deepcopy(self._config)

    def save_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        config = validate_weekly_config(raw)
        with self._condition:
            self._save(config)
            self._config = config
            self._condition.notify_all()
        self.tick_once()
        return self.get_config()

    def disable(self) -> bool:
        """Persistently disable automation after any in-flight tick completes."""
        with self._callback_lock:
            with self._condition:
                if not self._config["enabled"]:
                    return False
                config = deepcopy(self._config)
                config["enabled"] = False
                self._save(config)
                self._config = config
                self._condition.notify_all()
        return True

    def _target(self, *, day_key: str, period: str, mode: str, starts_at: datetime, ends_at: datetime, config: dict[str, Any]) -> dict[str, Any]:
        preset = deepcopy(config["presets"].get(mode)) if mode in {"1", "2"} else None
        return {
            "key": f"{day_key}:{period}:{starts_at.isoformat()}:{ends_at.isoformat()}:{mode}",
            "day": day_key,
            "period": period,
            "mode": mode,
            "preset_id": mode if mode in {"1", "2"} else None,
            "preset": preset,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "end": ends_at.strftime("%H:%M"),
        }

    def matching_target(self, now: datetime | None = None) -> dict[str, Any] | None:
        with self._condition:
            config = deepcopy(self._config)
        if not config["enabled"]:
            return None

        local_now = now.astimezone(self._timezone) if now else datetime.now(self._timezone)
        today_key = DAY_KEYS[local_now.weekday()]
        today = config["days"][today_key]
        today_day_start = datetime.combine(local_now.date(), _parse_clock(today["day_start"]), tzinfo=self._timezone)
        today_night_start = datetime.combine(local_now.date(), _parse_clock(today["night_start"]), tzinfo=self._timezone)

        if today["enabled"] and today_day_start <= local_now < today_night_start:
            return self._target(
                day_key=today_key,
                period="day",
                mode=today["day_mode"],
                starts_at=today_day_start,
                ends_at=today_night_start,
                config=config,
            )

        if today["enabled"] and local_now >= today_night_start:
            end_at = datetime.combine(local_now.date() + timedelta(days=1), _parse_clock(today["day_start"]), tzinfo=self._timezone)
            return self._target(
                day_key=today_key,
                period="night",
                mode=today["night_mode"],
                starts_at=today_night_start,
                ends_at=end_at,
                config=config,
            )

        previous_date = local_now.date() - timedelta(days=1)
        previous_key = DAY_KEYS[previous_date.weekday()]
        previous = config["days"][previous_key]
        if previous["enabled"]:
            previous_night_start = datetime.combine(previous_date, _parse_clock(previous["night_start"]), tzinfo=self._timezone)
            previous_night_end = datetime.combine(local_now.date(), _parse_clock(previous["day_start"]), tzinfo=self._timezone)
            if previous_night_start <= local_now < previous_night_end:
                return self._target(
                    day_key=previous_key,
                    period="night",
                    mode=previous["night_mode"],
                    starts_at=previous_night_start,
                    ends_at=previous_night_end,
                    config=config,
                )
        return None

    def get_status(self) -> dict[str, Any]:
        target = self.matching_target()
        with self._condition:
            return {
                "active_window": deepcopy(target),
                "last_error": self._last_error,
                "timezone": str(self._timezone),
            }

    def tick_once(self, now: datetime | None = None) -> None:
        with self._callback_lock:
            target = self.matching_target(now)
            try:
                self._target_callback(deepcopy(target))
                with self._condition:
                    self._last_error = None
            except Exception as error:
                with self._condition:
                    self._last_error = str(error)

    def _run(self) -> None:
        while True:
            self.tick_once()
            with self._condition:
                self._condition.wait(timeout=self._interval_seconds)
