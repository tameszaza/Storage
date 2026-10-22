import os

from flask import Flask, request, session

from lib.config import Config
from lib.goodnotes_status import import_warning
from lib.extensions import bcrypt
from lib.request_logging import register_request_logging, setup_logging
from lib.search_index import invalidate_search_cache
from lib.shell_usage import approximate_usage
from lib.storage import format_bytes, safe_upload_path
from lib.users import load_users
from routes import register_all_routes


_FILE_MUTATION_ENDPOINTS = frozenset({
    "upload_file", "delete_selected", "delete_file", "delete_folder",
    "save_file", "rename_file", "rename_folder", "create_folder",
    "create_text_file", "move_copy_item", "restore_trash_item",
    "restore_version_route", "shared_upload", "shared_upload_path",
    "shared_edit", "shared_edit_root", "shared_delete",
    "public_file_request", "remove_user", "clear_charts", "videos_action",
    "transcription_complete",
    "chat",  # The assistant can move, copy, and rename files.
})


def should_invalidate_file_inventory(endpoint: str | None, method: str, status_code: int) -> bool:
    """Avoid a full inventory refresh for unrelated form submissions."""
    return method in {"POST", "PUT", "PATCH", "DELETE"} and status_code < 400 and endpoint in _FILE_MUTATION_ENDPOINTS


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    setup_logging(app)
    bcrypt.init_app(app)
    register_request_logging(app)
    register_all_routes(app)

    @app.after_request
    def refresh_search_after_writes(response):
        if should_invalidate_file_inventory(request.endpoint, request.method, response.status_code):
            invalidate_search_cache()
        return response

    @app.context_processor
    def inject_goodnotes_context():
        if session.get('username')!='Admin' or request.endpoint not in {'index','admin'}:return {}
        path=(request.view_args or {}).get('path','')
        if request.endpoint=='index' and not (path=='Goodnotes' or path.startswith('Goodnotes/')):return {}
        return {'goodnotes_import_warning':import_warning(app.config['UPLOAD_FOLDER'])}

    @app.context_processor
    def inject_storage_shell_context():
        """Provide lightweight account and storage details to the shared app shell."""
        if not session.get("logged_in"):
            return {}

        username = session.get("username", "")
        users = load_users()
        if username == "Admin":
            used_bytes = approximate_usage(safe_upload_path(""), app.config["SHELL_USAGE_CACHE_DIR"])
            quota_bytes = 0
        else:
            user_root = safe_upload_path(username)
            used_bytes = approximate_usage(user_root, app.config["SHELL_USAGE_CACHE_DIR"])
            quota_bytes = int(
                users.get(username, {}).get("quota_bytes")
                or app.config.get("DEFAULT_USER_QUOTA_BYTES", 0)
                or 0
            )

        percent = min(100, round((used_bytes / quota_bytes) * 100, 1)) if quota_bytes and used_bytes is not None else 0
        initials = "".join(part[:1] for part in username.split() if part)[:2].upper() or "TS"
        return {
            "shell_username": username,
            "shell_initials": initials,
            "shell_storage_used": format_bytes(used_bytes) if used_bytes is not None else "Calculating…",
            "shell_storage_quota": format_bytes(quota_bytes) if quota_bytes else "Unlimited",
            "shell_storage_percent": percent,
        }

    return app


app = create_app()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "1").strip().lower() in {"1", "true", "yes", "on"}
    certificate = app.config.get("TLS_CERT_FILE")
    private_key = app.config.get("TLS_KEY_FILE")
    ssl_context = (certificate, private_key) if certificate and private_key else None

    app.run(debug=debug, host="0.0.0.0", port=5000, ssl_context=ssl_context)
