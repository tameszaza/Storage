import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import current_app, flash, jsonify, make_response, redirect, render_template, request, url_for

from lib.ac_control import AcControlError, get_ac_controller
from lib.charts import clear_charts
from lib.feedback_store import delete_feedback as delete_feedback_item, load_feedback, mark_all_as_read
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
from lib.users import load_users, save_users

def _admin_asset_version() -> int:
    static_root = Path(current_app.static_folder or "static")
    paths = (
        static_root / "css" / "admin.css",
        static_root / "css" / "camera.css",
        static_root / "js" / "admin.js",
        static_root / "js" / "camera.js",
    )
    timestamps = [int(path.stat().st_mtime) for path in paths if path.is_file()]
    return max(timestamps, default=int(time.time()))


def _restart_process_after_response(delay_seconds: float = 1.25) -> None:
    time.sleep(delay_seconds)
    if os.environ.get("WERKZEUG_RUN_MAIN", "").lower() == "true":
        # Werkzeug's development reloader restarts when the child exits with code 3.
        os._exit(3)
    os.execv(sys.executable, [sys.executable, *sys.argv])


@admin_required
def admin():
    users = load_users()
    usage = collect_system_usage()
    user_storage = user_storage_usage(users.keys())
    feedback = load_feedback()
    unread_count = sum(1 for item in feedback if not item.get("read"))
    return render_template(
        "admin_panel.html",
        users=users,
        user_storage=user_storage,
        unread_count=unread_count,
        uptime=usage["uptime"],
        format_bytes=format_bytes,
        admin_asset_version=_admin_asset_version(),
    )


@admin_required
def system_usage():
    return jsonify(collect_system_usage())


def _log_limit() -> int:
    try:
        return max(50, min(500, int(request.args.get("limit", "200"))))
    except (TypeError, ValueError):
        return 200


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
def view_feedback():
    feedback_list = mark_all_as_read()
    return render_template("view_feedback.html", feedback_list=feedback_list)


@admin_required
def delete_feedback(feedback_id):
    delete_feedback_item(feedback_id)
    flash("Feedback deleted.", "success")
    return redirect(url_for("view_feedback"))


@admin_required
def reset_user(username):
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
        logging.info("User login reset by Admin: %s", username)
        flash(f"Login record removed for {username}.", "success")
    return redirect(url_for("admin"))


@admin_required
def remove_user(username):
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
    user_path = safe_upload_path(username)
    if os.path.exists(user_path):
        shutil.rmtree(user_path)
    logging.info("User removed by Admin: %s", username)
    flash(f"Removed {username} and its storage folder.", "success")
    return redirect(url_for("admin"))


@admin_required
def suspend_user(username):
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
def git_pull():
    try:
        subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/main"], check=False)
        result = subprocess.run(["git", "pull"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        logging.info("Git pull stdout: %s", result.stdout)
        logging.info("Git pull stderr: %s", result.stderr)
        if result.returncode == 0:
            flash("Git pull completed successfully.", "success")
        else:
            flash("Git pull failed. Check server logs for details.", "danger")
    except Exception as exc:
        logging.exception("Git pull failed")
        flash(f"Git pull failed: {exc}", "danger")
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


def shutdown_server():
    os._exit(0)


@admin_required
def shutdown():
    logging.warning("Shutdown initiated by Admin.")
    shutdown_server()
    return "Server shutting down..."


@admin_required
def restart_server():
    controller = get_ac_controller(current_app)
    try:
        controller.prepare_for_restart()
    except (AcControlError, OSError) as error:
        logging.warning("Could not preserve AC state before restart: %s", error)
        flash(f"Restart cancelled because the AC state could not be saved: {error}", "danger")
        return redirect(url_for("admin") + "#server-control")

    logging.warning("Server restart initiated by Admin.")
    threading.Thread(
        target=_restart_process_after_response,
        name="tamestorage-server-restart",
        daemon=True,
    ).start()
    return render_template("server_restarting.html"), 202


@admin_required
def restart_status():
    return jsonify({"ready": True})


@admin_required
def set_user_quota(username):
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


@admin_required
def admin_video():
    response = make_response(
        render_template(
            "view_cam.html",
            camera_stream_url=url_for("camera_mjpeg"),
            camera_audio_chunk_url=url_for("audio_chunk"),
            camera_status_url=url_for("camera_status"),
            camera_control_url=url_for("camera_control"),
            camera_tts_url=url_for("tts_speak"),
            tts_max_chars=current_app.config["TTS_MAX_CHARS"],
            admin_asset_version=_admin_asset_version(),
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def register_routes(app):
    app.add_url_rule("/admin", "admin", admin)
    app.add_url_rule("/admin/video", "admin_video", admin_video)
    app.add_url_rule("/system_usage", "system_usage", system_usage)
    app.add_url_rule("/admin/logs", "admin_logs", admin_logs)
    app.add_url_rule("/admin/clear_logs", "clear_logs", clear_logs, methods=["POST"])
    app.add_url_rule("/admin/transfer_logs", "view_transfer_logs", view_transfer_logs)
    app.add_url_rule("/admin/clear_transfer_logs", "clear_transfer_logs", clear_transfer_logs, methods=["POST"])
    app.add_url_rule("/admin/view_feedback", "view_feedback", view_feedback)
    app.add_url_rule("/admin/delete_feedback/<feedback_id>", "delete_feedback", delete_feedback, methods=["POST"])
    app.add_url_rule("/admin/reset/<username>", "reset_user", reset_user, methods=["POST"])
    app.add_url_rule("/admin/remove/<username>", "remove_user", remove_user, methods=["POST"])
    app.add_url_rule("/admin/suspend/<username>", "suspend_user", suspend_user, methods=["POST"])
    app.add_url_rule("/admin/unsuspend/<username>", "unsuspend_user", unsuspend_user, methods=["POST"])
    app.add_url_rule("/admin/quota/<username>", "set_user_quota", set_user_quota, methods=["POST"])
    app.add_url_rule("/admin/git_pull", "git_pull", git_pull, methods=["POST"])
    app.add_url_rule("/admin/clear_charts", "clear_charts", clear_all_charts, methods=["POST"])
    app.add_url_rule("/admin/restart", "restart_server", restart_server, methods=["POST"])
    app.add_url_rule("/admin/restart/status", "restart_status", restart_status)
    app.add_url_rule("/admin/shutdown", "shutdown", shutdown, methods=["POST"])
