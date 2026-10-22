"""Real image store -> SQLite -> production Node CLI -> encrypted disposable notes.

Run only with diary_client_fixture.mjs shadowing /opt/livesync/client.mjs in a
disposable container. The caller mounts the native client at /native-livesync.
"""
import base64,hashlib,json,os,sys,tempfile,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask,g
from lib.config import Config
from lib.assistant_agenda import calendar_clock
from lib.assistant_diary import append_today_diary

prefix=os.environ.get('DIARY_TEST_PREFIX','')
if not prefix.startswith('Integration checks/diary-'):raise SystemExit('Disposable namespace required')
source=json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix='diary-pipeline-') as temporary:
 app=Flask(__name__);app.config.from_object(Config)
 app.config.update(ASSISTANT_DIARY_STATE=Path(temporary)/'receipts.sqlite',ACTIVITY_FILE=str(Path(temporary)/'activity.json'))
 with app.test_request_context('/'):
  request_id=str(uuid.uuid4());clock=calendar_clock()
  g.diary_context=dict(request_id=request_id,clock=clock,image_bytes=base64.b64decode(source['image_data']))
  result=append_today_diary('Admin','Temporary integration check','Synthetic integration check: Photo preview check.')
  assert result.get('success'),result
  repeated=append_today_diary('Admin','Different title','This must not replace the first entry.')
  assert repeated.get('status')=='already_saved',repeated
  assert repeated['image_url']==result['image_url']
  assert len(g.assistant_actions)==1
  print('DIARY_PIPELINE='+json.dumps({'prefix':prefix,'date':clock['today'],'timestamp':clock['local_datetime'],
   'id':hashlib.sha256(('Admin\0'+request_id).encode()).hexdigest()[:32],'image':result['image_url']}))
