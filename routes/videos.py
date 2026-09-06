import secrets
from flask import abort, jsonify, render_template, request, session, send_from_directory
from lib.security import admin_required
from lib import video_downloads as videos

def register_routes(app):
    @app.get('/videos')
    @admin_required
    def videos_page():
        session.setdefault('video_csrf', secrets.token_urlsafe(32))
        return render_template('videos.html', video_csrf=session['video_csrf'])

    @app.get('/videos/status')
    @admin_required
    def videos_status():
        result = [{k:v for k,v in row.items() if k != 'url'} for row in videos.items()]
        return jsonify(items=result)

    @app.post('/videos/action')
    @admin_required
    def videos_action():
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token',''), session.get('video_csrf','invalid')):
            abort(403)
        data = request.get_json() or {}
        try:
            if data.get('action') == 'add':
                title = str(data.get('title','')).strip()[:120]
                url = str(data.get('url','')).strip()
                if not title or len(url) > 4096:
                    raise ValueError('Enter a title and a valid video link.')
                videos.enqueue(title, url)
            elif data.get('action') == 'delete':
                videos.remove(str(data.get('id','')))
            elif data.get('action') == 'retry':
                with videos.connection() as db:
                    db.execute("UPDATE downloads SET status='queued', message='', received=0 WHERE id=? AND status='failed'", (data.get('id'),))
            else:
                abort(400)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(ok=True)

    @app.get('/videos/<key>/file')
    @admin_required
    def videos_file(key):
        row = next((r for r in videos.items() if r['id'] == key and r['status']=='ready'), None)
        if not row:
            abort(404)
        return send_from_directory(videos.folder(key), row['filename'], conditional=True, as_attachment=request.args.get('download') == '1')
