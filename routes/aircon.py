from __future__ import annotations

import hmac
import time
from functools import wraps
from pathlib import Path

from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from lib.ac_control import AcControlError, get_ac_controller


def _asset_version() -> int:
    static_root = Path(current_app.static_folder or "static")
    paths = (static_root / "css" / "aircon.css", static_root / "js" / "aircon.js")
    timestamps = [int(path.stat().st_mtime) for path in paths if path.is_file()]
    return max(timestamps, default=int(time.time()))


def _session_is_valid() -> bool:
    if not session.get("aircon_authenticated"):
        return False
    authenticated_at = float(session.get("aircon_authenticated_at") or 0)
    max_age_hours = int(current_app.config.get("AIRCON_SESSION_HOURS", 24))
    return authenticated_at > 0 and time.time() - authenticated_at <= max_age_hours * 3600


def aircon_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _session_is_valid():
            session.pop("aircon_authenticated", None)
            session.pop("aircon_authenticated_at", None)
            if request.path.startswith("/aircon/api/"):
                return jsonify({"ok": False, "error": "Aircon portal session expired."}), 401
            return redirect(url_for("aircon_login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def _json_error(error: Exception, status_code: int = 400):
    return jsonify({"ok": False, "error": str(error)}), status_code


def _request_settings() -> dict[str, object]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise AcControlError("Send the settings as JSON.")

    cycle_mode = str(payload.get("cycle_mode") or "continuous")
    max_cycles = payload.get("max_cycles", 1) if cycle_mode == "fixed" else 0
    return {
        "root_url": payload.get("root_url", ""),
        "on_duration_seconds": payload.get("on_duration_seconds", ""),
        "off_duration_seconds": payload.get("off_duration_seconds", ""),
        "max_cycles": max_cycles,
        "request_timeout_seconds": payload.get("request_timeout_seconds", 5),
        "turn_off_on_stop": bool(payload.get("turn_off_on_stop")),
    }


def aircon_login():
    configured = bool(current_app.config.get("AIRCON_PORTAL_PASSWORD"))
    if _session_is_valid():
        return redirect(url_for("aircon_portal"))

    if request.method == "POST":
        configured_password = str(current_app.config.get("AIRCON_PORTAL_PASSWORD") or "")
        entered_password = str(request.form.get("password") or "")
        if not configured_password:
            flash("Set AIRCON_PORTAL_PASSWORD in .env, then restart the server.", "danger")
        elif hmac.compare_digest(entered_password, configured_password):
            session["aircon_authenticated"] = True
            session["aircon_authenticated_at"] = time.time()
            return redirect(url_for("aircon_portal"))
        else:
            flash("Incorrect aircon portal password.", "danger")

    return render_template(
        "aircon_login.html",
        configured=configured,
        asset_version=_asset_version(),
    )


def aircon_logout():
    session.pop("aircon_authenticated", None)
    session.pop("aircon_authenticated_at", None)
    return redirect(url_for("aircon_login"))


@aircon_required
def aircon_portal():
    controller = get_ac_controller(current_app)
    return render_template(
        "aircon_portal.html",
        initial_data={
            "settings": controller.get_settings(),
            "status": controller.get_status(),
            "statistics": controller.get_statistics(),
            "rate_per_hour": 0.39,
        },
        asset_version=_asset_version(),
    )


@aircon_required
def aircon_status_api():
    controller = get_ac_controller(current_app)
    return jsonify(
        {
            "ok": True,
            "settings": controller.get_settings(),
            "status": controller.get_status(),
            "statistics": controller.get_statistics(),
            "rate_per_hour": 0.39,
        }
    )


@aircon_required
def aircon_settings_api():
    controller = get_ac_controller(current_app)
    try:
        settings = controller.save_settings(_request_settings())
        return jsonify(
            {
                "ok": True,
                "message": "Changes applied.",
                "settings": settings,
                "status": controller.get_status(),
                "statistics": controller.get_statistics(),
            }
        )
    except (AcControlError, OSError) as error:
        return _json_error(error)


@aircon_required
def aircon_action_api(action: str):
    controller = get_ac_controller(current_app)
    try:
        controller.manual_action(action)
        return jsonify(
            {
                "ok": True,
                "message": f"AC {action.upper()} sent.",
                "status": controller.get_status(),
                "statistics": controller.get_statistics(),
            }
        )
    except AcControlError as error:
        return _json_error(error)


@aircon_required
def aircon_start_api():
    controller = get_ac_controller(current_app)
    try:
        controller.start_cycle()
        return jsonify(
            {
                "ok": True,
                "message": "Schedule started.",
                "status": controller.get_status(),
                "statistics": controller.get_statistics(),
            }
        )
    except AcControlError as error:
        return _json_error(error)


@aircon_required
def aircon_stop_api():
    controller = get_ac_controller(current_app)
    try:
        controller.stop_cycle()
        return jsonify(
            {
                "ok": True,
                "message": "Schedule stopped.",
                "status": controller.get_status(),
                "statistics": controller.get_statistics(),
            }
        )
    except AcControlError as error:
        return _json_error(error)


@aircon_required
def aircon_skip_api():
    controller = get_ac_controller(current_app)
    try:
        controller.skip_phase()
        return jsonify(
            {
                "ok": True,
                "message": "Moving to the next phase.",
                "status": controller.get_status(),
                "statistics": controller.get_statistics(),
            }
        )
    except AcControlError as error:
        return _json_error(error)



@aircon_required
def aircon_reset_statistics_api():
    controller = get_ac_controller(current_app)
    try:
        statistics = controller.reset_statistics()
        return jsonify(
            {
                "ok": True,
                "message": "Usage totals reset.",
                "statistics": statistics,
                "status": controller.get_status(),
            }
        )
    except OSError as error:
        return _json_error(error, 500)

def register_routes(app):
    app.add_url_rule("/aircon/login", "aircon_login", aircon_login, methods=["GET", "POST"])
    app.add_url_rule("/aircon/logout", "aircon_logout", aircon_logout, methods=["POST"])
    app.add_url_rule("/aircon", "aircon_portal", aircon_portal)
    app.add_url_rule("/aircon/api/status", "aircon_status_api", aircon_status_api)
    app.add_url_rule("/aircon/api/settings", "aircon_settings_api", aircon_settings_api, methods=["POST"])
    app.add_url_rule("/aircon/api/action/<action>", "aircon_action_api", aircon_action_api, methods=["POST"])
    app.add_url_rule("/aircon/api/start", "aircon_start_api", aircon_start_api, methods=["POST"])
    app.add_url_rule("/aircon/api/stop", "aircon_stop_api", aircon_stop_api, methods=["POST"])
    app.add_url_rule("/aircon/api/skip", "aircon_skip_api", aircon_skip_api, methods=["POST"])
    app.add_url_rule("/aircon/api/statistics/reset", "aircon_reset_statistics_api", aircon_reset_statistics_api, methods=["POST"])
