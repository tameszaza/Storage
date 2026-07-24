import io
import os
from collections import deque
from pathlib import Path
from typing import Any

from flask import current_app
from PIL import Image

from lib.storage import (
    TEXT_PREVIEW_EXTENSIONS,
    get_user_file_structure,
    is_allowed_for_user,
    normalize_relative_path,
    safe_upload_path,
)

_client = None
_client_error = None

MAX_CONTEXT_CHARS = 12000
SAFE_TEXT_EXTENSIONS = set(TEXT_PREVIEW_EXTENSIONS) | {".csv", ".tsv", ".tex", ".rst", ".ini", ".toml"}


def read_api_key() -> str | None:
    path = current_app.config["GEMINI_CONFIG_PATH"]
    try:
        with open(path, "r", encoding="utf-8") as file:
            key = file.read().strip()
            return key or None
    except OSError:
        return None


def get_client():
    global _client, _client_error

    if _client is not None:
        return _client
    if _client_error is not None:
        raise RuntimeError(_client_error)

    api_key = read_api_key()
    if not api_key:
        _client_error = "Gemini API key is missing. Put it in uploads/Admin/config.txt or set GEMINI_CONFIG_PATH."
        raise RuntimeError(_client_error)

    try:
        from google import genai

        _client = genai.Client(api_key=api_key)
        return _client
    except ModuleNotFoundError as exc:
        _client_error = "Missing Gemini SDK. Run: pip install -U google-genai"
        raise RuntimeError(_client_error) from exc
    except Exception as exc:
        _client_error = str(exc)
        raise RuntimeError(_client_error) from exc


def get_model():
    return get_client()


def initial_history(username: str | None) -> list[dict]:
    username = username or "Unknown user"
    return [
        {
            "role": "user",
            "parts": f"My name is {username}. I use this file management system and need help managing files.",
        },
        {
            "role": "user",
            "parts": "The current file structure is as follows:\n" + get_user_file_structure(username),
        },
    ]


def _part_text(part: Any) -> str:
    if isinstance(part, dict):
        return str(part.get("text") or part.get("parts") or "")
    return str(part or "")


def _message_text(message: dict) -> str:
    parts = message.get("parts", "")
    if isinstance(parts, str):
        return parts
    if isinstance(parts, list):
        return "\n".join(_part_text(part) for part in parts).strip()
    return str(parts or "")


def _api_history(history: list[dict]) -> list[dict]:
    contents: list[dict] = []

    for item in history:
        text = _message_text(item).strip()
        if not text:
            continue

        role = "model" if item.get("role") == "model" else "user"
        content = {"role": role, "parts": [{"text": text}]}

        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append({"text": text})
        else:
            contents.append(content)

    return contents


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text)
    return "I received a response, but it did not contain text."


def _read_text_file_for_ai(username: str | None, file_path: str) -> str:
    normalized = normalize_relative_path(file_path)
    if not normalized:
        return ""
    if not is_allowed_for_user(normalized, username):
        return "Selected file context was ignored because this user cannot access that path."

    absolute = safe_upload_path(normalized)
    if not os.path.exists(absolute) or not os.path.isfile(absolute):
        return f"Selected file context was requested, but the file was not found: {normalized}"

    suffix = Path(absolute).suffix.lower()
    if suffix not in SAFE_TEXT_EXTENSIONS:
        return f"Selected file context was requested, but this file type is not safe for text reading: {suffix or 'no extension'}"

    try:
        with open(absolute, "r", encoding="utf-8", errors="replace") as file:
            content = file.read(MAX_CONTEXT_CHARS + 1)
    except OSError:
        return "Selected file context was requested, but the file could not be read."

    truncated = len(content) > MAX_CONTEXT_CHARS
    content = content[:MAX_CONTEXT_CHARS]
    suffix_note = "\n[Content truncated for safety.]" if truncated else ""
    return f"Selected file path: {normalized}\nSelected file preview:\n```\n{content}\n```{suffix_note}"


def build_ai_context(
    username: str | None,
    include_tree: bool = True,
    file_path: str = "",
    detail_level: str = "balanced",
    response_style: str = "practical",
) -> str:
    parts = [
        "Assistant behavior preferences:",
        f"- Detail level: {detail_level}",
        f"- Response style: {response_style}",
        "- Be practical and directly useful for a private file storage app.",
        "- When suggesting file actions, explain the safest option first.",
        "- The app supports safe slash commands in chat: /open path, /move source -> folder, /copy source -> folder, and /rename source -> new-name.",
        "- If the user wants you to perform a file action, suggest the exact slash command when you are not already executing one.",
    ]

    if include_tree:
        parts.append("\nCurrent file tree:\n" + get_user_file_structure(username))

    file_context = _read_text_file_for_ai(username, file_path)
    if file_context:
        parts.append("\n" + file_context)

    return "\n".join(parts)


def _with_context(message: str, extra_context: str = "") -> str:
    if not extra_context:
        return message
    return f"{extra_context}\n\nUser request:\n{message}"


def ask_text(history: list[dict], message: str, extra_context: str = "") -> tuple[str, list[dict]]:
    client = get_client()
    updated_history = deque(history, maxlen=1000)
    updated_history.append({"role": "user", "parts": _with_context(message, extra_context)})

    response = client.models.generate_content(
        model=current_app.config["GEMINI_MODEL"],
        contents=_api_history(list(updated_history)),
    )

    text = _response_text(response)
    updated_history.append({"role": "model", "parts": text})
    return text, list(updated_history)


def ask_image(history: list[dict], image_bytes: bytes, prompt: str, label: str = "", extra_context: str = "") -> tuple[str, list[dict]]:
    client = get_client()
    image = Image.open(io.BytesIO(image_bytes))
    image.load()

    recent_context = "\n\n".join(_message_text(item) for item in history[-8:] if _message_text(item).strip())
    prompt_text = prompt or "Describe this image."
    context_parts = []
    if extra_context:
        context_parts.append(extra_context)
    if recent_context:
        context_parts.append("Recent chat context:\n" + recent_context)
    context_parts.append("User image prompt:\n" + prompt_text)
    if label:
        context_parts.append("User text sent with image:\n" + label)
    prompt_text = "\n\n".join(context_parts)

    response = client.models.generate_content(
        model=current_app.config["GEMINI_MODEL"],
        contents=[prompt_text, image],
    )

    text = _response_text(response)
    updated_history = deque(history, maxlen=1000)
    updated_history.append({"role": "user", "parts": "Image sent: " + label})
    updated_history.append({"role": "model", "parts": text})
    return text, list(updated_history)
