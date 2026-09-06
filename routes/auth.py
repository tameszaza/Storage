import logging

from flask import redirect, render_template, request, session, url_for

from lib.extensions import bcrypt
from lib.users import load_users


def login():
    if request.method == "GET" and session.get("logged_in"):
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = load_users()
        user = users.get(username)
        if user and bcrypt.check_password_hash(user.get("password", ""), password):
            if user.get("suspended"):
                return render_template("login.html", error="This account is suspended."), 403
            session.clear()
            session["logged_in"] = True
            session["username"] = username
            session.permanent = True
            logging.info("User %s logged in.", username)
            if username == "Admin":
                return redirect(url_for("index"))
            return redirect(url_for("user_folder", username=username))

        logging.warning("Failed login attempt for username: %s", username)
        return render_template("login.html", error="Invalid username or password."), 401
    return render_template("login.html")


def logout():
    logging.info("User %s logged out.", session.get("username"))
    session.clear()
    return redirect(url_for("login"))


def register_routes(app):
    app.add_url_rule("/", "login", login, methods=["GET", "POST"])
    app.add_url_rule("/logout", "logout", logout)
