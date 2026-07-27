from __future__ import annotations

import csv
import html
import io
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from flask import abort, after_this_request, current_app, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for

from lib.activity import list_activity, log_activity
from lib.backup import create_backup_zip
from lib.file_requests import create_request, get_request, is_expired, list_requests, update_request
from lib.extensions import bcrypt
from lib.metadata import all_metadata, get_metadata, move_metadata, set_metadata
from lib.notifications import list_notifications, mark_read, notify, unread_count
from lib.search_index import duplicate_groups, search_files
from lib.security import admin_required, login_required
from lib.storage import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    TEXT_PREVIEW_EXTENSIONS,
    VIDEO_EXTENSIONS,
    file_kind,
    format_bytes,
    get_folder_size,
    get_path_details,
    is_allowed_for_user,
    normalize_relative_path,
    safe_relative_upload_name,
    safe_upload_path,
    safe_path_part,
    visible_path_for_user,
)
from lib.trash import delete_forever, empty_trash, list_trash, restore
from lib.versions import get_version, list_versions, move_versions, restore_version, version_file_path
from lib.users import load_users, save_users



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
    return render_template(
        "preview.html",
        target=target,
        name=name,
        ext=ext,
        kind=kind,
        content=content,
        rows=rows,
        json_text=json_text,
        audio=ext in AUDIO_EXTENSIONS,
        image=ext in IMAGE_EXTENSIONS,
        video=ext in VIDEO_EXTENSIONS,
        file_info=get_path_details(target),
        metadata=get_metadata(target),
    )


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
    return render_template("metadata.html", target=target, metadata=get_metadata(target), file_info=get_path_details(target))


@login_required
def toggle_star_route():
    target = _target_from_request()
    meta = get_metadata(target)
    new_state = not bool(meta.get("starred"))
    updated = set_metadata(target, starred=new_state)
    log_activity("metadata.star", target, details={"starred": new_state})
    return jsonify(success=True, starred=bool(updated.get("starred")))


