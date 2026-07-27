import logging
import os
import shutil
import tempfile
from pathlib import Path
from flask import abort, after_this_request, current_app, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for
from lib.charts import build_storage_analysis
from lib.editor_service import EditorConflictError, load_document, save_document
from lib.activity import log_activity
from lib.metadata import get_metadata, move_metadata
from lib.trash import move_to_trash
from lib.versions import create_version, move_versions
from lib.users import load_users
from lib.security import login_required
from lib.storage import (
    TEXT_PREVIEW_EXTENSIONS,
    delete_path,
    format_bytes,
    get_folder_size,
    is_allowed_for_user,
    list_directory,
    normalize_relative_path,
    rename_path,
    safe_filename,
    safe_path_part,
    safe_relative_upload_name,
    safe_upload_path,
    upload_root,
    valid_folder_name,
    visible_path_for_user,
)
from lib.users import ensure_user_folder


def _parent_path(path: str) -> str:
    path = normalize_relative_path(path)
    return "/".join(path.split("/")[:-1])


def _require_path_access(path: str) -> str:
    username = session.get("username")
    normalized = normalize_relative_path(path)
    if not is_allowed_for_user(normalized, username):
        abort(403)
    return normalized


def _wants_json_response() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"


def _upload_result(success: bool, message: str, path: str, status: int = 200, count: int = 0, **details):
    if _wants_json_response():
        payload = {
            "success": success,
            "message": message,
            "uploaded": count,
            "redirect_url": url_for("index", path=path),
        }
        payload.update(details)
        return jsonify(**payload), status
    if message:
        flash(message, "success" if success else "warning")
    return redirect(url_for("index", path=path))


def _available_upload_target(relative_target: str, reserved: set[str]) -> str:
    directory, filename = os.path.split(relative_target)
    stem, extension = os.path.splitext(filename)
    number = 1
    while True:
        candidate_name = f"{stem} ({number}){extension}"
        candidate = normalize_relative_path(os.path.join(directory, candidate_name))
        if candidate not in reserved and not os.path.exists(safe_upload_path(candidate)):
            return candidate
        number += 1


def _upload_conflict_names(path: str, filenames: list[str]) -> list[str]:
    conflicts = []
    seen_targets = set()
    for filename in filenames:
        relative_name = safe_relative_upload_name(filename)
        relative_target = normalize_relative_path(os.path.join(path, relative_name))
        if os.path.exists(safe_upload_path(relative_target)) or relative_target in seen_targets:
            conflicts.append(relative_name)
        seen_targets.add(relative_target)
    return list(dict.fromkeys(conflicts))


@login_required
def index(path=""):
    path = visible_path_for_user(path, session.get("username"))
    current_path = safe_upload_path(path)
    if not os.path.exists(current_path):
        logging.error("Folder not found: %s", current_path)
        abort(404)

    search_query = request.args.get("search", "").strip() or None
    files = list_directory(path, search_query)
    for item in files:
        item["metadata"] = get_metadata(item.get("relative_path", ""))
    readme_path = safe_upload_path(path, "README.md")
    folder_readme = None
    if os.path.isfile(readme_path):
        with open(readme_path, "r", encoding="utf-8", errors="replace") as readme_file:
            folder_readme = readme_file.read(4000)
    return render_template("index.html", files=files, path=path, search_query=search_query, folder_readme=folder_readme)


@login_required
def user_folder(username):
    if session.get("username") != username:
        logging.warning("Unauthorized access attempt to user folder: %s", username)
        return redirect(url_for("login"))
    ensure_user_folder(username)
    return redirect(url_for("index", path=username))


@login_required
def upload_conflicts(path=""):
    path = visible_path_for_user(path, session.get("username"))
    payload = request.get_json(silent=True) or {}
    filenames = payload.get("filenames") or []
    if not isinstance(filenames, list):
        return jsonify(success=False, message="Invalid upload selection."), 400
    filenames = [str(name) for name in filenames if str(name).strip()]
    return jsonify(success=True, conflicts=_upload_conflict_names(path, filenames))


