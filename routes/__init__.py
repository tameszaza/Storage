from routes import admin, ai, aircon, auth, features, feedback, files, network_test, planner, public, shares


def register_all_routes(app):
    auth.register_routes(app)
    files.register_routes(app)
    features.register_routes(app)
    admin.register_routes(app)
    network_test.register_routes(app)
    planner.register_routes(app)
    aircon.register_routes(app)
    feedback.register_routes(app)
    shares.register_routes(app)
    ai.register_routes(app)
    public.register_routes(app)
