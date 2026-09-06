from routes import admin, ai, auth, features, files, music, network_test, planner, public, shares
from routes import videos


def register_all_routes(app):
    videos.register_routes(app)
    auth.register_routes(app)
    files.register_routes(app)
    features.register_routes(app)
    admin.register_routes(app)
    music.register_routes(app)
    network_test.register_routes(app)
    planner.register_routes(app)
    shares.register_routes(app)
    ai.register_routes(app)
    public.register_routes(app)
