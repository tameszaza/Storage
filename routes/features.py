from __future__ import annotations

import csv
import html
import io
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from flask import abort, after_this_request, current_app, flash, redirect, render_template, request, send_file, send_from_directory, session, url_for

from lib.activity import list_activity, log_activity
from lib.backup import create_backup_zip
from lib.file_requests import create_request, get_request, is_expired, list_requests, update_request
from lib.metadata import all_metadata, get_metadata, set_metadata
from lib.notifications import list_notifications, mark_read, notify, unread_count
from lib.search_index import duplicate_groups, search_files
from lib.security import admin_required, login_required
from lib.storage import (
    IMAGE_EXTENSIONS,
    TEXT_PREVIEW_EXTENSIONS,
    VIDEO_EXTENSIONS,
    file_kind,
    format_bytes,
    is_allowed_for_user,
    normalize_relative_path,
    safe_relative_upload_name,
    safe_upload_path,
    safe_path_part,
    visible_path_for_user,
)
from lib.trash import delete_forever, empty_trash, list_trash, restore
from lib.versions import get_version, list_versions, restore_version, version_file_path

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}


def _require_access(target: str) -> str:
    target = normalize_relative_path(target)
    if not is_allowed_for_user(target, session.get("username")):
        abort(403)
    return target


def _target_from_request() -> str:
    return _require_access(request.values.get("target", ""))


@login_required
def preview_file():
    target = _target_from_request()
    absolute = safe_upload_path(target)
    if not os.path.isfile(absolute):
        abort(404)
    name = os.path.basename(target)
    ext = Path(name).suffix.lower()
    kind = file_kind(name, False)
    content = None
    rows = None
    json_text = None
    if ext in TEXT_PREVIEW_EXTENSIONS or ext in {".csv", ".json"}:
        try:
            text = Path(absolute).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if ext == ".csv":
            rows = list(csv.reader(io.StringIO(text)))[:200]
        elif ext == ".json":
            json_text = text
        else:
            content = text
    return render_template("preview.html", target=target, name=name, ext=ext, kind=kind, content=content, rows=rows, json_text=json_text, audio=ext in AUDIO_EXTENSIONS, image=ext in IMAGE_EXTENSIONS, video=ext in VIDEO_EXTENSIONS)


@login_required
def raw_file():
    target = _target_from_request()
    absolute = safe_upload_path(target)
    if not os.path.isfile(absolute):
        abort(404)
    return send_from_directory(os.path.dirname(absolute), os.path.basename(absolute))


@login_required
def trash_page():
    return render_template("trash.html", items=list_trash(session.get("username")))


@login_required
def restore_trash_item(item_id):
    if restore(item_id):
        flash("Item restored.", "success")
        log_activity("trash.restore", item_id)
    else:
        flash("Could not restore item.", "warning")
    return redirect(url_for("trash_page"))


@login_required
def delete_trash_item(item_id):
    if delete_forever(item_id):
        flash("Item permanently deleted.", "success")
        log_activity("trash.delete_forever", item_id)
    return redirect(url_for("trash_page"))


@login_required
def empty_trash_route():
    count = empty_trash(session.get("username"))
    flash(f"Emptied {count} item(s) from trash.", "success")
    log_activity("trash.empty", details={"count": count})
    return redirect(url_for("trash_page"))


@login_required
def versions_page():
    target = _target_from_request()
    return render_template("versions.html", target=target, versions=list_versions(target))


@login_required
def download_version(version_id):
    target = _target_from_request()
    entry = get_version(target, version_id)
    if not entry:
        abort(404)
    path = version_file_path(entry)
    return send_file(path, as_attachment=True, download_name=os.path.basename(target))


@login_required
def restore_version_route(version_id):
    target = _target_from_request()
    if restore_version(target, version_id):
        flash("Version restored. The previous current file was saved as a new version.", "success")
        log_activity("version.restore", target)
    else:
        flash("Could not restore version.", "warning")
    return redirect(url_for("versions_page", target=target))