@login_required
def advanced_search():
    username = session.get("username")
    base = "" if username == "Admin" else username

    query = request.args.get("q", "").strip()
    kind = request.args.get("kind", "").strip()
    tag = request.args.get("tag", "").strip()
    content = request.args.get("content", "").strip()
    starred = request.args.get("starred") == "1"
    modified_days_raw = request.args.get("modified_days", "").strip()
    size_preset = request.args.get("size", "").strip()
    sort_mode = request.args.get("sort", "relevance").strip()

    def positive_int(value):
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    modified_days = positive_int(modified_days_raw)
    size_ranges = {
        "small": (None, 1024 * 1024),
        "medium": (1024 * 1024, 10 * 1024 * 1024),
        "large": (10 * 1024 * 1024, 100 * 1024 * 1024),
        "huge": (100 * 1024 * 1024, None),
    }
    min_size, max_size = size_ranges.get(size_preset, (None, None))

    has_search = any(
        [query, kind, tag, content, starred, modified_days is not None, size_preset]
    )
    results = search_files(
        base,
        query=query,
        kind=kind,
        tag=tag,
        content=content,
        starred=starred,
        min_size=min_size,
        max_size=max_size,
        modified_days=modified_days,
    )

    normalized_query = query.casefold()
    if sort_mode == "newest" or not has_search:
        results.sort(key=lambda item: (-item.get("mtime", 0), item["name"].casefold()))
    elif sort_mode == "oldest":
        results.sort(key=lambda item: (item.get("mtime", 0), item["name"].casefold()))
    elif sort_mode == "name":
        results.sort(key=lambda item: (not item["is_dir"], item["name"].casefold()))
    elif sort_mode == "largest":
        results.sort(key=lambda item: (-item.get("size", 0), item["name"].casefold()))
    elif sort_mode == "smallest":
        results.sort(key=lambda item: (item.get("size", 0), item["name"].casefold()))
    elif normalized_query:
        results.sort(
            key=lambda item: (
                item["name"].casefold() != normalized_query,
                not item["name"].casefold().startswith(normalized_query),
                not item["is_dir"],
                item["name"].casefold(),
            )
        )
    else:
        results.sort(key=lambda item: (-item.get("mtime", 0), item["name"].casefold()))

    show_recent = not has_search
    if show_recent:
        results = results[:12]

    tags = sorted({value for meta in all_metadata().values() for value in meta.get("tags", [])})
    return render_template(
        "advanced_search.html",
        results=results,
        tags=tags,
        show_recent=show_recent,
        has_search=has_search,
        size_preset=size_preset,
        sort_mode=sort_mode,
    )


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
    is_json = request.is_json
    payload = request.get_json(silent=True) if is_json else None
    payload = payload or {}

    if is_json:
        raw_sources = payload.get("source_paths") or payload.get("selected_paths") or []
        if isinstance(raw_sources, str):
            raw_sources = [raw_sources]
        source_path = payload.get("source_path")
        if source_path:
            raw_sources.insert(0, source_path)
        destination_raw = payload.get("destination_folder", "")
        operation = payload.get("operation", "move")
        new_name = payload.get("new_name", "")
    else:
        raw_sources = request.form.getlist("source_paths") or request.form.getlist("selected_paths")
        source_path = request.form.get("source_path")
        if source_path:
            raw_sources.insert(0, source_path)
        destination_raw = request.form.get("destination_folder", "")
        operation = request.form.get("operation", "move")
        new_name = request.form.get("new_name", "")

    sources = []
    for value in raw_sources:
        value = normalize_relative_path(value)
        if value and value not in sources:
            sources.append(_require_access(value))

    def finish(success: bool, message: str, status: int = 200, redirect_path: str | None = None):
        if is_json:
            return jsonify(success=success, message=message), status
        flash(message, "success" if success else "warning")
        return redirect(url_for("index", path=redirect_path or destination_folder))

    destination_folder = visible_path_for_user(destination_raw, session.get("username"))
    destination_folder = normalize_relative_path(destination_folder)
    operation = "copy" if operation == "copy" else "move"

    if not sources:
        return finish(False, "No source item selected.", 400, getattr(request, "referrer", None) or "")

    destination_folder_abs = safe_upload_path(destination_folder)
    os.makedirs(destination_folder_abs, exist_ok=True)
    if not os.path.isdir(destination_folder_abs):
        return finish(False, "Destination is not a folder.", 400, os.path.dirname(sources[0]))

    planned_operations = []
    planned_destinations = set()
    for source in sources:
        source_absolute = safe_upload_path(source)
        if not os.path.exists(source_absolute):
            return finish(False, f"Source does not exist: {source}", 404, os.path.dirname(source))

        filename = safe_path_part(new_name or os.path.basename(source)) if len(sources) == 1 else safe_path_part(os.path.basename(source))
        destination = normalize_relative_path(os.path.join(destination_folder, filename))
        dest_absolute = safe_upload_path(destination)

        if normalize_relative_path(source) == destination:
            return finish(False, "Source and destination are the same.", 400, os.path.dirname(source))

        if operation == "move" and os.path.isdir(source_absolute):
            source_prefix = normalize_relative_path(source).rstrip("/") + "/"
            destination_prefix = destination.rstrip("/") + "/"
            if destination_prefix.startswith(source_prefix):
                return finish(False, "A folder cannot be moved inside itself.", 400, os.path.dirname(source))

        if os.path.exists(dest_absolute) or destination in planned_destinations:
            return finish(False, f"Destination already exists: {destination}", 409, destination_folder)

        planned_destinations.add(destination)
        planned_operations.append((source, source_absolute, destination, dest_absolute))

    completed = 0
    for source, source_absolute, destination, dest_absolute in planned_operations:
        os.makedirs(os.path.dirname(dest_absolute), exist_ok=True)
        if operation == "copy":
            if os.path.isdir(source_absolute):
                shutil.copytree(source_absolute, dest_absolute)
            else:
                shutil.copy2(source_absolute, dest_absolute)
            log_activity("file.copy", source, details={"destination": destination})
        else:
            shutil.move(source_absolute, dest_absolute)
            move_metadata(source, destination)
            move_versions(source, destination)
            log_activity("file.move", source, details={"destination": destination})
        completed += 1

    action = "Copied" if operation == "copy" else "Moved"
    return finish(True, f"{action} {completed} item(s).", 200, destination_folder)

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


