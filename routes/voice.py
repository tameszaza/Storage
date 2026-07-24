from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from flask import current_app, jsonify, render_template, session

from lib.security import admin_required
from lib.voice_bridge import VoiceBridgeConfig, VoiceBridgeError, VoiceBridgeSession, command_status


def _asset_version() -> int:
    static_root = Path(current_app.static_folder or "static")
    files = (static_root / "css" / "voice.css", static_root / "js" / "voice.js")
    timestamps = [int(path.stat().st_mtime) for path in files if path.is_file()]
    return max(timestamps, default=int(time.time()))


def _bridge_status() -> dict:
    return {
        "playback": command_status(current_app.config.get("VOICE_PLAYBACK_COMMAND", "")),
        "capture": command_status(current_app.config.get("VOICE_CAPTURE_COMMAND", "")),
        "websocket_available": bool(current_app.extensions.get("voice_websocket_available")),
    }


@admin_required
def voice_page():
    return render_template(
        "voice.html",
        bridge_status=_bridge_status(),
        asset_version=_asset_version(),
    )


@admin_required
def voice_status_api():
    return jsonify(ok=True, **_bridge_status())


def voice_socket(websocket):
    if not session.get("logged_in") or session.get("username") != "Admin":
        websocket.send(json.dumps({"type": "error", "message": "Admin login required."}))
        websocket.close()
        return

    lock: threading.Lock = current_app.extensions["voice_call_lock"]
    if not lock.acquire(blocking=False):
        websocket.send(json.dumps({"type": "error", "message": "Another server voice call is active."}))
        websocket.close()
        return

    bridge = VoiceBridgeSession(
        websocket,
        VoiceBridgeConfig(
            playback_command=current_app.config.get("VOICE_PLAYBACK_COMMAND", ""),
            capture_command=current_app.config.get("VOICE_CAPTURE_COMMAND", ""),
        ),
    )
    started = False
    try:
        while True:
            try:
                message = websocket.receive(timeout=2)
            except TimeoutError:
                continue
            if message is None:
                continue
            if isinstance(message, (bytes, bytearray)):
                if started:
                    bridge.write_browser_audio(bytes(message))
                continue

            try:
                payload = json.loads(message)
            except (TypeError, json.JSONDecodeError):
                continue
            message_type = payload.get("type")
            if message_type == "start" and not started:
                send_to_server = bool(payload.get("send_to_server"))
                receive_from_server = bool(payload.get("receive_from_server"))
                if not send_to_server and not receive_from_server:
                    raise VoiceBridgeError("Choose at least one audio direction.")
                bridge.start(send_to_server, receive_from_server)
                started = True
            elif message_type == "stop":
                break
    except (VoiceBridgeError, ConnectionError, OSError) as error:
        try:
            websocket.send(json.dumps({"type": "error", "message": str(error)}))
        except Exception:
            pass
    finally:
        bridge.close()
        lock.release()


def register_routes(app, sock=None):
    app.add_url_rule("/admin/voice", "voice_page", voice_page)
    app.add_url_rule("/admin/voice/status", "voice_status_api", voice_status_api)
    app.extensions.setdefault("voice_call_lock", threading.Lock())
    available = sock is not None
    app.extensions["voice_websocket_available"] = available
    if available:
        sock.route("/admin/voice/ws")(voice_socket)
