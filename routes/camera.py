from __future__ import annotations

import time
from pathlib import Path

from flask import Response, current_app, jsonify, render_template, request, stream_with_context, url_for

from lib.audio_stream import audio_stream
from lib.camera_macro import CameraMacroError, trigger_open_camera
from lib.camera_stream import camera_stream
from lib.inworld_tts import InworldTtsError, synthesize_speech
from lib.phone_commands import PhoneCommandCancelled, phone_command_queue
from lib.security import admin_required
from lib.tts_stream import tts_stream

_MAX_CAMERA_FRAME_BYTES = 2 * 1024 * 1024
_MAX_AUDIO_CHUNK_BYTES = 256 * 1024


def _camera_asset_version() -> int:
    static_root = Path(current_app.static_folder or "static")
    paths = (static_root / "css" / "camera.css", static_root / "js" / "camera.js")
    timestamps = [int(path.stat().st_mtime) for path in paths if path.is_file()]
    return max(timestamps, default=int(time.time()))


@admin_required
def camera_server():
    return render_template(
        "camera_server.html",
        camera_frame_url=url_for("camera_frame"),
        audio_frame_url=url_for("audio_frame"),
        camera_status_url=url_for("camera_status"),
        camera_control_url=url_for("camera_control"),
        tts_chunk_url=url_for("tts_chunk"),
        # The phone keeps its own acknowledged cursor. Starting from zero here
        # lets a first connection receive clips queued while it was away.
        tts_sequence=0,
        admin_asset_version=_camera_asset_version(),
    )


@admin_required
def camera_frame():
    if request.mimetype != "image/jpeg":
        return jsonify(ok=False, error="Camera frames must be JPEG images."), 415

    if request.content_length and request.content_length > _MAX_CAMERA_FRAME_BYTES:
        return jsonify(ok=False, error="Camera frame is too large."), 413

    data = request.get_data(cache=False)
    if not data or len(data) > _MAX_CAMERA_FRAME_BYTES:
        return jsonify(ok=False, error="Camera frame is empty or too large."), 400
    if not data.startswith(b"\xff\xd8\xff"):
        return jsonify(ok=False, error="Camera frame is not a valid JPEG."), 415

    frame = camera_stream.publish(data)
    return jsonify(ok=True, sequence=frame.sequence), 202


@admin_required
def audio_frame():
    if request.mimetype not in {"audio/pcm", "application/octet-stream"}:
        return jsonify(ok=False, error="Audio chunks must be raw PCM."), 415

    if request.content_length and request.content_length > _MAX_AUDIO_CHUNK_BYTES:
        return jsonify(ok=False, error="Audio chunk is too large."), 413

    try:
        sample_rate = int(request.headers.get("X-Audio-Sample-Rate", "48000"))
        channels = int(request.headers.get("X-Audio-Channels", "1"))
    except ValueError:
        return jsonify(ok=False, error="Invalid audio format."), 400
    if not 8000 <= sample_rate <= 96000 or channels not in {1, 2}:
        return jsonify(ok=False, error="Unsupported audio format."), 400

    data = request.get_data(cache=False)
    if not data or len(data) > _MAX_AUDIO_CHUNK_BYTES or len(data) % 2:
        return jsonify(ok=False, error="Audio chunk is empty, too large, or misaligned."), 400

    chunk = audio_stream.publish(data, sample_rate, channels)
    return jsonify(ok=True, sequence=chunk.sequence), 202


@admin_required
def camera_status():
    status = camera_stream.status()
    status["audio"] = audio_stream.status()
    return jsonify(status)


@admin_required
def camera_control():
    if request.method == "GET":
        try:
            after_sequence = max(0, int(request.args.get("after", 0)))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Invalid control sequence."), 400
        bootstrap = request.args.get("bootstrap", "0") == "1"
        return jsonify(camera_stream.control(after_sequence, bootstrap=bootstrap))

    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower().replace("-", "_")
    if action in {"opencamera", "open_camera"}:
        phone_command_queue.cancel_pending("camera")
        try:
            message = trigger_open_camera(
                current_app.config["CAMERA_OPEN_WEBHOOK_URL"],
                current_app.config["CAMERA_OPEN_WEBHOOK_TIMEOUT_SECONDS"],
            )
        except (CameraMacroError, PhoneCommandCancelled) as error:
            return jsonify(ok=False, error=str(error)), 502
        state = camera_stream.control()
        state["action"] = "open_camera"
        return jsonify(ok=True, message=message, **state)
    if action == "start_camera":
        return jsonify(ok=True, **camera_stream.request_start())
    if action == "stop_camera":
        return jsonify(ok=True, **camera_stream.request_stop())
    if action == "switch_camera":
        return jsonify(ok=True, **camera_stream.request_switch())
    if action == "set_quality":
        try:
            state = camera_stream.set_quality(str(payload.get("preset", "")))
        except ValueError as error:
            return jsonify(ok=False, error=str(error)), 400
        return jsonify(ok=True, **state)
    if action == "set_fps":
        try:
            state = camera_stream.set_fps(str(payload.get("preset", "")))
        except ValueError as error:
            return jsonify(ok=False, error=str(error)), 400
        return jsonify(ok=True, **state)
    if action == "set_audio":
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return jsonify(ok=False, error="Audio enabled must be true or false."), 400
        return jsonify(ok=True, **camera_stream.set_audio(enabled))
    return jsonify(ok=False, error="Unsupported camera control."), 400


