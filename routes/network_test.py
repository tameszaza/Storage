from __future__ import annotations

import time
from pathlib import Path

from flask import Response, current_app, jsonify, render_template, request

from lib.security import admin_required

MAX_PING_PAYLOAD = 64 * 1024
MAX_TRANSFER_BYTES = 25 * 1024 * 1024


def _asset_version() -> int:
    static_root = Path(current_app.static_folder or "static")
    files = (
        static_root / "css" / "network-test.css",
        static_root / "js" / "network-test.js",
    )
    timestamps = [int(path.stat().st_mtime) for path in files if path.is_file()]
    return max(timestamps, default=int(time.time()))


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@admin_required
def network_test_page():
    return render_template("network_test.html", asset_version=_asset_version())


@admin_required
def network_test_ping():
    size = _bounded_int(request.args.get("size"), 64, 0, MAX_PING_PAYLOAD)
    payload = b"0" * size
    response = Response(payload, mimetype="application/octet-stream")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Server-Time-Ns"] = str(time.time_ns())
    response.headers["X-Packet-Bytes"] = str(size)
    return response


@admin_required
def network_test_download():
    size = _bounded_int(request.args.get("size"), 5 * 1024 * 1024, 1, MAX_TRANSFER_BYTES)
    chunk = b"\0" * (256 * 1024)

    def generate():
        remaining = size
        while remaining > 0:
            amount = min(remaining, len(chunk))
            yield chunk[:amount]
            remaining -= amount

    response = Response(generate(), mimetype="application/octet-stream")
    response.headers["Content-Length"] = str(size)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Transfer-Bytes"] = str(size)
    return response


@admin_required
def network_test_upload():
    content_length = request.content_length or 0
    if content_length > MAX_TRANSFER_BYTES:
        return jsonify(ok=False, error="Upload test payload is too large."), 413

    started = time.perf_counter()
    received = 0
    while True:
        chunk = request.stream.read(256 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > MAX_TRANSFER_BYTES:
            return jsonify(ok=False, error="Upload test payload is too large."), 413
    elapsed_ms = (time.perf_counter() - started) * 1000
    return jsonify(ok=True, received_bytes=received, server_read_ms=round(elapsed_ms, 3))


def register_routes(app):
    app.add_url_rule("/admin/network-test", "network_test_page", network_test_page)
    app.add_url_rule("/admin/network-test/ping", "network_test_ping", network_test_ping)
    app.add_url_rule("/admin/network-test/download", "network_test_download", network_test_download)
    app.add_url_rule("/admin/network-test/upload", "network_test_upload", network_test_upload, methods=["POST"])
