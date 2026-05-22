import logging
from flask import redirect, render_template, request, session, url_for
from lib.extensions import bcrypt
from lib.users import ensure_user_folder, load_users, save_users


def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template("register.html", error="Please enter a username and password."), 400

        users = load_users()
        if username in users:
            return render_template("register.html", error="Username already exists."), 400

        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")
        users[username] = {"password": hashed_password, "suspended": False}
        save_users(users)
        ensure_user_folder(username)
        logging.info("New user registered: %s", username)
        return redirect(url_for("login"))
    return render_template("register.html")


def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        users = load_users()
        user = users.get(username)
        if user and bcrypt.check_password_hash(user.get("password", ""), password):
            if user.get("suspended"):
                return render_template("login.html", error="This account is suspended."), 403
            session["logged_in"] = True
            session["username"] = username
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
    app.add_url_rule("/register", "register", register, methods=["GET", "POST"])
    app.add_url_rule("/", "login", login, methods=["GET", "POST"])
    app.add_url_rule("/logout", "logout", logout)
