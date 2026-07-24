from routes import admin, ai, aircon, auth, features, feedback, files, network_test, public, shares, voice


def register_all_routes(app, sock=None):
    auth.register_routes(app)
    files.register_routes(app)
    features.register_routes(app)
    admin.register_routes(app)
    network_test.register_routes(app)
    voice.register_routes(app, sock=sock)
    aircon.register_routes(app)
    feedback.register_routes(app)
    shares.register_routes(app)
    ai.register_routes(app)
    public.register_routes(app)
