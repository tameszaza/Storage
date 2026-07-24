import os

from flask import Flask, session

from lib.config import Config
from lib.extensions import bcrypt
from lib.request_logging import register_request_logging, setup_logging
from lib.storage import format_bytes, get_folder_size, get_total_storage_bytes, safe_upload_path
from lib.users import load_users
from routes import register_all_routes


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    setup_logging(app)
    bcrypt.init_app(app)
    register_request_logging(app)
    register_all_routes(app)

    @app.context_processor
    def inject_storage_shell_context():
        """Provide lightweight account and storage details to the shared app shell."""
        if not session.get("logged_in"):
            return {}

        username = session.get("username", "")
        users = load_users()
        if username == "Admin":
            used_bytes = get_total_storage_bytes()
            quota_bytes = 0
        else:
            user_root = safe_upload_path(username)
            used_bytes = get_folder_size(user_root) if os.path.isdir(user_root) else 0
            quota_bytes = int(
                users.get(username, {}).get("quota_bytes")
                or app.config.get("DEFAULT_USER_QUOTA_BYTES", 0)
                or 0
            )

        percent = min(100, round((used_bytes / quota_bytes) * 100, 1)) if quota_bytes else 0
        initials = "".join(part[:1] for part in username.split() if part)[:2].upper() or "TS"
        return {
            "shell_username": username,
            "shell_initials": initials,
            "shell_storage_used": format_bytes(used_bytes),
            "shell_storage_quota": format_bytes(quota_bytes) if quota_bytes else "Unlimited",
            "shell_storage_percent": percent,
        }

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
