import os
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from flask import abort, current_app, g, jsonify, redirect, render_template, request, send_file, session, url_for

from lib.activity import log_activity
from lib.assistant_calendar_drafts import confirm_draft, draft_image_path, load_draft
from lib.assistant_agenda import direct_temporal_answer, calendar_clock
from lib.ai_client import (
    ask_image,
    ask_text,
    build_ai_context,
    calendar_write_requested,
    compact_session_history,
    direct_command_catalog,
    initial_history,
    is_ai_readable_file,
)
from lib.metadata import move_metadata
from lib.search_index import matching_file_paths
from lib.security import login_required
from lib.storage import (
    file_kind,
    format_bytes,
    is_allowed_for_user,
    normalize_relative_path,
    safe_path_part,
    safe_upload_path,
    visible_path_for_user,
)
from lib.versions import move_versions


def _current_user():
    return session.get("username")


def _assistant_workspace() -> dict:
    """Keep the chat page fast; file options are searched on demand."""
    return {"commands": direct_command_catalog()}


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
    if not session.get("assistant_csrf"):
        session["assistant_csrf"] = secrets.token_urlsafe(32)
    return render_template(
        "chat.html",
        assistant_workspace=_assistant_workspace(),
        assistant_csrf=session["assistant_csrf"],
        selected_context_path=selected_context_path,
    )


@login_required
def chat_context_files():
    """Find readable files anywhere in this account's workspace for the picker."""
    query = request.args.get("q", "").strip()[:100]
    if len(query) < 2:
        return jsonify({"items": []})
    base_path = "" if _current_user() == "Admin" else normalize_relative_path(_current_user())
    def allowed(path: str) -> bool:
        return is_ai_readable_file(path) and _can_access(path) and not os.path.islink(safe_upload_path(path))

    matches = matching_file_paths(base_path, query, allowed=allowed)
    return jsonify({"items": matches})


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


def _store_direct_chat_turn(message: str, response: str) -> None:
    history = session.get("conversation_history") or initial_history(session.get("username"))
    history = list(history)[-998:]
    history.append({"role": "user", "parts": message})
    history.append({"role": "model", "parts": response})
    session["conversation_history"] = compact_session_history(history)


@login_required
def chat():
    # Browser requests must originate from this application now that chat can write.
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return jsonify({"response": "Please send this request from the assistant page."}), 403
    history = session.get("conversation_history") or initial_history(session.get("username"))
    msg = request.form.get("msg", "").strip()
    image = request.files.get("image")
    prompt = request.form.get("prompt", "Describe this image.").strip() or "Describe this image."

    if (msg and not (image and image.filename) and calendar_write_requested(msg)
            and re.search(r"\b(?:image|photo|picture|screenshot)\b", msg, re.IGNORECASE)):
        return jsonify({"response": "Attach the schedule image first. No calendar dates were added."})

    include_context = request.form.get("include_context", "1") == "1"

    if msg and not image:
        command_result = handle_file_command(msg) if include_context else None
        if command_result and command_result.get("handled"):
            return jsonify(command_result)

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

    image_bytes = None
    if image and image.filename:
        from routes.obsidian_images import LIMIT
        image_bytes = image.read(LIMIT + 1)
        if len(image_bytes) > LIMIT:
            return jsonify({"response": "Please use an image under 20 MB."}), 413
    csrf = session.get("assistant_csrf", "")
    supplied_csrf = request.form.get("csrf", "")
    if include_context and session.get("username") == "Admin" and csrf and supplied_csrf.isascii() and secrets.compare_digest(csrf, supplied_csrf):
        request_id = request.form.get("request_id", "")
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,128}", request_id):
            request_id = secrets.token_hex(16)
        g.diary_context = dict(request_id=request_id, clock=calendar_clock(), image_bytes=image_bytes)

    try:
        if image and image.filename:
            response_text, history = ask_image(
                history,
                image_bytes,
                prompt,
                msg,
                extra_context=extra_context,
                username=session.get("username"),
                allow_tools=include_context,
            )
        elif msg:
            response_text, history = ask_text(
                history,
                msg,
                username=session.get("username"),
                extra_context=extra_context,
                allow_tools=include_context,
            )
        else:
            return jsonify({"response": "No valid input provided."}), 400
    except RuntimeError as exc:
        actions = getattr(g, "assistant_actions", [])
        return jsonify({"response": "The assistant could not finish its reply. See completed actions below." if actions else str(exc), "actions": actions}), 200 if actions else 503
    except Exception:
        current_app.logger.exception("Assistant request failed")
        actions = getattr(g, "assistant_actions", [])
        return jsonify({"response": "The assistant could not finish its reply. See completed actions below." if actions else "The assistant service is temporarily unavailable. Please try again.", "actions": actions}), 200 if actions else 503

    session["conversation_history"] = compact_session_history(history)
    return jsonify({
        "response": response_text,
        "actions": getattr(g, "assistant_actions", []),
        "action_url": getattr(g, "assistant_review_url", ""),
        "action_label": getattr(g, "assistant_review_label", ""),
    })


@login_required
def calendar_import_review(token):
    username = _current_user()
    draft = load_draft(username, token)
    if draft is None:
        abort(404)
    csrf = session.get("calendar_import_csrf")
    if not csrf:
        csrf = secrets.token_urlsafe(32)
        session["calendar_import_csrf"] = csrf
    error = ""
    rows = draft.get("candidates", [])
    selected = None
    if request.method == "POST" and not draft.get("confirmed"):
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            abort(403)
        if not secrets.compare_digest(request.form.get("csrf", ""), csrf):
            abort(403)
        selected = set(request.form.getlist("selected"))
        submitted = []
        revised = []
        for index, original in enumerate(rows):
            item = {
                "title": request.form.get(f"title_{index}", original.get("title", "")).strip(),
                "date": request.form.get(f"date_{index}", original.get("date", "")).strip(),
                "all_day": request.form.get(f"timing_{index}", "all_day") == "all_day",
                "start_time": request.form.get(f"start_{index}", "").strip(),
                "end_time": request.form.get(f"end_{index}", "").strip(),
                "date_evidence": original.get("date_evidence", ""),
                "time_evidence": original.get("time_evidence", ""),
                "verified": original.get("verified", True),
                "review_reason": original.get("review_reason", ""),
                "conflict_group": original.get("conflict_group", ""),
            }
            revised.append(item)
            if str(index) in selected:
                submitted.append(item)
        rows = revised
        if not submitted:
            error = "Select at least one date to add."
        else:
            try:
                result = confirm_draft(username, token, submitted)
                if result is None:
                    abort(404)
                return redirect(url_for("calendar_import_review", token=token), code=303)
            except ValueError as exc:
                error = str(exc)
    return render_template(
        "calendar_import_review.html", token=token, draft=draft, rows=rows,
        csrf=csrf, error=error, selected=selected,
    )


@login_required
def calendar_import_image(token):
    image = draft_image_path(_current_user(), token)
    if image is None:
        abort(404)
    response = send_file(image, mimetype="image/jpeg", conditional=True)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def register_routes(app):
    app.add_url_rule("/chat", "chat_page", chat_page, methods=["GET"])
    app.add_url_rule("/chat", "chat", chat, methods=["POST"])
    app.add_url_rule("/chat/files", "chat_context_files", chat_context_files, methods=["GET"])
    app.add_url_rule("/chat/calendar-import/<token>", "calendar_import_review", calendar_import_review, methods=["GET", "POST"])
    app.add_url_rule("/chat/calendar-import/<token>/image", "calendar_import_image", calendar_import_image, methods=["GET"])
