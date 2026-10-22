"""Admin-only Docker controls via the existing restricted host broker."""
import json
import socket
from flask import current_app,jsonify,request
from routes.transcription import admin as protect


def host(payload,*,timeout=7):
 try:
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
   client.settimeout(timeout);client.connect(current_app.config['TAILSCALE_CONTROL_SOCKET'])
   client.sendall(json.dumps(payload).encode()+b'\n')
   with client.makefile('rb') as f:raw=f.readline(262145)
  if len(raw)>262144:raise ValueError()
  data=json.loads(raw)
  if not isinstance(data,dict):raise ValueError()
  return data
 except (OSError,ValueError):return dict(ok=False,error='Service controls are unavailable. Try Refresh.')


@protect
def containers():
 result=host({'action':'containers-list'})
 return jsonify(result),200 if result.get('ok') else 503


@protect
def detail(name):
 result=host({'action':'container-detail','name':name})
 return jsonify(result),200 if result.get('ok') else 409


@protect
def action(name):
 if request.content_length is None or request.content_length>1024:return jsonify(error='Invalid request.'),400
 body=request.get_json(silent=True)
 if not isinstance(body,dict) or body.get('action') not in {'start','stop','restart'}:return jsonify(error='Choose Start, Stop or Restart.'),400
 result=host({'action':'container-action','name':name,'verb':body['action']})
 return jsonify(result),202 if result.get('ok') else 409


@protect
def operation(ident):
 result=host({'action':'container-operation','id':ident})
 return jsonify(result),200 if result.get('ok') else 409


def register_routes(app):
 app.add_url_rule('/admin/services','admin_services',containers)
 app.add_url_rule('/admin/services/<name>','admin_service_detail',detail)
 app.add_url_rule('/admin/services/<name>/action','admin_service_action',action,methods=['POST'])
 app.add_url_rule('/admin/service-operations/<ident>','admin_service_operation',operation)
 @app.after_request
 def private(response):
  if (request.endpoint or '').startswith(('admin_service','admin_services')):
   response.headers['Cache-Control']='private, no-store';response.headers['X-Content-Type-Options']='nosniff'
  return response
