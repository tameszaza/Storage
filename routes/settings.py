"""Private settings page and validated calls to the existing host broker."""
import secrets
from flask import current_app,jsonify,render_template,request,session
from routes.transcription import admin
from routes.services import host

@admin
def page():
 token=session.setdefault('transcription_csrf',secrets.token_urlsafe(32))
 return render_template('server_settings.html',settings_csrf=token)

@admin
def api():
 if request.method=='GET':result=host({'action':'settings-get'},timeout=25)
 else:
  if request.content_length is None or request.content_length>15000:return jsonify(ok=False,error='Settings request is too large.'),413
  body=request.get_json(silent=True)
  if not isinstance(body,dict):return jsonify(ok=False,error='Invalid settings request.'),400
  action=body.get('action')
  if action not in ['preview','save']:return jsonify(ok=False,error='Choose Preview or Save.'),400
  if not isinstance(body.get('revision'),str) or len(body['revision'])!=64:return jsonify(ok=False,error='Refresh settings before saving.'),400
  result=host({'action':'settings-'+action,'revision':body['revision'],'changes':body.get('changes',{}),'apply':body.get('apply',False),'pending_only':body.get('pending_only',False)},timeout=25)
 return jsonify(result),200 if result.get('ok') else 409


def register_routes(app):
 app.add_url_rule('/admin/settings','server_settings',page)
 app.add_url_rule('/admin/settings/data','server_settings_api',api,methods=['GET','POST'])
 @app.after_request
 def private(response):
  if (request.endpoint or '').startswith('server_settings'):
   response.headers['Cache-Control']='private, no-store';response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin'
  return response
