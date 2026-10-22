import json
import socket


class TailscaleControlError(RuntimeError):
    pass


def request_host_control(socket_path: str, action: str, *, enabled: bool | None = None) -> dict:
    payload = {"action": action}
    if enabled is not None:
        payload["enabled"] = enabled
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(socket_path)
            client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            response = b""
            while not response.endswith(b"\n") and len(response) < 16_384:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response += chunk
    except (OSError, TimeoutError) as exc:
        raise TailscaleControlError("Host network control is unavailable.") from exc
    try:
        result = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TailscaleControlError("Host network control returned an invalid response.") from exc
    if not result.get("ok"):
        raise TailscaleControlError(result.get("error") or "Could not update the exit node.")
    return result
