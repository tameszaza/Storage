import logging
import json
import secrets
import socket
import os
import shutil
import time
from pathlib import Path

from flask import current_app, flash, jsonify, make_response, redirect, render_template, request, url_for, session

from lib.charts import clear_charts
from lib.disk_monitor import collect_disk_report
from lib.log_viewer import (
    LogAccessError,
    clear_log_file,
    filter_entries,
    parse_server_entries,
    parse_transfer_entries,
    read_recent_lines,
)
from lib.security import admin_required
from lib.storage import format_bytes, safe_upload_path, user_storage_usage
from lib.system_info import system_usage as collect_system_usage
from lib.tailscale_control import TailscaleControlError, request_host_control
from lib.users import load_users, save_users

def _admin_asset_version() -> int:
    static_root = Path(current_app.static_folder or "static")
    paths = (
        static_root / "css" / "admin.css",
        static_root / "css" / "services.css",
        static_root / "js" / "services.js",
        static_root / "js" / "admin.js",
        static_root / "js" / "disk_analysis.js",
    )
    timestamps = [int(path.stat().st_mtime) for path in paths if path.is_file()]
    return max(timestamps, default=int(time.time()))


@admin_required
def admin():
    users = load_users()
    usage = collect_system_usage()
    user_storage = user_storage_usage(users.keys())
    return render_template(
        "admin_panel.html",
        users=users,
        user_storage=user_storage,
        uptime=usage["uptime"],
        system_usage=usage,
        format_bytes=format_bytes,
        admin_asset_version=_admin_asset_version(),
        services_csrf=session.setdefault("transcription_csrf",secrets.token_urlsafe(32)),
    )


@admin_required
def system_usage():
    return jsonify(collect_system_usage())


