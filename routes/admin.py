import logging
import os
import shutil
import subprocess
from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from lib.charts import clear_charts
from lib.feedback_store import delete_feedback as delete_feedback_item, load_feedback, mark_all_as_read
from lib.security import admin_required
from lib.storage import format_bytes, safe_upload_path, user_storage_usage
from lib.system_info import system_usage as collect_system_usage
from lib.users import load_users, save_users


def read_text_file(path: str, missing_message: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as file:
            return file.read()
    except FileNotFoundError:
        return missing_message


def write_empty_file(path: str) -> None:
    with open(path, "w", encoding="utf-8") as file:
        file.write("")


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
    )


@admin_required
def system_usage():
    return jsonify(collect_system_usage())


@admin_required
def admin_logs():
    log_contents = read_text_file(current_app.config["SERVER_LOG_FILE"], "Log file not found.")
    return render_template("admin_logs.html", log_contents=log_contents)


@admin_required
def clear_logs():
    try:
        write_empty_file(current_app.config["SERVER_LOG_FILE"])
        logging.info("Server log file cleared by Admin.")
        flash("Server logs cleared.", "success")
    except Exception as exc:
        logging.exception("Error clearing server logs")
        flash(f"Error clearing server logs: {exc}", "danger")
    return redirect(url_for("admin_logs"))


@admin_required
def view_transfer_logs():
    log_contents = read_text_file(current_app.config["DATA_TRANSFER_LOG"], "Transfer log file not found.")
    return render_template("transfer_logs.html", log_contents=log_contents)


@admin_required
def clear_transfer_logs():
    try:
        write_empty_file(current_app.config["DATA_TRANSFER_LOG"])
        logging.info("Transfer log file cleared by Admin.")
        flash("Transfer logs cleared.", "success")
    except Exception as exc:
        logging.exception("Error clearing transfer logs")
        flash(f"Error clearing transfer logs: {exc}", "danger")
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
    logging.warning("Shutdown initiated by admin")
    shutdown_server()
    return "Server shutting down..."



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

def register_routes(app):
    app.add_url_rule("/admin", "admin", admin)
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
    app.add_url_rule("/admin/shutdown", "shutdown", shutdown, methods=["POST"])