@login_required
def upload_file(path=""):
    path = visible_path_for_user(path, session.get("username"))
    files = request.files.getlist("file")
    if not files or not any(file.filename for file in files):
        return _upload_result(False, "Choose at least one file to upload.", path, 400)

    conflict_action = request.form.get("conflict_action", "").strip().lower()
    if conflict_action not in {"", "replace", "rename"}:
        return _upload_result(False, "Choose a valid duplicate-file action.", path, 400)

    current_path = safe_upload_path(path)
    os.makedirs(current_path, exist_ok=True)

    prepared = []
    conflicts = []
    seen_targets = set()
    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue
        relative_name = safe_relative_upload_name(uploaded.filename)
        relative_target = normalize_relative_path(os.path.join(path, relative_name))
        destination_exists = os.path.exists(safe_upload_path(relative_target))
        if destination_exists or relative_target in seen_targets:
            conflicts.append(relative_name)
        seen_targets.add(relative_target)
        prepared.append((uploaded, relative_name, relative_target))

    if conflicts and not conflict_action:
        return _upload_result(
            False,
            "One or more files already exist. Choose whether to replace or rename them.",
            path,
            409,
            conflict=True,
            conflicts=list(dict.fromkeys(conflicts)),
        )

    planned = []
    reserved_targets = set()
    renamed_count = 0
    for uploaded, relative_name, relative_target in prepared:
        final_target = relative_target
        if conflict_action == "rename" and (
            os.path.exists(safe_upload_path(final_target)) or final_target in reserved_targets
        ):
            final_target = _available_upload_target(final_target, reserved_targets)
            renamed_count += 1
        if conflict_action == "replace" and os.path.isdir(safe_upload_path(final_target)):
            return _upload_result(
                False,
                f'A folder already uses the name "{relative_name}". Choose Rename to keep both.',
                path,
                409,
            )
        reserved_targets.add(final_target)
        planned.append((uploaded, final_target))

    username = session.get("username")
    if username != "Admin":
        users = load_users()
        quota = int(users.get(username, {}).get("quota_bytes") or current_app.config.get("DEFAULT_USER_QUOTA_BYTES", 0) or 0)
        if quota:
            incoming = 0
            replaced_bytes = 0
            replaced_targets = set()
            for uploaded, relative_target in planned:
                try:
                    position = uploaded.stream.tell()
                    uploaded.stream.seek(0, os.SEEK_END)
                    incoming += uploaded.stream.tell()
                    uploaded.stream.seek(position)
                except Exception:
                    pass
                if conflict_action == "replace" and relative_target not in replaced_targets:
                    existing_path = safe_upload_path(relative_target)
                    if os.path.isfile(existing_path):
                        replaced_bytes += os.path.getsize(existing_path)
                        replaced_targets.add(relative_target)
            used = get_folder_size(safe_upload_path(username))
            if used - replaced_bytes + incoming > quota:
                return _upload_result(False, "Upload blocked because it would exceed your storage quota.", path, 413)

    uploaded_count = 0
    for uploaded, relative_target in planned:
        filepath = safe_upload_path(relative_target)
        if os.path.exists(filepath) and os.path.isfile(filepath):
            create_version(relative_target, "before upload replace")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        uploaded.save(filepath)
        uploaded_count += 1
        logging.info("File uploaded: %s", filepath)
        log_activity("file.upload", relative_target)

    label = "file" if uploaded_count == 1 else "files"
    message = f"Uploaded {uploaded_count} {label} successfully."
    if renamed_count:
        message = f"Uploaded {uploaded_count} {label}; renamed {renamed_count} to keep both."
    return _upload_result(True, message, path, 201, uploaded_count)


@login_required
def uploaded_file(path, filename):
    path = _require_path_access(path)
    directory = safe_upload_path(path)
    return send_from_directory(directory, safe_filename(filename))


@login_required
def download_file(path, filename):
    path = _require_path_access(path)
    filename = safe_filename(filename)
    logging.info("File downloaded: %s", os.path.join(path, filename))
    log_activity("file.download", os.path.join(path, filename))
    return send_from_directory(safe_upload_path(path), filename, as_attachment=True)


@login_required
def download_folder(path):
    path = _require_path_access(path)
    folder_path = safe_upload_path(path)
    if not os.path.isdir(folder_path):
        abort(404)

    temp_dir = tempfile.mkdtemp(prefix="tamestorage_zip_")
    zip_base = os.path.join(temp_dir, os.path.basename(folder_path) or "folder")
    zip_path = shutil.make_archive(zip_base, "zip", folder_path)

    @after_this_request
    def cleanup(response):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return response

    return send_file(zip_path, as_attachment=True, mimetype="application/zip", download_name=os.path.basename(zip_path))


