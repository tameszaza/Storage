from flask import render_template


def introduction():
    return render_template("introduction.html")


def page_not_found(error):
    return render_template("404.html"), 404


def register_routes(app):
    app.add_url_rule("/introduction", "introduction", introduction)
    app.register_error_handler(404, page_not_found)