def _file_request_destination_options(username: str) -> list[dict]:
    base_path = "" if username == "Admin" else normalize_relative_path(username)
    base_absolute = safe_upload_path(base_path)
    options = [{
        "path": base_path,
        "label": "Storage root" if username == "Admin" else "My files",
        "name": "Storage root" if username == "Admin" else "My files",
        "parent": None,
        "child_count": 0,
    }]
    if not os.path.isdir(base_absolute):
        return options

    for root, directories, _files in os.walk(base_absolute):
        directories[:] = sorted(
            (name for name in directories if name != ".tamestorage_system"),
            key=str.casefold,
        )
        for directory in directories:
            absolute = os.path.join(root, directory)
            relative = normalize_relative_path(
                os.path.join(base_path, os.path.relpath(absolute, base_absolute))
            )
            display_path = relative
            if username != "Admin" and relative == username:
                display_path = ""
            elif username != "Admin" and relative.startswith(username + "/"):
                display_path = relative[len(username) + 1:]
            options.append({
                "path": relative,
                "label": display_path.replace("/", " › ") or "My files",
                "name": directory,
                "parent": normalize_relative_path(os.path.dirname(relative)),
                "child_count": 0,
            })

    options_by_path = {option["path"]: option for option in options}
    for option in options[1:]:
        parent = options_by_path.get(option["parent"])
        if parent:
            parent["child_count"] += 1
    return options


@login_required
def folder_destinations_api():
    username = session.get("username")
    folders = _file_request_destination_options(username)
    root = folders[0]
    return jsonify(
        success=True,
        root_path=root["path"],
        root_label=root["label"],
        folders=folders,
    )


@login_required
def file_requests_page():
    username = session.get("username")
    destination_options = _file_request_destination_options(username)
    allowed_destinations = {option["path"] for option in destination_options}
    default_destination = username if username in allowed_destinations else destination_options[0]["path"]
    selected_destination = default_destination
    if request.method == "POST":
        destination = visible_path_for_user(request.form.get("destination", default_destination), username)
        if destination not in allowed_destinations:
            flash("Choose an existing destination folder from the list.", "warning")
            selected_option = next(option for option in destination_options if option["path"] == default_destination)
            return render_template(
                "file_requests.html",
                requests=list_requests(username),
                destination_options=destination_options,
                selected_destination=default_destination,
                selected_destination_label=selected_option["label"],
                form_values=request.form,
            ), 400
        item = create_request(request.form.get("title", ""), destination, request.form.get("password", ""), request.form.get("expires_at", ""), request.form.get("allowed_ext", ""), request.form.get("max_size_mb", ""))
        flash("Upload request created.", "success")
        log_activity("file_request.create", item["id"])
        return redirect(url_for("file_requests_page"))
    return render_template(
        "file_requests.html",
        requests=list_requests(username),
        destination_options=destination_options,
        selected_destination=selected_destination,
        selected_destination_label=next(option["label"] for option in destination_options if option["path"] == selected_destination),
        form_values={},
    )


@login_required
def disable_file_request(request_id):
    item = get_request(request_id)
    if not item:
        abort(404)
    if session.get("username") not in {item.get("owner"), "Admin"}:
        abort(403)
    if item.get("active"):
        update_request(request_id, active=False)
        flash("File request disabled.", "success")
    else:
        flash("File request is already disabled.", "info")
    return redirect(url_for("file_requests_page"))


