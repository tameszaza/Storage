from routes import obsidian_mobile, goodnotes, activity
from routes import admin, ai, auth, features, files, music, network_test, planner, public, shares
from routes import videos, obsidian_images, transcription, services, settings


def register_all_routes(app):
    obsidian_mobile.register_routes(app)
    goodnotes.register_routes(app)
    activity.register_routes(app)
    transcription.register_routes(app)
    services.register_routes(app)
    settings.register_routes(app)
    videos.register_routes(app)
    obsidian_images.register_routes(app)
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
