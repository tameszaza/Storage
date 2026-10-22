"""Private activity console; existing restricted host broker supplies snapshots."""
from pathlib import Path
from flask import current_app, jsonify, render_template, request
from routes.transcription import admin as protect
from routes.services import host


@protect
def page():
    root=Path(current_app.static_folder)
    version=max(int((root/name).stat().st_mtime) for name in ('css/activity-console.css','js/activity-console.js'))
    return render_template('activity_console.html', activity_version=version)


@protect
def snapshot():
    data=host({'action':'activity-snapshot'}, timeout=12)
    return jsonify(data),200 if data.get('ok') else 503


def register_routes(app):
    app.add_url_rule('/admin/activity','admin_activity',page)
    app.add_url_rule('/admin/activity/snapshot','admin_activity_snapshot',snapshot)
    @app.after_request
    def private(response):
        if (request.endpoint or '').startswith('admin_activity'):
            response.headers['Cache-Control']='private, no-store'
            response.headers['X-Content-Type-Options']='nosniff'
        return response
