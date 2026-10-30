"""Admin-only proxy; PDF processing stays in the private statistics service."""
import json,os,secrets,urllib.request,urllib.error
from flask import jsonify,render_template,request,session,url_for
from routes.transcription import admin as protect

@protect
def page():
 session.setdefault('transcription_csrf',secrets.token_urlsafe(32))
 return render_template('goodnotes_stats.html',stats_csrf=session['transcription_csrf'])

@protect
def proxy(part):
 if part not in {'summary','files','file','control'} or (request.method=='POST')!=(part=='control'):return jsonify(error='Not found'),404
 root=os.environ.get('GOODNOTES_STATS_URL','http://goodnotes-stats:8080').rstrip('/')
 query=request.query_string.decode('ascii','strict')
 if len(query)>4096:return jsonify(error='Request too large'),413
 body=None
 if request.method=='POST':
  if request.content_length is None or request.content_length>1024:return jsonify(error='Request too large'),413
  body=request.get_data()
 try:
  req=urllib.request.Request(root+'/'+part+('?' + query if query else ''),data=body,headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=4) as response:raw=response.read(1048577);status=response.status
  if len(raw)>1048576:raise ValueError()
  result=json.loads(raw)
 except urllib.error.HTTPError as error:
  return jsonify(error='Notebook not found' if error.code==400 else 'Statistics unavailable'),error.code
 except (OSError,ValueError):return jsonify(error='Statistics unavailable. Try Refresh.'),503
 return jsonify(result),status

def register_routes(app):
 app.add_url_rule('/goodnotes/statistics','goodnotes_statistics',page)
 app.add_url_rule('/goodnotes/statistics/api/<part>','goodnotes_statistics_api',proxy,methods=['GET','POST'])
 @app.after_request
 def private(response):
  if (request.endpoint or '').startswith('goodnotes_statistics'):
   response.headers['Cache-Control']='private, no-store';response.headers['X-Content-Type-Options']='nosniff'
  return response
