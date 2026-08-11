from __future__ import annotations

import json
import logging
import os
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.ac_history import AcHistoryStore
from lib.phone_commands import PhoneCommandCancelled, phone_command_queue
from lib.ac_usage import AcUsageStore
from lib.ac_weekly import WeeklyScheduleManager
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULT_SETTINGS: dict[str, Any] = {
    "root_url": "http://127.0.0.1:8080",
    "on_duration_seconds": 600,
    "off_duration_seconds": 1200,
    "max_cycles": 0,
    "request_timeout_seconds": 5,
    "turn_off_on_stop": True,
}

MIN_DURATION_SECONDS = 5
MAX_DURATION_SECONDS = 24 * 60 * 60
MAX_CYCLES = 10_000
MIN_REQUEST_TIMEOUT_SECONDS = 2
MAX_REQUEST_TIMEOUT_SECONDS = 60
ACTION_PATHS = {"on": "ac_on", "off": "ac_off"}
WAIT_PHASE_KEYS = {"on_wait", "off_wait"}


class AcControlError(RuntimeError):
    """Raised when AC settings or MacroDroid execution is invalid."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_root_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AcControlError("MacroDroid root URL must be a complete HTTP or HTTPS URL.")
    if parsed.query or parsed.fragment:
        raise AcControlError("MacroDroid root URL cannot contain a query string or fragment.")

    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def build_action_url(root_url: str, action: str) -> str:
    if action not in ACTION_PATHS:
        raise AcControlError("Unsupported AC action.")
    root = _clean_root_url(root_url)
    if not root:
        raise AcControlError("Configure the MacroDroid root URL first.")
    return f"{root}/{ACTION_PATHS[action]}"


def _int_setting(
    value: Any,
    field_name: str,
    minimum: int,
    maximum: int,
    *,
    allow_zero: bool = False,
) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise AcControlError(f"{field_name} must be a whole number.") from error

    if allow_zero and parsed == 0:
        return 0
    if parsed < minimum or parsed > maximum:
        raise AcControlError(f"{field_name} must be between {minimum} and {maximum}.")
    return parsed


def _legacy_root_url(stored: dict[str, Any]) -> str:
    on_url = str(stored.get("on_webhook_url") or "").strip()
    off_url = str(stored.get("off_webhook_url") or "").strip()
    if not on_url and not off_url:
        return ""

    roots: list[str] = []
    for url, expected_suffix in ((on_url, "/ac_on"), (off_url, "/ac_off")):
        if not url:
            continue
        clean = url.rstrip("/")
        if clean.endswith(expected_suffix):
            roots.append(clean[: -len(expected_suffix)])
        else:
            roots.append(clean.rsplit("/", 1)[0] if "/" in clean else clean)

    if roots and all(root == roots[0] for root in roots):
        try:
            return _clean_root_url(roots[0])
        except AcControlError:
            return ""
    return ""


def validate_settings(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "root_url": _clean_root_url(raw.get("root_url")),
        "on_duration_seconds": _int_setting(
            raw.get("on_duration_seconds"),
            "ON duration",
            MIN_DURATION_SECONDS,
            MAX_DURATION_SECONDS,
        ),
        "off_duration_seconds": _int_setting(
            raw.get("off_duration_seconds"),
            "OFF duration",
            MIN_DURATION_SECONDS,
            MAX_DURATION_SECONDS,
        ),
        "max_cycles": _int_setting(
            raw.get("max_cycles"),
            "Cycle count",
            1,
            MAX_CYCLES,
            allow_zero=True,
        ),
        "request_timeout_seconds": _int_setting(
            raw.get("request_timeout_seconds"),
            "Request timeout",
            MIN_REQUEST_TIMEOUT_SECONDS,
            MAX_REQUEST_TIMEOUT_SECONDS,
        ),
        "turn_off_on_stop": bool(raw.get("turn_off_on_stop")),
    }


class AcController:
    def __init__(
        self,
        settings_path: str,
        statistics_path: str,
        history_path: str,
        weekly_path: str,
        runtime_path: str,
        timezone_name: str,
    ):
        self._settings_path = Path(settings_path).expanduser()
        self._runtime_path = Path(runtime_path).expanduser()
        self._usage = AcUsageStore(statistics_path)
        self._history = AcHistoryStore(history_path)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop_requested = False
        self._skip_requested = False
        self._worker: threading.Thread | None = None
        self._phase_started_monotonic: float | None = None
        self._phase_accounted_seconds = 0.0
        self._usage_checkpoint_seconds = 15.0
        self._settings = self._load_settings()
        self._status: dict[str, Any] = {
            "active": False,
            "phase": "Stopped",
            "phase_key": "stopped",
            "current_state": "unknown",
            "next_action_at": None,
            "phase_started_at": None,
            "phase_duration_seconds": 0,
            "completed_cycles": 0,
            "current_cycle": 0,
            "last_action": None,
            "last_action_at": None,
            "last_result": "No AC command has been sent since this server started.",
            "last_error": None,
            "started_at": None,
            "on_seconds_accumulated": 0.0,
            "off_seconds_accumulated": 0.0,
            "schedule_source": None,
            "weekly_rule": None,
        }
        self._resume_phase: dict[str, Any] | None = None
        self._restore_runtime_snapshot(self._load_runtime_snapshot())
        self._weekly = WeeklyScheduleManager(
            weekly_path,
            timezone_name,
            self._apply_weekly_target,
            default_preset1=self._settings,
        )
        if self._resume_phase is not None:
            self._start_worker_locked()

    def _load_settings(self) -> dict[str, Any]:
        if not self._settings_path.is_file():
            return deepcopy(DEFAULT_SETTINGS)

        try:
            with self._settings_path.open("r", encoding="utf-8") as file:
                stored = json.load(file)
        except (OSError, json.JSONDecodeError):
            return deepcopy(DEFAULT_SETTINGS)

        if not isinstance(stored, dict):
            return deepcopy(DEFAULT_SETTINGS)

        migrated = dict(stored)
        if "root_url" not in migrated:
            migrated["root_url"] = _legacy_root_url(migrated)
        merged = {**DEFAULT_SETTINGS, **migrated}
        try:
            return validate_settings(merged)
        except AcControlError:
            return deepcopy(DEFAULT_SETTINGS)

    def _save_settings_file(self, settings: dict[str, Any]) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._settings_path.with_suffix(self._settings_path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(settings, file, indent=2)
            file.write("\n")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, self._settings_path)

    def _save_runtime_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._runtime_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._runtime_path.with_suffix(self._runtime_path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(snapshot, file, indent=2)
            file.write("\n")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, self._runtime_path)

    def _load_runtime_snapshot(self) -> dict[str, Any] | None:
        if not self._runtime_path.is_file():
            return None
        try:
            snapshot = json.loads(self._runtime_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            snapshot = None
        try:
            self._runtime_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logging.warning("Could not remove consumed AC runtime snapshot", exc_info=True)
        return snapshot if isinstance(snapshot, dict) else None

    @staticmethod
    def _phase_remaining_seconds(status: dict[str, Any]) -> float | None:
        if status.get("phase_key") not in WAIT_PHASE_KEYS:
            return None

        next_action_at = str(status.get("next_action_at") or "").strip()
        if next_action_at:
            try:
                return max(0.0, datetime.fromisoformat(next_action_at).timestamp() - time.time())
            except ValueError:
                pass

        try:
            duration = float(status.get("phase_duration_seconds") or 0)
            started_at = datetime.fromisoformat(str(status.get("phase_started_at") or ""))
            elapsed = max(0.0, time.time() - started_at.timestamp())
            return max(0.0, duration - elapsed)
        except (TypeError, ValueError, OSError):
            return None

    def _runtime_snapshot_locked(self) -> dict[str, Any]:
        status = deepcopy(self._status)
        phase_key = str(status.get("phase_key") or "")
        resume_phase = phase_key if phase_key in WAIT_PHASE_KEYS or phase_key in {"sending_on", "sending_off"} else "sending_on"
        return {
            "version": 1,
            "saved_at": _utc_now_iso(),
            "status": status,
            "resume_phase": {
                "phase_key": resume_phase,
                "remaining_seconds": self._phase_remaining_seconds(status),
            },
        }

    def _restore_runtime_snapshot(self, snapshot: dict[str, Any] | None) -> None:
        if not snapshot or not isinstance(snapshot.get("status"), dict):
            return

        saved_status = snapshot["status"]
        fields = (
            "current_state",
            "phase",
            "phase_key",
            "next_action_at",
            "phase_started_at",
            "phase_duration_seconds",
            "completed_cycles",
            "current_cycle",
            "last_action",
            "last_action_at",
            "started_at",
            "on_seconds_accumulated",
            "off_seconds_accumulated",
            "schedule_source",
            "weekly_rule",
        )
        for field in fields:
            if field in saved_status:
                self._status[field] = saved_status[field]

        if not bool(saved_status.get("active")):
            self._status["active"] = False
            return
        if not self._settings["root_url"]:
            self._status["active"] = False
            self._status["last_result"] = "Saved AC schedule was not resumed because MacroDroid is not configured."
            return

        resume_phase = snapshot.get("resume_phase")
        if not isinstance(resume_phase, dict):
            resume_phase = {}
        phase_key = str(resume_phase.get("phase_key") or "sending_on")
        if phase_key not in WAIT_PHASE_KEYS and phase_key not in {"sending_on", "sending_off"}:
            phase_key = "sending_on"
        remaining = resume_phase.get("remaining_seconds")
        try:
            remaining = max(0.0, float(remaining)) if remaining is not None else None
        except (TypeError, ValueError):
            remaining = None
        self._resume_phase = {"phase_key": phase_key, "remaining_seconds": remaining}
        self._status["active"] = True
        self._status["last_error"] = None
        self._status["last_result"] = "AC schedule restored after server restart."

    def _start_worker_locked(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run_cycle,
            name="tamestorage-ac-cycle",
            daemon=True,
        )
        self._worker.start()

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    def get_history(self, limit: int = 80) -> list[dict[str, Any]]:
        return self._history.recent(limit)

    def get_weekly_config(self) -> dict[str, Any]:
        return self._weekly.get_config()

    def get_weekly_status(self) -> dict[str, Any]:
        return self._weekly.get_status()

    def save_weekly_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        config = self._weekly.save_config(raw)
        enabled_days = sum(1 for day in config["days"].values() if day["enabled"])
        self._history.add(
            "weekly_schedule_updated",
            summary=f"Weekly automation {'enabled' if config['enabled'] else 'disabled'} · {enabled_days} active day(s)",
        )
        return config

    def _active_unaccounted_seconds_locked(self) -> tuple[str | None, float]:
        if (
            not self._status["active"]
            or self._phase_started_monotonic is None
            or self._status["phase_key"] not in WAIT_PHASE_KEYS
        ):
            return None, 0.0
        elapsed = max(0.0, time.monotonic() - self._phase_started_monotonic)
        unaccounted = max(0.0, elapsed - self._phase_accounted_seconds)
        state = "on" if self._status["phase_key"] == "on_wait" else "off"
        return state, unaccounted

    def get_statistics(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._usage.snapshot()
            state, unaccounted = self._active_unaccounted_seconds_locked()
            if state == "on":
                snapshot["total_on_seconds"] += unaccounted
            elif state == "off":
                snapshot["total_off_seconds"] += unaccounted
            snapshot["live"] = bool(state)
            return snapshot

    def reset_statistics(self) -> dict[str, Any]:
        with self._condition:
            if self._phase_started_monotonic is not None:
                self._phase_accounted_seconds = max(
                    0.0, time.monotonic() - self._phase_started_monotonic
                )
            return self._usage.reset()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            status = deepcopy(self._status)
            status["root_url_configured"] = bool(self._settings["root_url"])
            status["max_cycles"] = self._settings["max_cycles"]
            status["on_duration_seconds"] = self._settings["on_duration_seconds"]
            status["off_duration_seconds"] = self._settings["off_duration_seconds"]
            status["can_skip_phase"] = bool(
                status["active"] and status["phase_key"] in WAIT_PHASE_KEYS
            )
            return status

    def save_settings(self, raw: dict[str, Any]) -> dict[str, Any]:
        settings = validate_settings(raw)
        with self._condition:
            previous = deepcopy(self._settings)
            self._save_settings_file(settings)
            self._settings = settings
            if (
                self._status["active"]
                and self._status["phase_key"] == "off_wait"
                and settings["max_cycles"]
                and int(self._status["completed_cycles"]) >= settings["max_cycles"]
            ):
                self._skip_requested = True
            self._condition.notify_all()

        changed = {}
        for key in ("on_duration_seconds", "off_duration_seconds", "max_cycles"):
            if previous.get(key) != settings.get(key):
                changed[key] = {"from": previous.get(key), "to": settings.get(key)}
        if changed:
            self._history.add("duration_adjusted", summary="Schedule timing adjusted", details=changed)
        return self.get_settings()

    def _set_status(self, **changes: Any) -> None:
        with self._condition:
            self._status.update(changes)
            self._condition.notify_all()

    def _send_trigger(self, action: str, source: str) -> None:
        with self._lock:
            settings = deepcopy(self._settings)
        url = build_action_url(settings["root_url"], action)

        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "text/plain, application/json, */*",
                "User-Agent": "Tamestorage-AC-Control/4.0",
            },
        )
        timeout = settings["request_timeout_seconds"]

        def send_request() -> None:
            try:
                with urlopen(request, timeout=timeout) as response:
                    status_code = int(getattr(response, "status", 200))
                    if status_code >= 400:
                        raise AcControlError(
                            f"MacroDroid {action.upper()} trigger returned HTTP {status_code}."
                        )
            except HTTPError as error:
                raise AcControlError(f"MacroDroid {action.upper()} trigger returned HTTP {error.code}.") from error
            except URLError as error:
                detail = getattr(error, "reason", error)
                raise AcControlError(f"Could not reach the local MacroDroid server: {detail}") from error
            except TimeoutError as error:
                raise AcControlError(f"MacroDroid {action.upper()} trigger timed out.") from error
            except OSError as error:
                raise AcControlError(f"Could not send the MacroDroid {action.upper()} trigger: {error}") from error

        try:
            phone_command_queue.execute(send_request, command_type="ac")
        except AcControlError as error:
            message = str(error)
            self._set_status(last_error=message, last_result=message)
            raise

        timestamp = _utc_now_iso()
        self._set_status(
            current_state=action,
            last_action=action,
            last_action_at=timestamp,
            last_error=None,
            last_result=f"AC {action.upper()} signal sent by {source}.",
        )

    def manual_action(self, action: str) -> None:
        with self._lock:
            if self._status["active"]:
                raise AcControlError("Stop the schedule before using direct control.")
        phone_command_queue.cancel_pending("ac")
        try:
            self._send_trigger(action, "direct control")
        except PhoneCommandCancelled as error:
            raise AcControlError(str(error)) from error
        self._usage.add_manual_command(action)
        self._history.add("direct_control", summary=f"AC {action.upper()} sent", details={"action": action})
        self._set_status(
            phase="Direct control",
            phase_key="manual",
            next_action_at=None,
            phase_started_at=None,
            phase_duration_seconds=0,
        )

    def start_cycle(self, *, source: str = "manual", source_label: str | None = None) -> None:
        with self._condition:
            if self._status["active"]:
                raise AcControlError("The schedule is already running.")
            if not self._settings["root_url"]:
                raise AcControlError("Configure the MacroDroid root URL first.")

            now = _utc_now_iso()
            self._stop_requested = False
            self._skip_requested = False
            self._status.update(
                active=True,
                phase="Starting",
                phase_key="starting",
                next_action_at=None,
                phase_started_at=now,
                phase_duration_seconds=0,
                completed_cycles=0,
                current_cycle=1,
                last_error=None,
                last_result="Starting schedule.",
                started_at=now,
                on_seconds_accumulated=0.0,
                off_seconds_accumulated=0.0,
                schedule_source=source,
                weekly_rule=source_label if source == "weekly" else None,
            )
            self._usage.add_schedule_start()
            self._start_worker_locked()
        self._history.add(
            "schedule_started",
            source=source,
            summary="Weekly schedule started" if source == "weekly" else "Schedule started",
            details={"rule": source_label} if source_label else {},
        )

    def stop_cycle(self, *, turn_off: bool | None = None, source: str = "manual") -> bool:
        if source == "manual":
            phone_command_queue.cancel_pending("ac")
        weekly_disabled = source == "manual" and self._weekly.disable()
        with self._condition:
            worker = self._worker
            was_active = bool(self._status["active"])
            settings = deepcopy(self._settings)
            if was_active:
                self._status.update(
                    phase="Stopping",
                    phase_key="stopping",
                    next_action_at=None,
                )
            self._stop_requested = True
            self._skip_requested = False
            self._condition.notify_all()

        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=settings["request_timeout_seconds"] + 3)

        should_turn_off = (
            was_active
            and (settings["turn_off_on_stop"] if turn_off is None else turn_off)
            and bool(settings["root_url"])
        )
        off_sent = False
        if should_turn_off:
            self._send_trigger("off", "schedule stop")
            off_sent = True

        result = "Schedule stopped and AC OFF sent." if off_sent else "Schedule stopped."
        if weekly_disabled:
            result += " Weekly automation disabled."
        self._set_status(
            active=False,
            phase="Stopped",
            phase_key="stopped",
            next_action_at=None,
            phase_started_at=None,
            phase_duration_seconds=0,
            last_result=result,
            schedule_source=None,
            weekly_rule=None,
        )
        if was_active and source != "restart":
            self._history.add(
                "schedule_stopped",
                source=source,
                summary="Weekly schedule stopped" if source == "weekly" else "Schedule stopped",
            )
        if weekly_disabled:
            self._history.add(
                "weekly_schedule_updated",
                source="manual",
                summary="Weekly automation disabled by Stop",
            )
        return weekly_disabled

    def prepare_for_restart(self) -> None:
        with self._condition:
            self._save_runtime_snapshot(self._runtime_snapshot_locked())
            was_active = bool(self._status["active"])
        if was_active:
            self.stop_cycle(turn_off=False, source="restart")

    def skip_phase(self) -> None:
        with self._condition:
            if not self._status["active"]:
                raise AcControlError("Start the schedule before skipping a phase.")
            if self._status["phase_key"] not in WAIT_PHASE_KEYS:
                raise AcControlError("Wait for the ON or OFF timer before skipping.")
            self._skip_requested = True
            self._status["last_result"] = f"Skipping {self._status['phase']}."
            self._status["last_error"] = None
            self._condition.notify_all()

    def _apply_weekly_target(self, target: dict[str, Any] | None) -> None:
        with self._condition:
            active = bool(self._status["active"])
            source = self._status.get("schedule_source")

        if target is None or target.get("mode") == "off":
            if active and source == "weekly":
                self.stop_cycle(source="weekly")
            return

        preset = target.get("preset")
        if not isinstance(preset, dict):
            raise AcControlError("Weekly preset is missing.")

        period = str(target.get("period") or "window").title()
        label = (
            f"{target['day'].title()} {period} · preset {target['preset_id']} "
            f"until {target['end']}"
        )
        with self._condition:
            active = bool(self._status["active"])
            source = self._status.get("schedule_source")
            if active and source != "weekly":
                return

            changed = (
                self._settings["on_duration_seconds"] != preset["on_duration_seconds"]
                or self._settings["off_duration_seconds"] != preset["off_duration_seconds"]
                or self._settings["max_cycles"] != 0
            )
            rule_changed = self._status.get("weekly_rule") != label
            if changed:
                updated = {
                    **self._settings,
                    "on_duration_seconds": preset["on_duration_seconds"],
                    "off_duration_seconds": preset["off_duration_seconds"],
                    "max_cycles": 0,
                }
                self._save_settings_file(updated)
                self._settings = updated
                self._condition.notify_all()

            if active and source == "weekly" and rule_changed:
                self._status["weekly_rule"] = label
                self._status["last_result"] = f"Weekly automation switched to preset {target['preset_id']}."
                self._history.add(
                    "weekly_preset_changed",
                    source="weekly",
                    summary=f"Preset {target['preset_id']} active",
                    details={"rule": label},
                )
                return

        if not active:
            self.start_cycle(source="weekly", source_label=label)

    def _checkpoint_usage_locked(self, state: str, elapsed: float, *, force: bool = False) -> None:
        delta = max(0.0, elapsed - self._phase_accounted_seconds)
        if delta <= 0 or (not force and delta < self._usage_checkpoint_seconds):
            return
        try:
            self._usage.add_time(state, delta)
            self._phase_accounted_seconds = elapsed
        except OSError as error:
            logging.exception("Could not persist AC usage statistics")
            self._status["last_error"] = f"Could not persist AC usage statistics: {error}"

    def _wait_dynamic(
        self,
        *,
        duration_setting: str,
        phase: str,
        phase_key: str,
        accumulator_key: str,
        remaining_seconds: float | None = None,
    ) -> str:
        configured_duration = int(self._settings[duration_setting])
        if remaining_seconds is None:
            initial_elapsed = 0.0
        else:
            initial_remaining = max(0.0, min(float(configured_duration), remaining_seconds))
            initial_elapsed = max(0.0, configured_duration - initial_remaining)
        started_monotonic = time.monotonic() - initial_elapsed
        started_wall = time.time() - initial_elapsed
        usage_state = "on" if phase_key == "on_wait" else "off"

        with self._condition:
            self._phase_started_monotonic = started_monotonic
            self._phase_accounted_seconds = initial_elapsed
            self._status.update(
                phase=phase,
                phase_key=phase_key,
                phase_started_at=datetime.fromtimestamp(started_wall, timezone.utc).isoformat(),
            )

            result = "completed"
            while True:
                if self._stop_requested:
                    result = "stopped"
                    break
                if self._skip_requested:
                    self._skip_requested = False
                    result = "skipped"
                    break

                configured_duration = int(self._settings[duration_setting])
                elapsed = max(0.0, time.monotonic() - started_monotonic)
                remaining = configured_duration - elapsed
                self._status["phase_duration_seconds"] = configured_duration
                self._status["next_action_at"] = datetime.fromtimestamp(
                    started_wall + configured_duration,
                    timezone.utc,
                ).isoformat()
                self._checkpoint_usage_locked(usage_state, elapsed)

                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 1.0))

            elapsed_actual = max(0.0, time.monotonic() - started_monotonic)
            self._checkpoint_usage_locked(usage_state, elapsed_actual, force=True)
            newly_elapsed = max(0.0, elapsed_actual - initial_elapsed)
            self._status[accumulator_key] = float(self._status.get(accumulator_key, 0.0)) + newly_elapsed
            self._status.update(
                next_action_at=None,
                phase_started_at=None,
                phase_duration_seconds=0,
                phase_key="transition" if self._status["active"] else "stopped",
            )
            self._phase_started_monotonic = None
            self._phase_accounted_seconds = 0.0
            return result

    def _run_cycle(self) -> None:
        resume_phase = self._resume_phase
        self._resume_phase = None

        def complete_on_phase() -> bool:
            self._set_status(phase="Sending OFF", phase_key="sending_off")
            self._send_trigger("off", "automatic schedule")

            with self._condition:
                self._status["completed_cycles"] += 1
                completed_cycles = int(self._status["completed_cycles"])
                max_cycles = int(self._settings["max_cycles"])
            self._usage.add_cycle()

            if max_cycles and completed_cycles >= max_cycles:
                self._set_status(last_result="The planned cycle count is complete.")
                return False
            return True

        try:
            if resume_phase:
                phase_key = str(resume_phase.get("phase_key") or "sending_on")
                remaining = resume_phase.get("remaining_seconds")
                if phase_key == "on_wait":
                    result = self._wait_dynamic(
                        duration_setting="on_duration_seconds",
                        phase="AC ON",
                        phase_key="on_wait",
                        accumulator_key="on_seconds_accumulated",
                        remaining_seconds=remaining,
                    )
                    if result == "stopped":
                        return
                    if not complete_on_phase():
                        return
                    result = self._wait_dynamic(
                        duration_setting="off_duration_seconds",
                        phase="AC OFF",
                        phase_key="off_wait",
                        accumulator_key="off_seconds_accumulated",
                    )
                    if result == "stopped":
                        return
                elif phase_key == "off_wait":
                    result = self._wait_dynamic(
                        duration_setting="off_duration_seconds",
                        phase="AC OFF",
                        phase_key="off_wait",
                        accumulator_key="off_seconds_accumulated",
                        remaining_seconds=remaining,
                    )
                    if result == "stopped":
                        return
                elif phase_key == "sending_off":
                    if not complete_on_phase():
                        return
                    result = self._wait_dynamic(
                        duration_setting="off_duration_seconds",
                        phase="AC OFF",
                        phase_key="off_wait",
                        accumulator_key="off_seconds_accumulated",
                    )
                    if result == "stopped":
                        return

            while True:
                with self._condition:
                    if self._stop_requested:
                        break
                    max_cycles = int(self._settings["max_cycles"])
                    completed_cycles = int(self._status["completed_cycles"])
                    if max_cycles and completed_cycles >= max_cycles:
                        self._status["last_result"] = "The planned cycle count is complete."
                        break
                    self._status["current_cycle"] = completed_cycles + 1
                    self._status.update(phase="Sending ON", phase_key="sending_on")

                self._send_trigger("on", "automatic schedule")
                on_result = self._wait_dynamic(
                    duration_setting="on_duration_seconds",
                    phase="AC ON",
                    phase_key="on_wait",
                    accumulator_key="on_seconds_accumulated",
                )
                if on_result == "stopped":
                    break

                if not complete_on_phase():
                    break

                off_result = self._wait_dynamic(
                    duration_setting="off_duration_seconds",
                    phase="AC OFF",
                    phase_key="off_wait",
                    accumulator_key="off_seconds_accumulated",
                )
                if off_result == "stopped":
                    break
        except PhoneCommandCancelled:
            self._set_status(last_result="Queued AC command canceled.")
        except AcControlError as error:
            self._set_status(last_error=str(error), last_result=str(error))
        except Exception as error:
            message = f"Unexpected schedule error: {error}"
            self._set_status(last_error=message, last_result=message)
        finally:
            with self._condition:
                self._stop_requested = False
                self._skip_requested = False
                self._status.update(
                    active=False,
                    phase="Stopped",
                    phase_key="stopped",
                    next_action_at=None,
                    phase_started_at=None,
                    phase_duration_seconds=0,
                    schedule_source=None,
                    weekly_rule=None,
                )
                self._phase_started_monotonic = None
                self._phase_accounted_seconds = 0.0
                self._worker = None
                self._condition.notify_all()


def get_ac_controller(app) -> AcController:
    controller = app.extensions.get("ac_controller")
    if controller is None:
        controller = AcController(
            app.config["AC_CONTROL_FILE"],
            app.config["AC_STATISTICS_FILE"],
            app.config["AC_HISTORY_FILE"],
            app.config["AC_WEEKLY_FILE"],
            app.config["AC_RUNTIME_FILE"],
            app.config.get("APP_TIMEZONE", "Asia/Singapore"),
        )
        app.extensions["ac_controller"] = controller
    return controller
