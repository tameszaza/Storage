from __future__ import annotations

import base64
import binascii
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class InworldTtsError(RuntimeError):
    """Raised when Inworld cannot synthesize speech."""


def _http_error_message(error: HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", "replace")
        detail = json.loads(body)
        if isinstance(detail, dict):
            for key in ("message", "error", "detail"):
                message = detail.get(key)
                if isinstance(message, str) and message.strip():
                    return message.strip()[:800]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        pass
    return f"Inworld TTS returned HTTP {error.code}."


def synthesize_speech(
    text: str,
    *,
    api_key: str,
    endpoint: str,
    voice_id: str,
    model_id: str,
    speaking_rate: float,
    delivery_mode: str,
    language: str,
    timeout_seconds: int,
) -> bytes:
    api_key = api_key.strip()
    if not api_key:
        raise InworldTtsError("Inworld TTS is not configured. Set INWORLD_API_KEY in the server environment.")

    payload = {
        "text": text,
        "voiceId": voice_id,
        "modelId": model_id,
        "audioConfig": {"speakingRate": speaking_rate},
        "deliveryMode": delivery_mode,
        "language": language,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Tamestorage-Remote-Voice/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise InworldTtsError(_http_error_message(error)) from error
    except URLError as error:
        raise InworldTtsError(f"Could not reach Inworld TTS: {error.reason}") from error
    except TimeoutError as error:
        raise InworldTtsError("Inworld TTS timed out.") from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InworldTtsError("Inworld TTS returned an invalid response.") from error

    encoded_audio = body.get("audioContent")
    if not isinstance(encoded_audio, str) or not encoded_audio:
        raise InworldTtsError("Inworld TTS returned no audio.")
    try:
        audio = base64.b64decode(encoded_audio, validate=True)
    except (binascii.Error, ValueError) as error:
        raise InworldTtsError("Inworld TTS returned invalid audio data.") from error
    if not audio:
        raise InworldTtsError("Inworld TTS returned empty audio.")
    return audio
