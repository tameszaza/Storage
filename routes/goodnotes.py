"""Admin handoff: confirmed absence only, with an explicit existing-folder workflow."""
from datetime import datetime, timezone
from collections import defaultdict
from flask import Response, abort, current_app, flash, redirect, render_template, request, session, url_for
from lib.goodnotes_download import stream_import_zip
from lib.goodnotes_status import ImportStatusUnavailable, import_warning
from lib.security import login_required


def pending():
    if session.get('username')!='Admin':abort(403)
    try:return import_warning(current_app.config['UPLOAD_FOLDER'],strict=True)
    except ImportStatusUnavailable:abort(503,description='Goodnotes import list unavailable. Try again shortly.')


@login_required
def goodnotes_import_review():
    warning=pending();folders=defaultdict(list)
    for item in (warning or {}).get('files',[]):
        if item['confidence']=='missing':folders[item['folder']].append(item)
    return render_template('goodnotes_import.html',warning=warning,folders=dict(sorted(folders.items())))


@login_required
def download_goodnotes_for_ipad():
    warning=pending()
    if request.args.get('existing_folders')!='yes':return redirect(url_for('goodnotes_import_review'))
    files=[item for item in (warning or {}).get('files',[]) if item['confidence']=='missing']
    if not files:
        flash('No confirmed missing PDFs. Unconfirmed files are excluded to avoid duplicate imports.','info')
        return redirect(url_for('goodnotes_import_review'))
    stamp=datetime.now(timezone.utc).strftime('%Y-%m-%d')
    response=Response(stream_import_zip(current_app.config['UPLOAD_FOLDER'],files),mimetype='application/zip',direct_passthrough=True)
    response.headers['Content-Disposition']=f'attachment; filename="Goodnotes-missing-PDFs-{stamp}.zip"'
    response.headers['Cache-Control']='private, no-store';response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Accel-Buffering']='no'
    return response


def register_routes(app):
    app.add_url_rule('/goodnotes/import','goodnotes_import_review',goodnotes_import_review)
    app.add_url_rule('/goodnotes/download-for-ipad','download_goodnotes_for_ipad',download_goodnotes_for_ipad)