@admin_required
def audio_chunk():
    try:
        after_sequence = max(0, int(request.args.get("after", 0)))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid audio sequence."), 400

    chunk = audio_stream.wait_for_chunk(after_sequence)
    if chunk is None:
        return Response(status=204, headers={"Cache-Control": "no-store"})

    response = Response(audio_stream.wav_bytes(chunk), mimetype="audio/wav")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["X-Audio-Sequence"] = str(chunk.sequence)
    response.headers["X-Audio-Sample-Rate"] = str(chunk.sample_rate)
    return response


@admin_required
def tts_speak():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    request_id = str(request.headers.get("X-TTS-Request-Id") or payload.get("request_id") or "").strip()
    max_chars = current_app.config["TTS_MAX_CHARS"]
    if not text:
        return jsonify(ok=False, error="Enter text to speak."), 400
    if len(text) > max_chars:
        return jsonify(ok=False, error=f"Text is limited to {max_chars} characters."), 413
    if len(request_id) > 128:
        return jsonify(ok=False, error="Invalid voice request id."), 400

    if request_id:
        cached_chunk = tts_stream.find_by_request_id(request_id)
        if cached_chunk is not None:
            return jsonify(
                ok=True,
                sequence=cached_chunk.sequence,
                message="Voice was already queued for the phone speaker.",
            )

    try:
        audio = synthesize_speech(
            text,
            api_key=current_app.config["INWORLD_API_KEY"],
            endpoint=current_app.config["TTS_API_URL"],
            voice_id=current_app.config["TTS_VOICE_ID"],
            model_id=current_app.config["TTS_MODEL_ID"],
            speaking_rate=current_app.config["TTS_SPEAKING_RATE"],
            delivery_mode=current_app.config["TTS_DELIVERY_MODE"],
            language=current_app.config["TTS_LANGUAGE"],
            timeout_seconds=current_app.config["TTS_API_TIMEOUT_SECONDS"],
        )
    except InworldTtsError as error:
        return jsonify(ok=False, error=str(error)), 502

    chunk = tts_stream.publish(audio, request_id=request_id or None)
    return jsonify(ok=True, sequence=chunk.sequence, message="Voice queued for the phone speaker.")


@admin_required
def tts_chunk():
    try:
        after_sequence = max(0, int(request.args.get("after", 0)))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid voice sequence."), 400

    chunk = tts_stream.wait_for_chunk(after_sequence)
    if chunk is None:
        latest_sequence = tts_stream.latest_sequence()
        headers = {
            "Cache-Control": "no-store",
            "X-TTS-Latest-Sequence": str(latest_sequence),
        }
        if after_sequence > latest_sequence:
            headers["X-TTS-Sequence-Reset"] = "1"
        return Response(status=204, headers=headers)

    response = Response(chunk.data, mimetype="audio/mpeg")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["X-TTS-Sequence"] = str(chunk.sequence)
    return response


@admin_required
def camera_mjpeg():
    @stream_with_context
    def generate():
        sequence = 0
        while True:
            frame = camera_stream.wait_for_frame(sequence)
            if frame is None:
                # Keep the HTTP connection alive while waiting for the phone.
                yield b"\r\n"
                continue
            sequence = frame.sequence
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame.data)}\r\n".encode("ascii")
                + f"X-Frame-Sequence: {frame.sequence}\r\n\r\n".encode("ascii")
                + frame.data
                + b"\r\n"
            )

    response = Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame", direct_passthrough=True)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def register_routes(app):
    app.add_url_rule("/server", "camera_server", camera_server)
    app.add_url_rule("/server/frame", "camera_frame", camera_frame, methods=["POST"])
    app.add_url_rule("/server/audio/frame", "audio_frame", audio_frame, methods=["POST"])
    app.add_url_rule("/server/status", "camera_status", camera_status)
    app.add_url_rule("/server/control", "camera_control", camera_control, methods=["GET", "POST"])
    app.add_url_rule("/server/stream", "camera_mjpeg", camera_mjpeg)
    app.add_url_rule("/server/audio/chunk", "audio_chunk", audio_chunk)
    app.add_url_rule("/server/tts", "tts_speak", tts_speak, methods=["POST"])
    app.add_url_rule("/server/tts/chunk", "tts_chunk", tts_chunk)