def public_file_request(request_id):
    item = get_request(request_id)
    if not item:
        return render_template(
            "shared_gate.html",
            share=None,
            resource_name="File request",
            message="This upload request does not exist.",
            password_required=False,
            gate_kind="missing",
            gate_label="Request not found",
        ), 404
    if not item.get("active"):
        return render_template(
            "shared_gate.html",
            share=None,
            resource_name=item.get("title") or "File request",
            message="This upload request has been disabled by its owner.",
            password_required=False,
            gate_kind="unavailable",
            gate_label="Request disabled",
        ), 410
    if is_expired(item):
        return render_template(
            "shared_gate.html",
            share=None,
            resource_name=item.get("title") or "File request",
            message="This upload request has expired.",
            password_required=False,
            gate_kind="unavailable",
            gate_label="Request expired",
        ), 410
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



@login_required
def recent_page():
    username = session.get("username")
    base = "" if username == "Admin" else username
    results = sorted(search_files(base), key=lambda item: item.get("mtime", 0), reverse=True)[:100]
    return render_template("recent.html", results=results)


@login_required
def settings_page():
    username = session.get("username")
    users = load_users()
    user = users.get(username, {})

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "change_password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not bcrypt.check_password_hash(user.get("password", ""), current_password):
                flash("Current password is incorrect.", "danger")
            elif len(new_password) < 8:
                flash("New password must be at least 8 characters.", "warning")
            elif new_password != confirm_password:
                flash("New password confirmation does not match.", "warning")
            else:
                user["password"] = bcrypt.generate_password_hash(new_password).decode("utf-8")
                users[username] = user
                save_users(users)
                flash("Password updated successfully.", "success")
                log_activity("account.password_change", username)
            return redirect(url_for("settings_page"))

    base = "" if username == "Admin" else username
    absolute = safe_upload_path(base)
    used_bytes = get_folder_size(absolute) if os.path.isdir(absolute) else 0
    quota_bytes = int(user.get("quota_bytes") or current_app.config.get("DEFAULT_USER_QUOTA_BYTES", 0) or 0) if username != "Admin" else 0
    results = search_files(base)
    file_count = sum(1 for item in results if not item.get("is_dir"))
    folder_count = sum(1 for item in results if item.get("is_dir"))
    return render_template(
        "settings.html",
        used_label=format_bytes(used_bytes),
        quota_label=format_bytes(quota_bytes) if quota_bytes else "Unlimited",
        storage_percent=min(100, round((used_bytes / quota_bytes) * 100, 1)) if quota_bytes else 0,
        file_count=file_count,
        folder_count=folder_count,
    )

def register_routes(app):
    app.add_url_rule("/recent", "recent_page", recent_page)
    app.add_url_rule("/settings", "settings_page", settings_page, methods=["GET", "POST"])
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
    app.add_url_rule("/metadata/toggle-star", "toggle_star_route", toggle_star_route, methods=["POST"])
    app.add_url_rule("/search", "advanced_search", advanced_search)
    app.add_url_rule("/starred", "starred_files", starred_files)
    app.add_url_rule("/duplicates", "duplicates_page", duplicates_page)
    app.add_url_rule("/move-copy", "move_copy_item", move_copy_item, methods=["POST"])
    app.add_url_rule("/gallery", "gallery_page", gallery_page)
    app.add_url_rule("/backup", "backup_page", backup_page)
    app.add_url_rule("/backup/download", "download_backup", download_backup, methods=["POST"])
    app.add_url_rule("/activity", "activity_page", activity_page)
    app.add_url_rule("/notifications", "notifications_page", notifications_page, methods=["GET", "POST"])
    app.add_url_rule("/api/folders", "folder_destinations_api", folder_destinations_api)
    app.add_url_rule("/file-requests", "file_requests_page", file_requests_page, methods=["GET", "POST"])
    app.add_url_rule("/file-requests/disable/<request_id>", "disable_file_request", disable_file_request, methods=["POST"])
    app.add_url_rule("/request/<request_id>", "public_file_request", public_file_request, methods=["GET", "POST"])
    app.add_url_rule("/integrity", "integrity_page", integrity_page)