@login_required
def download_selected():
    current_path = visible_path_for_user(request.form.get("current_path", ""), session.get("username"))
    selected_files = request.form.getlist("selected_files")
    if not selected_files:
        return jsonify(success=False, message="No files selected."), 400

    temp_dir = tempfile.mkdtemp(prefix="tamestorage_selected_")
    try:
        bundle_dir = os.path.join(temp_dir, "selected")
        os.makedirs(bundle_dir, exist_ok=True)
        for name in selected_files:
            source = safe_upload_path(current_path, safe_filename(name))
            if not os.path.exists(source):
                continue
            destination = os.path.join(bundle_dir, safe_filename(name))
            if os.path.isdir(source):
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        zip_path = shutil.make_archive(os.path.join(temp_dir, "selected_files"), "zip", bundle_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    @after_this_request
    def cleanup(response):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return response

    return send_file(zip_path, as_attachment=True, mimetype="application/zip", download_name="selected_files.zip")


@login_required
def toggle_dark_mode():
    mode = (request.json or {}).get("mode")
    if mode in ["dark", "light"]:
        session["dark_mode"] = mode
    return jsonify(success=True)


@login_required
def delete_selected():
    current_path = visible_path_for_user(request.form.get("current_path", ""), session.get("username"))
    selected_files = request.form.getlist("selected_files")
    if not selected_files:
        return jsonify(success=False, message="No files selected."), 400
    for name in selected_files:
        target = os.path.join(current_path, safe_filename(name))
        move_to_trash(target)
        log_activity("file.trash", target)
    return jsonify(success=True)


@login_required
def delete_file(path, filename):
    path = _require_path_access(path)
    target = os.path.join(path, safe_filename(filename))
    move_to_trash(target)
    logging.info("File moved to trash: %s", target)
    log_activity("file.trash", target)
    return redirect(url_for("index", path=path))


@login_required
def delete_folder(path=""):
    path = _require_path_access(path)
    if not path:
        abort(400)
    move_to_trash(path)
    logging.info("Folder moved to trash: %s", path)
    log_activity("folder.trash", path)
    return redirect(url_for("index", path=_parent_path(path)))


@login_required
def edit_file(path, filename):
    path = _require_path_access(path)
    filename = safe_filename(filename)
    file_path = safe_upload_path(path, filename)
    if os.path.isdir(file_path):
        flash("The selected path is a directory, not a file.", "danger")
        return redirect(url_for("index", path=path))
    if os.path.splitext(filename)[1].lower() not in TEXT_PREVIEW_EXTENSIONS:
        flash("Only text-based files can be edited safely in the browser.", "warning")
        return redirect(url_for("index", path=path))
    try:
        document = load_document(file_path)
    except OSError:
        abort(404)
    return render_template(
        "editfile.html",
        filename=filename,
        content=document.content,
        path=path,
        file_mtime_ns=document.mtime_ns,
        file_revision=document.revision,
    )


@login_required
def save_file(path, filename):
    path = _require_path_access(path)
    filename = safe_filename(filename)
    file_path = safe_upload_path(path, filename)
    payload = request.get_json(silent=True) if request.is_json else None
    payload = payload if isinstance(payload, dict) else {}
    new_content = payload.get("file_content") if request.is_json else request.form.get("file_content", "")
    new_content = str(new_content or "")
    expected_raw = payload.get("expected_mtime_ns") if request.is_json else request.form.get("expected_mtime_ns")
    expected_revision = str(
        payload.get("expected_revision") if request.is_json else request.form.get("expected_revision") or ""
    ).strip() or None
    try:
        expected_mtime_ns = int(expected_raw) if expected_raw not in (None, "") else None
    except (TypeError, ValueError):
        expected_mtime_ns = None

    try:
        current_document = load_document(file_path)
    except OSError:
        abort(404)

    revision_conflict = bool(expected_revision and current_document.revision != expected_revision)
    legacy_mtime_conflict = bool(
        not expected_revision
        and expected_mtime_ns is not None
        and current_document.mtime_ns != expected_mtime_ns
    )
    if revision_conflict or legacy_mtime_conflict:
        message = "This file changed on the server after you opened it."
        if request.is_json or _wants_json_response():
            return jsonify(success=False, conflict=True, message=message), 409
        flash(message, "warning")
        return redirect(url_for("edit_file", path=path, filename=filename))

    changed = current_document.content != new_content
    if changed:
        create_version(os.path.join(path, filename), "before browser edit")
    try:
        saved_document = save_document(
            file_path,
            new_content,
            expected_revision=expected_revision,
            expected_mtime_ns=expected_mtime_ns,
        )
    except EditorConflictError as error:
        if request.is_json or _wants_json_response():
            return jsonify(success=False, conflict=True, message=str(error)), 409
        flash(str(error), "warning")
        return redirect(url_for("edit_file", path=path, filename=filename))
    except OSError as error:
        logging.exception("Could not save text file")
        if request.is_json or _wants_json_response():
            return jsonify(success=False, message=f"Could not save file: {error}"), 500
        flash(f"Could not save file: {error}", "danger")
        return redirect(url_for("edit_file", path=path, filename=filename))

    if changed:
        log_activity("file.edit", os.path.join(path, filename))

    if request.is_json or _wants_json_response():
        return jsonify(
            success=True,
            changed=changed,
            message="Saved" if changed else "No changes",
            revision=saved_document.revision,
            mtime_ns=saved_document.mtime_ns,
            size_bytes=saved_document.size_bytes,
            folder_url=url_for("index", path=path),
            preview_url=url_for("preview_file", target=os.path.join(path, filename)),
        )

    flash("File saved successfully." if changed else "No changes to save.", "success" if changed else "info")
    if request.form.get("save_action") == "continue":
        return redirect(url_for("edit_file", path=path, filename=filename))
    return redirect(url_for("index", path=path))


@login_required
def rename_file(path, filename):
    path = _require_path_access(path)
    new_filename = request.form.get("new_name", "").strip()
    if not new_filename:
        flash("Please provide a new file name.", "warning")
        return redirect(url_for("index", path=path))
    try:
        old_target = normalize_relative_path(os.path.join(path, filename))
        new_target = normalize_relative_path(os.path.join(path, new_filename))
        rename_path(path, filename, new_filename)
        move_metadata(old_target, new_target)
        move_versions(old_target, new_target)
        log_activity("file.rename", old_target, details={"new_path": new_target})
    except FileExistsError:
        flash("A file with that name already exists.", "warning")
    except FileNotFoundError:
        abort(404)
    return redirect(url_for("index", path=path))


@login_required
def rename_folder(path, foldername):
    path = _require_path_access(path)
    new_folder_name = request.form.get("new_name", "").strip()
    if not valid_folder_name(new_folder_name):
        flash("Invalid folder name.", "warning")
        return redirect(url_for("index", path=path))
    try:
        old_target = normalize_relative_path(os.path.join(path, foldername))
        new_target = normalize_relative_path(os.path.join(path, new_folder_name))
        rename_path(path, foldername, new_folder_name)
        move_metadata(old_target, new_target)
        log_activity("folder.rename", old_target, details={"new_path": new_target})
    except FileExistsError:
        flash("A folder with that name already exists.", "warning")
    except FileNotFoundError:
        abort(404)
    return redirect(url_for("index", path=path))


@login_required
def create_folder(path=""):
    path = visible_path_for_user(path, session.get("username"))
    folder_name = request.form.get("folder_name", "").strip()
    if not valid_folder_name(folder_name):
        flash("Invalid folder name.", "warning")
        return redirect(url_for("index", path=path))
    os.makedirs(safe_upload_path(path, folder_name), exist_ok=True)
    logging.info("Folder created: %s", os.path.join(path, folder_name))
    log_activity("folder.create", os.path.join(path, folder_name))
    return redirect(url_for("index", path=path))




@login_required
def create_text_file(path=""):
    path = visible_path_for_user(path, session.get("username"))
    raw_name = request.form.get("file_name", "").strip()
    requested_extension = request.form.get("extension", ".txt").strip().lower()
    allowed_extensions = sorted(TEXT_PREVIEW_EXTENSIONS)
    if not raw_name:
        flash("Enter a name for the new text file.", "warning")
        return redirect(url_for("index", path=path, new="text"))

    if not requested_extension.startswith("."):
        requested_extension = "." + requested_extension
    if requested_extension not in TEXT_PREVIEW_EXTENSIONS:
        requested_extension = ".txt"

    cleaned_name = safe_path_part(raw_name, "untitled")
    existing_extension = os.path.splitext(cleaned_name)[1].lower()
    if existing_extension:
        if existing_extension not in TEXT_PREVIEW_EXTENSIONS:
            flash("Choose a supported text-file extension.", "warning")
            return redirect(url_for("index", path=path, new="text"))
        filename = cleaned_name
    else:
        filename = cleaned_name + requested_extension

    target = normalize_relative_path(os.path.join(path, filename))
    absolute = safe_upload_path(target)
    if os.path.exists(absolute):
        flash("A file with that name already exists.", "warning")
        return redirect(url_for("index", path=path, new="text"))

    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    Path(absolute).write_text(request.form.get("initial_content", ""), encoding="utf-8")
    logging.info("Text file created: %s", target)
    log_activity("file.create_text", target)
    flash(f"Created {filename}.", "success")
    return redirect(url_for("edit_file", path=path, filename=filename))


@login_required
def detail(directory):
    if directory == "Root":
        if session.get("username") != "Admin":
            abort(403)
        directory = ""
    directory = _require_path_access(directory)
    directory_path = safe_upload_path(directory)
    if not os.path.isdir(directory_path):
        abort(404)

    chart_title = os.path.basename(directory.rstrip("/")) if directory else "Root"
    storage_tree, file_type_data = build_storage_analysis(directory_path, chart_title)
    total_size = int(storage_tree.get("size", 0))
    file_type_rows = [
        {"name": extension, "size": int(size)}
        for extension, size in sorted(
            file_type_data.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        if size > 0
    ]
    storage_analysis = {
        "tree": storage_tree,
        "fileTypes": file_type_rows,
        "basePath": directory,
        "browserBaseUrl": url_for("index"),
    }
    return render_template(
        "detail.html",
        directory=directory or "Root",
        file_count=storage_tree.get("file_count", 0),
        total_storage=round(total_size / (1024 ** 2), 2),
        storage_analysis=storage_analysis,
    )


@login_required
def serve_chart(filename):
    charts_dir = safe_upload_path("Admin", "charts")
    return send_from_directory(charts_dir, safe_filename(filename))


def register_routes(app):
    app.add_url_rule("/index", "index", index, defaults={"path": ""})
    app.add_url_rule("/index/<path:path>", "index", index)
    app.add_url_rule("/user/<username>", "user_folder", user_folder)
    app.add_url_rule("/upload", "upload_file", upload_file, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/upload/<path:path>", "upload_file", upload_file, methods=["POST"])
    app.add_url_rule("/upload-conflicts", "upload_conflicts", upload_conflicts, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/upload-conflicts/<path:path>", "upload_conflicts", upload_conflicts, methods=["POST"])
    app.add_url_rule("/uploads/<filename>", "uploaded_file", uploaded_file, defaults={"path": ""})
    app.add_url_rule("/uploads/<path:path>/<filename>", "uploaded_file", uploaded_file)
    app.add_url_rule("/download/<filename>", "download_file", download_file, defaults={"path": ""})
    app.add_url_rule("/download/<path:path>/<filename>", "download_file", download_file)
    app.add_url_rule("/download_folder/<path:path>", "download_folder", download_folder, methods=["POST"])
    app.add_url_rule("/download_selected", "download_selected", download_selected, methods=["POST"])
    app.add_url_rule("/toggle_dark_mode", "toggle_dark_mode", toggle_dark_mode, methods=["POST"])
    app.add_url_rule("/delete_selected", "delete_selected", delete_selected, methods=["POST"])
    app.add_url_rule("/delete/<filename>", "delete_file", delete_file, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/delete/<path:path>/<filename>", "delete_file", delete_file, methods=["POST"])
    app.add_url_rule("/delete_folder", "delete_folder", delete_folder, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/delete_folder/<path:path>", "delete_folder", delete_folder, methods=["POST"])
    app.add_url_rule("/edit_file/<filename>", "edit_file", edit_file, defaults={"path": ""})
    app.add_url_rule("/edit_file/<path:path>/<filename>", "edit_file", edit_file)
    app.add_url_rule("/save_file/<filename>", "save_file", save_file, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/save_file/<path:path>/<filename>", "save_file", save_file, methods=["POST"])
    app.add_url_rule("/rename_file/<filename>", "rename_file", rename_file, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/rename_file/<path:path>/<filename>", "rename_file", rename_file, methods=["POST"])
    app.add_url_rule("/rename_folder/<foldername>", "rename_folder", rename_folder, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/rename_folder/<path:path>/<foldername>", "rename_folder", rename_folder, methods=["POST"])
    app.add_url_rule("/create_folder", "create_folder", create_folder, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/create_folder/<path:path>", "create_folder", create_folder, methods=["POST"])
    app.add_url_rule("/create_text_file", "create_text_file", create_text_file, methods=["POST"], defaults={"path": ""})
    app.add_url_rule("/create_text_file/<path:path>", "create_text_file", create_text_file, methods=["POST"])
    app.add_url_rule("/detail/<path:directory>", "detail", detail)
    app.add_url_rule("/charts/<filename>", "serve_chart", serve_chart)