@login_required
def metadata_page():
    target = _target_from_request()
    if request.method == "POST":
        tags = request.form.get("tags", "").split(",")
        note = request.form.get("note", "")
        starred = request.form.get("starred") == "on"
        set_metadata(target, tags=tags, note=note, starred=starred)
        flash("Metadata saved.", "success")
        log_activity("metadata.update", target)
        return redirect(url_for("metadata_page", target=target))
    return render_template("metadata.html", target=target, metadata=get_metadata(target))


@login_required
def advanced_search():
    username = session.get("username")
    base = "" if username == "Admin" else username
    query = request.args.get("q", "")
    kind = request.args.get("kind", "")
    tag = request.args.get("tag", "")
    content = request.args.get("content", "")
    starred = request.args.get("starred") == "1"
    min_size = request.args.get("min_size", "")
    max_size = request.args.get("max_size", "")
    modified_days = request.args.get("modified_days", "")
    to_int = lambda v: int(v) if str(v).isdigit() else None
    results = []
    if request.args:
        results = search_files(base, query=query, kind=kind, tag=tag, content=content, starred=starred, min_size=to_int(min_size), max_size=to_int(max_size), modified_days=to_int(modified_days))
    tags = sorted({tag for meta in all_metadata().values() for tag in meta.get("tags", [])})
    return render_template("advanced_search.html", results=results, tags=tags)


@login_required
def starred_files():
    username = session.get("username")
    base = "" if username == "Admin" else username
    results = search_files(base, starred=True)
    return render_template("advanced_search.html", results=results, tags=[], starred_page=True)


@login_required
def duplicates_page():
    username = session.get("username")
    base = "" if username == "Admin" else username
    return render_template("duplicates.html", groups=duplicate_groups(base))


@login_required
def move_copy_item():
    source = _require_access(request.form.get("source_path", ""))
    destination_folder = visible_path_for_user(request.form.get("destination_folder", ""), session.get("username"))
    operation = request.form.get("operation", "move")
    source_absolute = safe_upload_path(source)
    if not os.path.exists(source_absolute):
        abort(404)
    filename = safe_path_part(request.form.get("new_name") or os.path.basename(source))
    dest_absolute = safe_upload_path(destination_folder, filename)
    if os.path.exists(dest_absolute):
        flash("Destination already exists.", "warning")
        return redirect(url_for("index", path=os.path.dirname(source)))
    os.makedirs(os.path.dirname(dest_absolute), exist_ok=True)
    if operation == "copy":
        if os.path.isdir(source_absolute):
            shutil.copytree(source_absolute, dest_absolute)
        else:
            shutil.copy2(source_absolute, dest_absolute)
        log_activity("file.copy", source, details={"destination": normalize_relative_path(os.path.join(destination_folder, filename))})
    else:
        shutil.move(source_absolute, dest_absolute)
        log_activity("file.move", source, details={"destination": normalize_relative_path(os.path.join(destination_folder, filename))})
    flash("File operation completed.", "success")
    return redirect(url_for("index", path=destination_folder))


@login_required
def gallery_page():
    path = visible_path_for_user(request.args.get("path", ""), session.get("username"))
    results = [item for item in search_files(path, kind="image")]
    return render_template("gallery.html", path=path, images=results)


@admin_required
def backup_page():
    return render_template("backup.html")


@admin_required
def download_backup():
    zip_path = create_backup_zip()

    @after_this_request
    def cleanup(response):
        shutil.rmtree(os.path.dirname(zip_path), ignore_errors=True)
        return response

    log_activity("backup.create", "full")
    return send_file(zip_path, as_attachment=True, mimetype="application/zip", download_name=os.path.basename(zip_path))


@admin_required
def activity_page():
    return render_template("activity.html", entries=list_activity())


@login_required
def notifications_page():
    username = session.get("username")
    if request.method == "POST":
        mark_read(username)
        return redirect(url_for("notifications_page"))
    return render_template("notifications.html", items=list_notifications(username))


@login_required
def file_requests_page():
    username = session.get("username")
    if request.method == "POST":
        destination = visible_path_for_user(request.form.get("destination", username or ""), username)
        item = create_request(request.form.get("title", ""), destination, request.form.get("password", ""), request.form.get("expires_at", ""), request.form.get("allowed_ext", ""), request.form.get("max_size_mb", ""))
        flash("Upload request created.", "success")
        log_activity("file_request.create", item["id"])
        return redirect(url_for("file_requests_page"))
    return render_template("file_requests.html", requests=list_requests(username))


