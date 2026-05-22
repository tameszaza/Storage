import io
from collections import deque
from flask import current_app
from PIL import Image
from lib.storage import get_user_file_structure

_model = None
_model_error = None


def read_api_key() -> str | None:
    path = current_app.config["GEMINI_CONFIG_PATH"]
    try:
        with open(path, "r", encoding="utf-8") as file:
            key = file.read().strip()
            return key or None
    except OSError:
        return None


def get_model():
    global _model, _model_error
    if _model is not None:
        return _model
    if _model_error is not None:
        raise RuntimeError(_model_error)
    api_key = read_api_key()
    if not api_key:
        _model_error = "Gemini API key is missing. Put it in uploads/Admin/config.txt or set GEMINI_CONFIG_PATH."
        raise RuntimeError(_model_error)
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        _model = genai.GenerativeModel(current_app.config["GEMINI_MODEL"])
        return _model
    except Exception as exc:
        _model_error = str(exc)
        raise RuntimeError(_model_error) from exc


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


def ask_text(history: list[dict], message: str) -> tuple[str, list[dict]]:
    model = get_model()
    updated_history = deque(history, maxlen=1000)
    updated_history.append({"role": "user", "parts": message})
    chat = model.start_chat(history=list(updated_history))
    response = chat.send_message(message)
    updated_history.append({"role": "model", "parts": response.text})
    return response.text, list(updated_history)


def ask_image(history: list[dict], image_bytes: bytes, prompt: str, label: str = "") -> tuple[str, list[dict]]:
    model = get_model()
    image = Image.open(io.BytesIO(image_bytes))
    response = model.generate_content([prompt or "Describe this image.", image])
    updated_history = deque(history, maxlen=1000)
    updated_history.append({"role": "user", "parts": "Image sent: " + label})
    updated_history.append({"role": "model", "parts": response.text})
    return response.text, list(updated_history)
