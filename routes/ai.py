import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from flask import jsonify, render_template, request, session, url_for

from lib.activity import log_activity
from lib.ai_client import ask_image, ask_text, build_ai_context, initial_history, is_ai_readable_file
from lib.metadata import move_metadata
from lib.security import login_required
from lib.storage import (
    file_kind,
    format_bytes,
    is_allowed_for_user,
    normalize_relative_path,
    safe_path_part,
    safe_upload_path,
    upload_root,
    visible_path_for_user,
)
from lib.versions import move_versions


def _current_user():
    return session.get("username")


def _assistant_workspace() -> dict:
    username = _current_user()
    relative_root = "" if username == "Admin" else normalize_relative_path(username)
    absolute_root = upload_root() if username == "Admin" else safe_upload_path(relative_root)
    context_files = []
    recent_files = []
    file_count = 0
    folder_count = 0
    total_bytes = 0

    if os.path.isdir(absolute_root):
        for root, directories, filenames in os.walk(absolute_root):
            directories[:] = sorted(
                directory
                for directory in directories
                if directory != ".tamestorage_system"
            )
            folder_count += len(directories)
            relative_directory = normalize_relative_path(os.path.relpath(root, absolute_root))
            relative_directory = "" if relative_directory == "." else relative_directory
            for filename in sorted(filenames):
                relative_path = normalize_relative_path(
                    os.path.join(relative_root, relative_directory, filename)
                )
                absolute_path = os.path.join(root, filename)
                try:
                    stat_result = os.stat(absolute_path)
                except OSError:
                    continue
                file_count += 1
                total_bytes += stat_result.st_size
                item = {
                    "name": filename,
                    "path": relative_path,
                    "kind": file_kind(filename, False),
                    "size_label": format_bytes(stat_result.st_size),
                    "modified_timestamp": stat_result.st_mtime,
                }
                recent_files.append(item)
                if is_ai_readable_file(relative_path) and len(context_files) < 500:
                    context_files.append(item)

    context_files.sort(key=lambda item: item["path"].casefold())
    recent_files.sort(key=lambda item: item["modified_timestamp"], reverse=True)
    return {
        "file_count": file_count,
        "folder_count": folder_count,
        "total_size_label": format_bytes(total_bytes),
        "context_files": context_files,
        "recent_files": recent_files[:4],
    }


@login_required
def chat_page():
    selected_context_path = normalize_relative_path(request.args.get("file", ""))
    if (
        not selected_context_path
        or not _can_access(selected_context_path)
        or not is_ai_readable_file(selected_context_path)
        or not os.path.isfile(safe_upload_path(selected_context_path))
    ):
        selected_context_path = ""
    return render_template(
        "chat.html",
        assistant_workspace=_assistant_workspace(),
        selected_context_path=selected_context_path,
    )


def _split_command_arguments(text: str) -> tuple[str, str]:
    value = text.strip()
    for separator in (" -> ", " => ", " to "):
        if separator in value:
            left, right = value.split(separator, 1)
            return left.strip().strip('"\''), right.strip().strip('"\'')
    return value.strip().strip('"\''), ""


def _can_access(path: str) -> bool:
    return bool(path) and is_allowed_for_user(path, _current_user())


def _path_exists(path: str) -> bool:
    return os.path.exists(safe_upload_path(path))


def _navigation_payload(path: str) -> dict:
    normalized = normalize_relative_path(path)
    if not _can_access(normalized):
        return {"handled": True, "response": f"I cannot open `{normalized}` because your account cannot access that path."}

    absolute = safe_upload_path(normalized)
    if os.path.isdir(absolute):
        return {
            "handled": True,
            "response": f"Ready to open folder `{normalized}`.",
            "action_url": url_for("index", path=normalized),
            "action_label": "Open folder",
        }
    if os.path.isfile(absolute):
        return {
            "handled": True,
            "response": f"Ready to preview file `{normalized}`.",
            "action_url": url_for("preview_file", target=normalized),
            "action_label": "Preview file",
        }
    return {"handled": True, "response": f"I could not find `{normalized}`."}