@login_required
def disable_file_request(request_id):
    update_request(request_id, active=False)
    flash("File request disabled.", "success")
    return redirect(url_for("file_requests_page"))


def public_file_request(request_id):
    item = get_request(request_id)
    if not item or not item.get("active") or is_expired(item):
        return render_template("shared_gate.html", error="This upload request is not available."), 404
    if request.method == "POST":
        if item.get("password") and request.form.get("password") != item.get("password"):
            return render_template("file_request_upload.html", item=item, error="Wrong password."), 403
        files = request.files.getlist("file")
        uploaded_count = 0
        for uploaded in files:
            if not uploaded or not uploaded.filename:
                continue
            relative_name = safe_relative_upload_name(uploaded.filename)
            ext = os.path.splitext(relative_name)[1].lower().lstrip(".")
            if item.get("allowed_ext") and ext not in item["allowed_ext"]:
                continue
            if item.get("max_size_mb") and uploaded.content_length and uploaded.content_length > item["max_size_mb"] * 1024 * 1024:
                continue
            dest = safe_upload_path(item["destination"], relative_name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            uploaded.save(dest)
            uploaded_count += 1
        update_request(request_id, uploads=int(item.get("uploads", 0)) + uploaded_count)
        notify(item.get("owner", "Admin"), "File request upload", f"{uploaded_count} file(s) uploaded to {item.get('destination')}")
        log_activity("file_request.upload", request_id, details={"count": uploaded_count}, username="public")
        return render_template("file_request_upload.html", item=item, success=f"Uploaded {uploaded_count} file(s).")
    return render_template("file_request_upload.html", item=item)


@admin_required
def integrity_page():
    username = session.get("username")
    base = "" if username == "Admin" else username
    results = search_files(base)
    missing = []
    total_size = sum(item["size"] for item in results if not item["is_dir"])
    return render_template("integrity.html", total=len(results), total_size=format_bytes(total_size), missing=missing)


def register_routes(app):
    app.add_url_rule("/preview", "preview_file", preview_file)
    app.add_url_rule("/raw", "raw_file", raw_file)
    app.add_url_rule("/trash", "trash_page", trash_page)
    app.add_url_rule("/trash/restore/<item_id>", "restore_trash_item", restore_trash_item, methods=["POST"])
    app.add_url_rule("/trash/delete/<item_id>", "delete_trash_item", delete_trash_item, methods=["POST"])
    app.add_url_rule("/trash/empty", "empty_trash_route", empty_trash_route, methods=["POST"])
    app.add_url_rule("/versions", "versions_page", versions_page)
    app.add_url_rule("/versions/download/<version_id>", "download_version", download_version)
    app.add_url_rule("/versions/restore/<version_id>", "restore_version_route", restore_version_route, methods=["POST"])
    app.add_url_rule("/metadata", "metadata_page", metadata_page, methods=["GET", "POST"])
    app.add_url_rule("/search", "advanced_search", advanced_search)
    app.add_url_rule("/starred", "starred_files", starred_files)
    app.add_url_rule("/duplicates", "duplicates_page", duplicates_page)
    app.add_url_rule("/move-copy", "move_copy_item", move_copy_item, methods=["POST"])
    app.add_url_rule("/gallery", "gallery_page", gallery_page)
    app.add_url_rule("/backup", "backup_page", backup_page)
    app.add_url_rule("/backup/download", "download_backup", download_backup, methods=["POST"])
    app.add_url_rule("/activity", "activity_page", activity_page)
    app.add_url_rule("/notifications", "notifications_page", notifications_page, methods=["GET", "POST"])
    app.add_url_rule("/file-requests", "file_requests_page", file_requests_page, methods=["GET", "POST"])
    app.add_url_rule("/file-requests/disable/<request_id>", "disable_file_request", disable_file_request, methods=["POST"])
    app.add_url_rule("/request/<request_id>", "public_file_request", public_file_request, methods=["GET", "POST"])
    app.add_url_rule("/integrity", "integrity_page", integrity_page)
