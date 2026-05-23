import logging
import os
import shutil
import tempfile
from flask import abort, after_this_request, current_app, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for
from lib.charts import analyze_directory_space, generate_pie_chart
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
def upload_file(path=""):
    path = visible_path_for_user(path, session.get("username"))
    files = request.files.getlist("file")
    if not files or not any(file.filename for file in files):
        return redirect(url_for("index", path=path))

    current_path = safe_upload_path(path)
    os.makedirs(current_path, exist_ok=True)

    username = session.get("username")
    if username != "Admin":
        users = load_users()
        quota = int(users.get(username, {}).get("quota_bytes") or current_app.config.get("DEFAULT_USER_QUOTA_BYTES", 0) or 0)
        if quota:
            incoming = 0
            for uploaded in files:
                try:
                    position = uploaded.stream.tell()
                    uploaded.stream.seek(0, os.SEEK_END)
                    incoming += uploaded.stream.tell()
                    uploaded.stream.seek(position)
                except Exception:
                    pass
            used = get_folder_size(safe_upload_path(username))
            if used + incoming > quota:
                flash("Upload blocked because it would exceed your storage quota.", "warning")
                return redirect(url_for("index", path=path))

    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue
        relative_name = safe_relative_upload_name(uploaded.filename)
        relative_target = normalize_relative_path(os.path.join(path, relative_name))
        filepath = safe_upload_path(relative_target)
        if os.path.exists(filepath) and os.path.isfile(filepath):
            create_version(relative_target, "before upload replace")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        uploaded.save(filepath)
        logging.info("File uploaded: %s", filepath)
        log_activity("file.upload", relative_target)
    return redirect(url_for("index", path=path))


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
        with open(file_path, "r", encoding="utf-8", errors="replace") as file:
            content = file.read()
    except OSError:
        abort(404)
    return render_template("editfile.html", filename=filename, content=content, path=path)


@login_required
def save_file(path, filename):
    path = _require_path_access(path)
    filename = safe_filename(filename)
    file_path = safe_upload_path(path, filename)
    create_version(os.path.join(path, filename), "before browser edit")
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(request.form.get("file_content", ""))
    log_activity("file.edit", os.path.join(path, filename))
    flash("File saved successfully.", "success")
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
def detail(directory):
    if directory == "Root":
        if session.get("username") != "Admin":
            abort(403)
        directory = ""
    directory = _require_path_access(directory)
    directory_path = safe_upload_path(directory)
    if not os.path.isdir(directory_path):
        abort(404)

    directory_data, file_type_data = analyze_directory_space(directory_path)
    file_count = sum(len(files) for _, _, files in os.walk(directory_path))
    total_storage = get_folder_size(directory_path) / (1024 ** 2)
    chart_path, chart_info = generate_pie_chart(directory_data)
    file_type_chart_path, file_type_chart_info = generate_pie_chart(file_type_data, is_file_type=True)
    return render_template(
        "detail.html",
        directory=directory or "Root",
        chart_filename=os.path.basename(chart_path),
        file_type_chart_filename=os.path.basename(file_type_chart_path),
        file_count=file_count,
        total_storage=round(total_storage, 2),
        chart_info=chart_info,
        file_type_chart_info=file_type_chart_info,
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
    app.add_url_rule("/detail/<path:directory>", "detail", detail)
    app.add_url_rule("/charts/<filename>", "serve_chart", serve_chart)
