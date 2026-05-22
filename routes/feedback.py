from flask import render_template, request, session
from lib.feedback_store import add_feedback


def feedback():
    feedback_submitted = False
    if request.method == "POST":
        add_feedback(
            username=session.get("username", "Anonymous"),
            feedback_type=request.form.get("feedback_type", "General"),
            message=request.form.get("message", ""),
            rating=request.form.get("rating"),
        )
        feedback_submitted = True
    return render_template("feedback.html", feedback_submitted=feedback_submitted)


def register_routes(app):
    app.add_url_rule("/feedback", "feedback", feedback, methods=["GET", "POST"])
