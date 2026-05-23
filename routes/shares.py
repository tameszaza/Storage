import os
import shutil
import tempfile
from pathlib import Path

from flask import (
    abort,
    after_this_request,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from lib.security import login_required
from lib.shares import (
    PERMISSION_PRESETS,
    check_share_password,
    create_share,
    delete_share,
    get_share,
    increment_download_count,
    is_password_unlocked,
    load_audit,
    load_shares,
    log_share_event,
    revoke_share,
    share_permissions,
    share_status,
    target_inside_share,
    user_can_open_share,
)
from lib.storage import (
    TEXT_PREVIEW_EXTENSIONS,
    build_file_item,
    file_kind,
    format_bytes,
    list_directory,
    normalize_relative_path,
    safe_filename,
    safe_relative_upload_name,
    safe_upload_path,
)
from lib.users import load_users


def _actor() -> str | None:
    return session.get("username")


def _share_url(token: str) -> str:
    return url_for("shared_view", token=token, _external=True)


def _can_manage_share(share: dict) -> bool:
    return session.get("username") in {share.get("owner"), "Admin"}


def _unlock_tokens() -> list[str]:
    return list(session.get("share_unlocked_tokens") or [])


def _remember_unlocked(token: str) -> None:
    tokens = set(_unlock_tokens())
    tokens.add(token)
    session["share_unlocked_tokens"] = sorted(tokens)


def _load_share_or_404(token: str) -> dict:
    share = get_share(token)
    if not share:
        abort(404)
    return share


def _guard_share(token: str, *, require: str | None = None) -> tuple[dict, dict]:
    share = _load_share_or_404(token)
    active, message = share_status(share)
    if not active:
        return share, {"allowed": False, "message": message, "status": 410}

    username = _actor()
    if not user_can_open_share(share, username):
        return share, {"allowed": False, "message": "You do not have access to this shared item.", "status": 403}

    if not is_password_unlocked(share, username, _unlock_tokens()):
        return share, {"allowed": False, "message": "Password required", "status": 401, "password_required": True}

    permissions = share_permissions(share)
    if require and not permissions.get(require):
        return share, {"allowed": False, "message": "This share does not allow that action.", "status": 403}

    return share, {"allowed": True, "permissions": permissions}


def _render_gate(share: dict, guard: dict):
    status_code = guard.get("status", 403)
    return render_template(
        "shared_gate.html",
        share=share,
        message=guard.get("message"),
        password_required=guard.get("password_required", False),
    ), status_code


def _zip_directory(folder_path: str, download_name: str):
    temp_dir = tempfile.mkdtemp(prefix="tamestorage_share_zip_")
    zip_base = os.path.join(temp_dir, safe_filename(download_name) or "shared-folder")
    zip_path = shutil.make_archive(zip_base, "zip", folder_path)

    @after_this_request
    def cleanup(response):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return response

    return send_file(zip_path, as_attachment=True, mimetype="application/zip", download_name=os.path.basename(zip_path))


@login_required
def shares_dashboard():
    username = session.get("username")
    shares = load_shares()
    users = load_users()
    created_token = request.args.get("created", "")

    owned = []
    received = []
    for share in shares.values():
        active, status_text = share_status(share)
        item = dict(share)
        item["active"] = active
        item["status_text"] = status_text
        item["url"] = _share_url(share["token"])
        item["permission_label"] = PERMISSION_PRESETS.get(share.get("permission"), PERMISSION_PRESETS["download"])["label"]
        if username == "Admin" or share.get("owner") == username:
            owned.append(item)
        elif username in share.get("allowed_users", []):
            received.append(item)

    owned.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    received.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    audit = load_audit()[-80:][::-1]

    return render_template(
        "shares.html",
        owned_shares=owned,
        received_shares=received,
        audit=audit,
        users=users,
        permission_presets=PERMISSION_PRESETS,
        created_token=created_token,
    )


@login_required
def create_share_route():
    username = session.get("username")
    target_path = normalize_relative_path(request.form.get("target_path", ""))
    if not target_path:
        abort(400)

    from lib.storage import is_allowed_for_user
    if not is_allowed_for_user(target_path, username):
        abort(403)

    try:
        share = create_share(
            owner=username,
            path=target_path,
            access_mode=request.form.get("access_mode", "link"),
            permission=request.form.get("permission", "download"),
            allowed_users=request.form.get("allowed_users", ""),
            password=request.form.get("password", ""),
            expires_at=request.form.get("expires_at", ""),
            max_downloads=request.form.get("max_downloads", ""),
            note=request.form.get("note", ""),
        )
    except Exception as exc:
        flash(f"Could not create share: {exc}", "danger")
        return redirect(request.referrer or url_for("index"))

    flash("Share link created. You can copy it from the sharing center.", "success")
    return redirect(url_for("shares_dashboard", created=share["token"]))


@login_required
def revoke_share_route(token: str):
    share = _load_share_or_404(token)
    if not _can_manage_share(share):
        abort(403)
    revoke_share(token, _actor())
    flash("Share link revoked.", "success")
    return redirect(url_for("shares_dashboard"))


@login_required
def delete_share_route(token: str):
    share = _load_share_or_404(token)
    if not _can_manage_share(share):
        abort(403)
    delete_share(token, _actor())
    flash("Share record deleted.", "success")
    return redirect(url_for("shares_dashboard"))


def shared_password(token: str):
    share = _load_share_or_404(token)
    password = request.form.get("password", "")
    if check_share_password(share, password):
        _remember_unlocked(token)
        log_share_event(token, _actor(), "unlocked")
        return redirect(url_for("shared_view", token=token))
    return render_template(
        "shared_gate.html",
        share=share,
        message="Incorrect password.",
        password_required=True,
    ), 401


def shared_view(token: str, subpath: str = ""):
    share, guard = _guard_share(token)
    if not guard.get("allowed"):
        return _render_gate(share, guard)

    try:
        relative_path, target_path = target_inside_share(share, subpath)
    except PermissionError:
        abort(403)

    if not os.path.exists(target_path):
        abort(404)

    permissions = guard["permissions"]
    share_root_path, share_root_abs = target_inside_share(share, "")
    current_subpath = normalize_relative_path(subpath)
    parent_subpath = "/".join(current_subpath.split("/")[:-1]) if current_subpath else ""

    if os.path.isdir(target_path):
        files = list_directory(relative_path)
        is_file = False
        item = None
    else:
        files = []
        is_file = True
        item = build_file_item(os.path.dirname(target_path), os.path.basename(target_path), "")

    log_share_event(token, _actor(), "viewed", current_subpath or share.get("path", ""))
    return render_template(
        "shared_view.html",
        share=share,
        permissions=permissions,
        share_url=_share_url(token),
        files=files,
        is_file=is_file,
        item=item,
        subpath=current_subpath,
        parent_subpath=parent_subpath,
        format_bytes=format_bytes,
        file_kind=file_kind,
    )


def shared_raw(token: str, item_path: str = ""):
    share, guard = _guard_share(token)
    if not guard.get("allowed"):
        return _render_gate(share, guard)
    try:
        _, target_path = target_inside_share(share, item_path)
    except PermissionError:
        abort(403)
    if not os.path.isfile(target_path):
        abort(404)
    log_share_event(token, _actor(), "previewed", item_path)
    return send_file(target_path, as_attachment=False, download_name=os.path.basename(target_path))


def shared_download(token: str, item_path: str = ""):
    share, guard = _guard_share(token, require="can_download")
    if not guard.get("allowed"):
        return _render_gate(share, guard)
    try:
        _, target_path = target_inside_share(share, item_path)
    except PermissionError:
        abort(403)
    if not os.path.exists(target_path):
        abort(404)

    increment_download_count(token, _actor(), item_path or share.get("path", ""))
    if os.path.isdir(target_path):
        return _zip_directory(target_path, os.path.basename(target_path) or share.get("name", "shared-folder"))
    return send_file(target_path, as_attachment=True, download_name=os.path.basename(target_path))


def shared_upload(token: str, subpath: str = ""):
    share, guard = _guard_share(token, require="can_upload")
    if not guard.get("allowed"):
        return _render_gate(share, guard)
    try:
        _, target_path = target_inside_share(share, subpath)
    except PermissionError:
        abort(403)
    if not os.path.isdir(target_path):
        abort(400)

    files = request.files.getlist("file")
    for uploaded in files:
        if not uploaded or not uploaded.filename:
            continue
        relative_name = safe_relative_upload_name(uploaded.filename)
        destination = os.path.abspath(os.path.join(target_path, relative_name))
        if os.path.commonpath([target_path, destination]) != target_path:
            abort(403)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        uploaded.save(destination)
        log_share_event(token, _actor(), "uploaded", normalize_relative_path(f"{subpath}/{relative_name}"))
    flash("Upload completed.", "success")
    if subpath:
        return redirect(url_for("shared_view_path", token=token, subpath=subpath))
    return redirect(url_for("shared_view", token=token))


def shared_edit(token: str, item_path: str = ""):
    share, guard = _guard_share(token, require="can_edit")
    if not guard.get("allowed"):
        return _render_gate(share, guard)
    try:
        _, target_path = target_inside_share(share, item_path)
    except PermissionError:
        abort(403)
    if not os.path.isfile(target_path):
        abort(404)
    if Path(target_path).suffix.lower() not in TEXT_PREVIEW_EXTENSIONS:
        flash("Only text files can be edited in the browser.", "warning")
        return redirect(url_for("shared_view", token=token))

    if request.method == "POST":
        with open(target_path, "w", encoding="utf-8") as file:
            file.write(request.form.get("file_content", ""))
        log_share_event(token, _actor(), "edited", item_path)
        flash("Shared text file saved.", "success")
        parent = os.path.dirname(normalize_relative_path(item_path))
        if share.get("is_dir") and parent:
            return redirect(url_for("shared_view_path", token=token, subpath=parent))
        return redirect(url_for("shared_view", token=token))

    with open(target_path, "r", encoding="utf-8", errors="replace") as file:
        content = file.read()
    return render_template("shared_edit.html", share=share, item_path=item_path, content=content)


def shared_delete(token: str, item_path: str):
    share, guard = _guard_share(token, require="can_delete")
    if not guard.get("allowed"):
        return _render_gate(share, guard)
    try:
        _, target_path = target_inside_share(share, item_path)
    except PermissionError:
        abort(403)
    if os.path.abspath(target_path) == os.path.abspath(safe_upload_path(share.get("path", ""))):
        abort(400)
    if os.path.isdir(target_path):
        shutil.rmtree(target_path)
    elif os.path.isfile(target_path):
        os.remove(target_path)
    log_share_event(token, _actor(), "deleted_item", item_path)
    flash("Shared item deleted.", "success")
    parent = os.path.dirname(normalize_relative_path(item_path))
    return redirect(url_for("shared_view_path", token=token, subpath=parent) if parent else url_for("shared_view", token=token))


def register_routes(app):
    app.add_url_rule("/shares", "shares_dashboard", shares_dashboard)
    app.add_url_rule("/shares/create", "create_share_route", create_share_route, methods=["POST"])
    app.add_url_rule("/shares/<token>/revoke", "revoke_share_route", revoke_share_route, methods=["POST"])
    app.add_url_rule("/shares/<token>/delete", "delete_share_route", delete_share_route, methods=["POST"])

    app.add_url_rule("/s/<token>", "shared_view", shared_view)
    app.add_url_rule("/s/<token>/browse/<path:subpath>", "shared_view_path", shared_view)
    app.add_url_rule("/s/<token>/password", "shared_password", shared_password, methods=["POST"])
    app.add_url_rule("/s/<token>/raw", "shared_raw_root", shared_raw)
    app.add_url_rule("/s/<token>/raw/<path:item_path>", "shared_raw", shared_raw)
    app.add_url_rule("/s/<token>/download", "shared_download_root", shared_download)
    app.add_url_rule("/s/<token>/download/<path:item_path>", "shared_download", shared_download)
    app.add_url_rule("/s/<token>/upload", "shared_upload", shared_upload, methods=["POST"])
    app.add_url_rule("/s/<token>/upload/<path:subpath>", "shared_upload_path", shared_upload, methods=["POST"])
    app.add_url_rule("/s/<token>/edit", "shared_edit_root", shared_edit, methods=["GET", "POST"])
    app.add_url_rule("/s/<token>/edit/<path:item_path>", "shared_edit", shared_edit, methods=["GET", "POST"])
    app.add_url_rule("/s/<token>/delete/<path:item_path>", "shared_delete", shared_delete, methods=["POST"])
