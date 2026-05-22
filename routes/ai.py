from flask import jsonify, render_template, request, session
from lib.ai_client import ask_image, ask_text, initial_history
from lib.security import login_required


@login_required
def chat_page():
    session.pop("conversation_history", None)
    return render_template("chat.html")


@login_required
def chat():
    history = session.get("conversation_history") or initial_history(session.get("username"))
    msg = request.form.get("msg", "").strip()
    image = request.files.get("image")
    prompt = request.form.get("prompt", "Describe this image.")

    try:
        if image and image.filename:
            response_text, history = ask_image(history, image.read(), prompt, msg)
        elif msg:
            response_text, history = ask_text(history, msg)
        else:
            return jsonify({"response": "No valid input provided."}), 400
    except RuntimeError as exc:
        return jsonify({"response": str(exc)}), 503

    session["conversation_history"] = history
    return jsonify({"response": response_text})


def register_routes(app):
    app.add_url_rule("/chat", "chat_page", chat_page, methods=["GET"])
    app.add_url_rule("/chat", "chat", chat, methods=["POST"])