@admin_required
def exit_node_api():
    try:
        if request.method == "GET":
            result = request_host_control(current_app.config["TAILSCALE_CONTROL_SOCKET"], "status")
        else:
            body = request.get_json(silent=True) or {}
            if type(body.get("enabled")) is not bool:
                return jsonify({"ok": False, "error": "Choose on or off."}), 400
            result = request_host_control(
                current_app.config["TAILSCALE_CONTROL_SOCKET"], "set", enabled=body["enabled"]
            )
        return jsonify({"ok": True, "enabled": result["enabled"]})
    except TailscaleControlError as exc:
        logging.warning("Exit-node control failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 503


@admin_required
def disk_dashboard():
    targets = [
        {
            "key": "system",
            "label": "Internal system disk",
            "role": "Ubuntu, Docker and music",
            "path": current_app.config["DISK_CHECK_SYSTEM_PATH"],
            "display_path": "/",
            "kind": "system",
        },
        {
            "key": "primary",
            "label": "Primary NAS disk",
            "role": "TameStorage uploads and application data",
            "path": current_app.config["DISK_CHECK_PRIMARY_PATH"],
            "display_path": "/srv/tamestorage",
            "kind": "primary",
        },
        {
            "key": "backup",
            "label": "Independent backup disk",
            "role": "TameStorage mirror every 6 hours",
            "path": current_app.config["DISK_CHECK_BACKUP_PATH"],
            "display_path": "/srv/tamestorage-backup",
            "kind": "backup",
        },
    ]
    report = collect_disk_report(targets)
    raid_disk = None
    raid_busy = False
    response = make_response(
        render_template(
            "disk_dashboard.html",
            report=report,
            raid_disk=raid_disk,
            raid_busy=raid_busy,
            format_bytes=format_bytes,
            admin_asset_version=_admin_asset_version(),
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_required
def disk_analysis(disk):
    if disk not in {"system", "primary", "backup"}:
        return jsonify(error="Unknown disk"), 404
    folder = request.args.get("path", "")
    if len(folder) > 1024:
        return jsonify(error="Invalid folder path"), 400
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect("/run/tamestorage-control/disk-analysis.sock")
            client.sendall(json.dumps({"disk": disk, "path": folder, "refresh": request.method == "POST"}).encode() + b"\n")
            with client.makefile("rb") as stream:
                data = json.loads(stream.readline(262144))
        response = jsonify(data)
        response.headers["Cache-Control"] = "no-store"
        return response
    except (OSError, ValueError):
        return jsonify(state="error", error="Storage analysis is temporarily unavailable."), 503


def _log_limit() -> int:
    try:
        return max(50, min(500, int(request.args.get("limit", "50"))))
    except (TypeError, ValueError):
        return 50


def _render_log_page(log_type: str):
    is_transfer = log_type == "transfer"
    config_key = "DATA_TRANSFER_LOG" if is_transfer else "SERVER_LOG_FILE"
    query = request.args.get("query", "").strip()
    category = request.args.get("category", "all").strip().lower() or "all"
    limit = _log_limit()
    categories = (
        ("all", "All"),
        ("request", "Requests"),
        ("response", "Responses"),
        ("unknown", "Unparsed"),
    ) if is_transfer else (
        ("all", "All"),
        ("critical", "Critical"),
        ("error", "Errors"),
        ("warning", "Warnings"),
        ("info", "Info"),
        ("debug", "Debug"),
        ("unknown", "Unparsed"),
    )
    valid_categories = {value for value, _label in categories}
    if category not in valid_categories:
        category = "all"

    try:
        snapshot = read_recent_lines(current_app.config[config_key])
        parsed = parse_transfer_entries(snapshot["lines"]) if is_transfer else parse_server_entries(snapshot["lines"])
        entries, summary = filter_entries(parsed, query=query, category=category, limit=limit)
        error = ""
        status_code = 200
    except LogAccessError as exc:
        logging.error("Could not open %s logs: %s", log_type, exc)
        snapshot = {
            "filename": Path(str(current_app.config.get(config_key) or "log")).name,
            "file_size": "Unavailable",
            "modified_at": None,
            "window_truncated": False,
            "loaded_lines": 0,
        }
        entries = []
        summary = {
            "matched": 0,
            "available": 0,
            "limit": limit,
            "server_counts": {},
            "transfer_counts": {},
            "transfer_size": "0 B",
        }
        error = str(exc)
        status_code = 503

    rendered = render_template(
        "admin_logs.html",
        log_type=log_type,
        page_title="Transfer logs" if is_transfer else "Server logs",
        page_eyebrow="Network" if is_transfer else "Runtime",
        entries=entries,
        snapshot=snapshot,
        summary=summary,
        error=error,
        query=query,
        category=category,
        categories=categories,
        selected_limit=limit,
        clear_endpoint="clear_transfer_logs" if is_transfer else "clear_logs",
        alternate_endpoint="admin_logs" if is_transfer else "view_transfer_logs",
        alternate_label="Server logs" if is_transfer else "Transfer logs",
        admin_asset_version=_admin_asset_version(),
    )
    response = make_response(rendered, status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_required
def admin_logs():
    return _render_log_page("server")


@admin_required
def clear_logs():
    try:
        clear_log_file(current_app.config["SERVER_LOG_FILE"])
        logging.info("Server log file cleared by Admin.")
        flash("Server logs cleared.", "success")
    except LogAccessError as exc:
        logging.error("Could not clear server logs: %s", exc)
        flash(str(exc), "danger")
    return redirect(url_for("admin_logs"))


@admin_required
def view_transfer_logs():
    return _render_log_page("transfer")


@admin_required
def clear_transfer_logs():
    try:
        clear_log_file(current_app.config["DATA_TRANSFER_LOG"])
        logging.info("Transfer log file cleared by Admin.")
        flash("Transfer logs cleared.", "success")
    except LogAccessError as exc:
        logging.error("Could not clear transfer logs: %s", exc)
        flash(str(exc), "danger")
    return redirect(url_for("view_transfer_logs"))


@admin_required
def reset_user(username):
    if username == "Admin":
        flash("The owner account cannot be reset here. Use Settings to change its password.", "warning")
        return redirect(url_for("admin"))
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
        logging.info("User login reset by Admin: %s", username)
        flash(f"Login record removed for {username}.", "success")
    return redirect(url_for("admin"))


@admin_required
def remove_user(username):
    if username == "Admin":
        flash("The owner account and its storage cannot be removed here.", "warning")
        return redirect(url_for("admin"))
    users = load_users()
    if username not in users:
        flash("User not found; no storage was removed.", "warning")
        return redirect(url_for("admin"))
    user_path = safe_upload_path(username)
    if Path(user_path).parent != Path(safe_upload_path("")):
        flash("Invalid user storage path; nothing was removed.", "danger")
        return redirect(url_for("admin"))
    if os.path.islink(user_path):
        flash("User storage is a link; inspect it before removal.", "danger")
        return redirect(url_for("admin"))
    if os.path.exists(user_path):
        shutil.rmtree(user_path)
    del users[username]
    save_users(users)
    logging.info("User removed by Admin: %s", username)
    flash(f"Removed {username} and its storage folder.", "success")
    return redirect(url_for("admin"))


@admin_required
def suspend_user(username):
    if username == "Admin":
        flash("The owner account cannot be suspended.", "warning")
        return redirect(url_for("admin"))
    users = load_users()
    if username in users:
        users[username]["suspended"] = True
        save_users(users)
        logging.info("User suspended by Admin: %s", username)
    return redirect(url_for("admin"))


@admin_required
def unsuspend_user(username):
    users = load_users()
    if username in users:
        users[username]["suspended"] = False
        save_users(users)
        logging.info("User unsuspended by Admin: %s", username)
    return redirect(url_for("admin"))


@admin_required
def clear_all_charts():
    try:
        clear_charts()
        flash("All charts have been cleared.", "success")
    except Exception as exc:
        logging.exception("Error clearing charts")
        flash(f"Error clearing charts: {exc}", "danger")
    return redirect(url_for("admin"))


@admin_required
def set_user_quota(username):
    if username == "Admin":
        flash("The owner account has no storage quota.", "warning")
        return redirect(url_for("admin"))
    users = load_users()
    if username not in users:
        flash("User not found.", "warning")
        return redirect(url_for("admin"))
    quota_gb = request.form.get("quota_gb", "").strip()
    try:
        quota = float(quota_gb)
    except ValueError:
        quota = 0
    users[username]["quota_bytes"] = int(quota * 1024 * 1024 * 1024) if quota > 0 else 0
    save_users(users)
    flash(f"Quota updated for {username}.", "success")
    return redirect(url_for("admin"))


def register_routes(app):
    app.add_url_rule("/admin/disks/<disk>/analysis", "disk_analysis", disk_analysis, methods=["GET", "POST"])
    app.add_url_rule("/admin", "admin", admin)
    app.add_url_rule("/admin/disks", "disk_dashboard", disk_dashboard)
    app.add_url_rule("/system_usage", "system_usage", system_usage)
    app.add_url_rule("/admin/api/exit-node", "exit_node_api", exit_node_api, methods=["GET", "POST"])
    app.add_url_rule("/admin/logs", "admin_logs", admin_logs)
    app.add_url_rule("/admin/clear_logs", "clear_logs", clear_logs, methods=["POST"])
    app.add_url_rule("/admin/transfer_logs", "view_transfer_logs", view_transfer_logs)
    app.add_url_rule("/admin/clear_transfer_logs", "clear_transfer_logs", clear_transfer_logs, methods=["POST"])
    app.add_url_rule("/admin/reset/<username>", "reset_user", reset_user, methods=["POST"])
    app.add_url_rule("/admin/remove/<username>", "remove_user", remove_user, methods=["POST"])
    app.add_url_rule("/admin/suspend/<username>", "suspend_user", suspend_user, methods=["POST"])
    app.add_url_rule("/admin/unsuspend/<username>", "unsuspend_user", unsuspend_user, methods=["POST"])
    app.add_url_rule("/admin/quota/<username>", "set_user_quota", set_user_quota, methods=["POST"])
    app.add_url_rule("/admin/clear_charts", "clear_charts", clear_all_charts, methods=["POST"])
