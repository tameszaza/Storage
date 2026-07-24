from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
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
    def __init__(self, settings_path: str):
        self._settings_path = Path(settings_path).expanduser()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._action_lock = threading.Lock()
        self._stop_requested = False
        self._skip_requested = False
        self._worker: threading.Thread | None = None
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
        }

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

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

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

        try:
            with self._action_lock:
                with urlopen(request, timeout=timeout) as response:
                    status_code = int(getattr(response, "status", 200))
                    if status_code >= 400:
                        raise AcControlError(
                            f"MacroDroid {action.upper()} trigger returned HTTP {status_code}."
                        )
        except HTTPError as error:
            message = f"MacroDroid {action.upper()} trigger returned HTTP {error.code}."
            self._set_status(last_error=message, last_result=message)
            raise AcControlError(message) from error
        except URLError as error:
            detail = getattr(error, "reason", error)
            message = f"Could not reach the local MacroDroid server: {detail}"
            self._set_status(last_error=message, last_result=message)
            raise AcControlError(message) from error
        except TimeoutError as error:
            message = f"MacroDroid {action.upper()} trigger timed out."
            self._set_status(last_error=message, last_result=message)
            raise AcControlError(message) from error
        except OSError as error:
            message = f"Could not send the MacroDroid {action.upper()} trigger: {error}"
            self._set_status(last_error=message, last_result=message)
            raise AcControlError(message) from error

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
        self._send_trigger(action, "direct control")
        self._set_status(
            phase="Direct control",
            phase_key="manual",
            next_action_at=None,
            phase_started_at=None,
            phase_duration_seconds=0,
        )

    def start_cycle(self) -> None:
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
            )
            self._worker = threading.Thread(
                target=self._run_cycle,
                name="tamestorage-ac-cycle",
                daemon=True,
            )
            self._worker.start()

    def stop_cycle(self, *, turn_off: bool | None = None) -> None:
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

        self._set_status(
            active=False,
            phase="Stopped",
            phase_key="stopped",
            next_action_at=None,
            phase_started_at=None,
            phase_duration_seconds=0,
            last_result="Schedule stopped and AC OFF sent." if off_sent else "Schedule stopped.",
        )

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

    def _wait_dynamic(
        self,
        *,
        duration_setting: str,
        phase: str,
        phase_key: str,
        accumulator_key: str,
    ) -> str:
        started_monotonic = time.monotonic()
        started_wall = time.time()

        with self._condition:
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

                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 1.0))

            elapsed_actual = max(0.0, time.monotonic() - started_monotonic)
            self._status[accumulator_key] = float(
                self._status.get(accumulator_key, 0.0)
            ) + elapsed_actual
            self._status.update(
                next_action_at=None,
                phase_started_at=None,
                phase_duration_seconds=0,
                phase_key="transition" if self._status["active"] else "stopped",
            )
            return result

    def _run_cycle(self) -> None:
        try:
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

                self._set_status(phase="Sending OFF", phase_key="sending_off")
                self._send_trigger("off", "automatic schedule")

                with self._condition:
                    self._status["completed_cycles"] += 1
                    completed_cycles = int(self._status["completed_cycles"])
                    max_cycles = int(self._settings["max_cycles"])

                if max_cycles and completed_cycles >= max_cycles:
                    self._set_status(last_result="The planned cycle count is complete.")
                    break

                off_result = self._wait_dynamic(
                    duration_setting="off_duration_seconds",
                    phase="AC OFF",
                    phase_key="off_wait",
                    accumulator_key="off_seconds_accumulated",
                )
                if off_result == "stopped":
                    break
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
                )
                self._worker = None
                self._condition.notify_all()


def get_ac_controller(app) -> AcController:
    controller = app.extensions.get("ac_controller")
    if controller is None:
        controller = AcController(app.config["AC_CONTROL_FILE"])
        app.extensions["ac_controller"] = controller
    return controller
