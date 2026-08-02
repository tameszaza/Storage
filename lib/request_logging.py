from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import current_app, g, request, session

_HANDLER_NAME = "tamestorage-server-file"


def _configured_log_path(app) -> Path:
    path = Path(str(app.config["SERVER_LOG_FILE"])).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve(strict=False)


def setup_logging(app) -> None:
    """Attach a durable file handler even when another library configured logging first."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    app.logger.setLevel(logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.INFO)

    log_path = _configured_log_path(app)
    existing_handler = next(
        (
            handler
            for handler in root_logger.handlers
            if getattr(handler, "name", "") == _HANDLER_NAME
            and Path(getattr(handler, "baseFilename", "")).resolve(strict=False) == log_path
        ),
        None,
    )
    if existing_handler is not None:
        return

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=int(app.config.get("SERVER_LOG_MAX_BYTES", 10 * 1024 * 1024)),
            backupCount=int(app.config.get("SERVER_LOG_BACKUP_COUNT", 5)),
            encoding="utf-8",
            delay=True,
        )
    except OSError as exc:
        app.logger.error("Could not initialize server log file %s: %s", log_path.name, exc)
        return

    handler.name = _HANDLER_NAME
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s")
    )
    root_logger.addHandler(handler)
    app.logger.info("Server logging initialized file=%s", log_path.name)


def _safe_token(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return "_".join(text.split())[:120]


def _request_context() -> dict[str, object]:
    started_at = getattr(g, "request_started_at", None)
    elapsed_ms = (time.perf_counter() - started_at) * 1000 if isinstance(started_at, float) else 0.0
    return {
        "request_id": _safe_token(getattr(g, "request_audit_id", ""), "unknown"),
        "user": _safe_token(session.get("username"), "anonymous"),
        "ip": _safe_token(request.remote_addr, "unknown"),
        "method": _safe_token(request.method, "UNKNOWN"),
        "path": _safe_token(request.path, "/"),
        "elapsed_ms": elapsed_ms,
    }


def register_request_logging(app) -> None:
    @app.before_request
    def log_request() -> None:
        g.request_started_at = time.perf_counter()
        g.request_audit_id = secrets.token_hex(6)
        try:
            with open(current_app.config["DATA_TRANSFER_LOG"], "a", encoding="utf-8") as file:
                data_size = request.content_length or 0
                file.write(
                    f"Incoming Request - Path: {request.path}, Method: {request.method}, "
                    f"Data Size: {data_size} bytes\n"
                )
        except OSError:
            current_app.logger.error("Could not write request transfer log", exc_info=True)

    @app.after_request
    def log_response(response):
        context = _request_context()
        response.headers.setdefault("X-Request-ID", str(context["request_id"]))

        try:
            if not response.direct_passthrough:
                data_size = len(response.get_data())
                with open(current_app.config["DATA_TRANSFER_LOG"], "a", encoding="utf-8") as file:
                    file.write(
                        f"{datetime.now()}: {request.remote_addr} - {data_size} bytes transferred.\n"
                    )
        except Exception:
            current_app.logger.error("Could not write response transfer log", exc_info=True)

        if response.status_code >= 400:
            log_level = logging.ERROR if response.status_code >= 500 else logging.WARNING
            current_app.logger.log(
                log_level,
                "HTTP response request_id=%s user=%s ip=%s method=%s path=%s status=%s duration_ms=%.1f",
                context["request_id"],
                context["user"],
                context["ip"],
                context["method"],
                context["path"],
                response.status_code,
                context["elapsed_ms"],
            )
        return response

    @app.teardown_request
    def log_request_exception(error: BaseException | None) -> None:
        if error is None:
            return
        context = _request_context()
        current_app.logger.error(
            "Unhandled request exception request_id=%s user=%s ip=%s method=%s path=%s",
            context["request_id"],
            context["user"],
            context["ip"],
            context["method"],
            context["path"],
            exc_info=(type(error), error, error.__traceback__),
        )
