from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from lib.phone_commands import phone_command_queue


class CameraMacroError(RuntimeError):
    """Raised when the MacroDroid camera webhook cannot be called."""


def trigger_open_camera(url: str, timeout_seconds: int = 5) -> str:
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "text/plain, application/json, */*",
            "User-Agent": "Tamestorage-Camera-Control/1.0",
        },
    )

    def send_request() -> str:
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200))
                if status_code >= 400:
                    raise CameraMacroError(f"MacroDroid open-camera trigger returned HTTP {status_code}.")
                return f"MacroDroid open-camera trigger returned HTTP {status_code}."
        except HTTPError as error:
            raise CameraMacroError(f"MacroDroid open-camera trigger returned HTTP {error.code}.") from error
        except URLError as error:
            raise CameraMacroError(f"Could not reach the MacroDroid open-camera trigger: {error.reason}") from error
        except TimeoutError as error:
            raise CameraMacroError("MacroDroid open-camera trigger timed out.") from error
        except OSError as error:
            raise CameraMacroError(f"Could not send the MacroDroid open-camera trigger: {error}") from error

    return phone_command_queue.execute(send_request, command_type="camera")
