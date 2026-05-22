import io
from collections import deque
from typing import Any

from flask import current_app
from PIL import Image

from lib.storage import get_user_file_structure

_client = None
_client_error = None


def read_api_key() -> str | None:
    path = current_app.config["GEMINI_CONFIG_PATH"]
    try:
        with open(path, "r", encoding="utf-8") as file:
            key = file.read().strip()
            return key or None
    except OSError:
        return None


def get_client():
    """Create one Google GenAI SDK client lazily.

    The previous implementation used google.generativeai, which is now the
    legacy Gemini SDK. This uses the newer google-genai package instead.
    """
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
    """Backward-compatible helper for older route code.

    The new SDK does not create a GenerativeModel object. It exposes model calls
    from the client, so this function returns the client.
    """
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
    """Convert saved Flask-session history into google-genai content dicts.

    Consecutive messages from the same role are merged because Gemini expects a
    cleaner alternating conversation shape. This also keeps the session history
    readable while making API calls more stable.
    """
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


def ask_text(history: list[dict], message: str) -> tuple[str, list[dict]]:
    client = get_client()
    updated_history = deque(history, maxlen=1000)
    updated_history.append({"role": "user", "parts": message})

    response = client.models.generate_content(
        model=current_app.config["GEMINI_MODEL"],
        contents=_api_history(list(updated_history)),
    )

    text = _response_text(response)
    updated_history.append({"role": "model", "parts": text})
    return text, list(updated_history)


def ask_image(history: list[dict], image_bytes: bytes, prompt: str, label: str = "") -> tuple[str, list[dict]]:
    client = get_client()
    image = Image.open(io.BytesIO(image_bytes))
    image.load()

    recent_context = "\n\n".join(_message_text(item) for item in history[-8:] if _message_text(item).strip())
    prompt_text = prompt or "Describe this image."
    if recent_context:
        prompt_text = f"Context from this file manager session:\n{recent_context}\n\nUser image prompt:\n{prompt_text}"

    response = client.models.generate_content(
        model=current_app.config["GEMINI_MODEL"],
        contents=[prompt_text, image],
    )

    text = _response_text(response)
    updated_history = deque(history, maxlen=1000)
    updated_history.append({"role": "user", "parts": "Image sent: " + label})
    updated_history.append({"role": "model", "parts": text})
    return text, list(updated_history)