def _inspect_payload(path: str) -> dict:
    normalized = normalize_relative_path(path)
    if not _can_access(normalized):
        return {"handled": True, "response": f"I cannot inspect `{normalized}` because your account cannot access it."}

    absolute = safe_upload_path(normalized)
    if not os.path.exists(absolute):
        return {"handled": True, "response": f"I could not find `{normalized}`."}

    stat_result = os.stat(absolute)
    if os.path.isdir(absolute):
        try:
            children = [entry for entry in os.scandir(absolute) if entry.name != ".tamestorage_system"]
        except OSError:
            children = []
        folders = sum(1 for entry in children if entry.is_dir(follow_symlinks=False))
        files = sum(1 for entry in children if entry.is_file(follow_symlinks=False))
        response = (
            f"**Folder:** `{normalized}`\n"
            f"- Contains {folders} folder{'s' if folders != 1 else ''} and {files} file{'s' if files != 1 else ''}\n"
            f"- Last changed: {datetime.fromtimestamp(stat_result.st_mtime).strftime('%d %B %Y, %H:%M')}"
        )
        action_url = url_for("index", path=normalized)
        action_label = "Open folder"
    else:
        suffix = Path(normalized).suffix.lower() or "No extension"
        content_access = (
            "Its text content can be selected as assistant context."
            if is_ai_readable_file(normalized)
            else "Its contents are not sent to the assistant; I can still use its metadata."
        )
        response = (
            f"**File:** `{normalized}`\n"
            f"- Type: {suffix}\n"
            f"- Size: {format_bytes(stat_result.st_size)}\n"
            f"- {content_access}"
        )
        action_url = url_for("preview_file", target=normalized)
        action_label = "Preview file"

    return {
        "handled": True,
        "response": response,
        "action_url": action_url,
        "action_label": action_label,
    }


def _move_or_copy(source: str, destination_folder: str, operation: str) -> dict:
    source = normalize_relative_path(source)
    destination_folder = normalize_relative_path(visible_path_for_user(destination_folder, _current_user()))

    if not _can_access(source):
        return {"handled": True, "response": f"I cannot access `{source}`."}
    if not is_allowed_for_user(destination_folder, _current_user()) and _current_user() != "Admin":
        return {"handled": True, "response": f"I cannot use `{destination_folder}` as a destination for this account."}

    source_abs = safe_upload_path(source)
    destination_folder_abs = safe_upload_path(destination_folder)
    if not os.path.exists(source_abs):
        return {"handled": True, "response": f"Source not found: `{source}`."}
    if not os.path.isdir(destination_folder_abs):
        return {"handled": True, "response": f"Destination folder not found: `{destination_folder}`."}

    destination = normalize_relative_path(os.path.join(destination_folder, os.path.basename(source)))
    destination_abs = safe_upload_path(destination)
    if os.path.exists(destination_abs):
        return {"handled": True, "response": f"Destination already exists: `{destination}`. Rename one of them first."}
    if operation == "move" and os.path.isdir(source_abs):
        source_prefix = source.rstrip("/") + "/"
        destination_prefix = destination.rstrip("/") + "/"
        if destination_prefix.startswith(source_prefix):
            return {"handled": True, "response": "A folder cannot be moved inside itself."}

    if operation == "copy":
        if os.path.isdir(source_abs):
            shutil.copytree(source_abs, destination_abs)
        else:
            shutil.copy2(source_abs, destination_abs)
        log_activity("ai.file.copy", source, details={"destination": destination})
        verb = "Copied"
    else:
        shutil.move(source_abs, destination_abs)
        move_metadata(source, destination)
        move_versions(source, destination)
        log_activity("ai.file.move", source, details={"destination": destination})
        verb = "Moved"

    return {
        "handled": True,
        "response": f"{verb} `{source}` to `{destination_folder}`.",
        "action_url": url_for("index", path=destination_folder),
        "action_label": "Open destination",
    }


