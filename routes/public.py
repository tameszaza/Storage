from flask import render_template
from lib.storage import format_bytes, get_total_storage_bytes
from lib.users import count_user_folders


def introduction():
    user_count = count_user_folders()
    total_storage = format_bytes(get_total_storage_bytes())
    return render_template("introduction.html", user_count=user_count, total_storage=total_storage)


def page_not_found(error):
    return render_template("404.html"), 404


def register_routes(app):
    app.add_url_rule("/introduction", "introduction", introduction)
    app.register_error_handler(404, page_not_found)
