from routes import admin, ai, auth, feedback, files, public


def register_all_routes(app):
    auth.register_routes(app)
    files.register_routes(app)
    admin.register_routes(app)
    feedback.register_routes(app)
    ai.register_routes(app)
    public.register_routes(app)