def _rename(source: str, new_name: str) -> dict:
    source = normalize_relative_path(source)
    new_name = safe_path_part(new_name)
    if not new_name:
        return {"handled": True, "response": "Please provide a new name."}
    if not _can_access(source):
        return {"handled": True, "response": f"I cannot access `{source}`."}

    source_abs = safe_upload_path(source)
    if not os.path.exists(source_abs):
        return {"handled": True, "response": f"Source not found: `{source}`."}

    destination = normalize_relative_path(os.path.join(os.path.dirname(source), new_name))
    destination_abs = safe_upload_path(destination)
    if os.path.exists(destination_abs):
        return {"handled": True, "response": f"A file or folder named `{new_name}` already exists there."}

    shutil.move(source_abs, destination_abs)
    move_metadata(source, destination)
    move_versions(source, destination)
    log_activity("ai.file.rename", source, details={"destination": destination})
    return {
        "handled": True,
        "response": f"Renamed `{source}` to `{destination}`.",
        "action_url": url_for("preview_file", target=destination) if os.path.isfile(destination_abs) else url_for("index", path=destination),
        "action_label": "Open renamed item",
    }


def _help_payload() -> dict:
    return {
        "handled": True,
        "response": (
            "I can help with normal questions and these safe file commands:\n\n"
            "`/open path/to/item` opens a folder or previews a file.\n"
            "`/inspect path/to/item` reports verified file or folder details.\n"
            "`/move source/path -> destination/folder` moves an item.\n"
            "`/copy source/path -> destination/folder` copies an item.\n"
            "`/rename source/path -> new-name.ext` renames an item in the same folder.\n\n"
            "Examples:\n"
            "`/open Tames/Docs/main.pdf`\n"
            "`/move Tames/Docs/main.pdf -> Tames/Archive`\n"
            "`/rename Tames/Docs/main.pdf -> rotation-notes.pdf`"
        ),
    }


def handle_file_command(message: str) -> dict | None:
    text = (message or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in {"/help", "/commands", "commands"}:
        return _help_payload()

    patterns = [
        (r"^/(open|go|navigate)\s+(.+)$", "open"),
        (r"^/(inspect|info)\s+(.+)$", "inspect"),
        (r"^(open|go to|navigate to)\s+(.+)$", "open"),
        (r"^/(move)\s+(.+)$", "move"),
        (r"^/(copy)\s+(.+)$", "copy"),
        (r"^/(rename)\s+(.+)$", "rename"),
    ]

    for pattern, command in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        payload = match.group(2).strip()
        if command == "open":
            return _navigation_payload(payload)
        if command == "inspect":
            return _inspect_payload(payload)
        left, right = _split_command_arguments(payload)
        if not left or not right:
            return {"handled": True, "response": f"Command format is incomplete. Try `/help` for examples."}
        if command in {"move", "copy"}:
            return _move_or_copy(left, right, command)
        if command == "rename":
            return _rename(left, right)

    return None


@login_required
def chat():
    history = session.get("conversation_history") or initial_history(session.get("username"))
    msg = request.form.get("msg", "").strip()
    image = request.files.get("image")
    prompt = request.form.get("prompt", "Describe this image.").strip() or "Describe this image."

    if msg and not image:
        command_result = handle_file_command(msg)
        if command_result and command_result.get("handled"):
            return jsonify(command_result)

    include_context = request.form.get("include_context", "1") == "1"
    file_path = request.form.get("file_path", "").strip()
    detail_level = request.form.get("detail_level", "balanced").strip() or "balanced"
    response_style = request.form.get("response_style", "practical").strip() or "practical"
    extra_context = build_ai_context(
        username=session.get("username"),
        include_tree=include_context,
        file_path=file_path,
        detail_level=detail_level,
        response_style=response_style,
    )

    try:
        if image and image.filename:
            response_text, history = ask_image(history, image.read(), prompt, msg, extra_context=extra_context)
        elif msg:
            response_text, history = ask_text(
                history,
                msg,
                username=session.get("username"),
                extra_context=extra_context,
            )
        else:
            return jsonify({"response": "No valid input provided."}), 400
    except RuntimeError as exc:
        return jsonify({"response": str(exc)}), 503

    session["conversation_history"] = history
    return jsonify({"response": response_text})


def register_routes(app):
    app.add_url_rule("/chat", "chat_page", chat_page, methods=["GET"])
    app.add_url_rule("/chat", "chat", chat, methods=["POST"])
