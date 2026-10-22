import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('activity_collector',ROOT/'tamestorage_activity.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.state=self.root/'state';self.state.mkdir()
        self.override=patch.object(a,'STATE',self.state);self.override.start()
    def tearDown(self):self.override.stop();self.tmp.cleanup()
    def database(self,relative,sql):
        path=self.state/relative;path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db:db.executescript(sql)
        return path
    def test_missing_database_is_unknown_and_not_created(self):
        c=a.Collector();c.search()
        self.assertTrue(all(t['state']=='unknown' for t in c.tasks))
        self.assertFalse((self.state/'lecture-search/index.sqlite').exists())
    def test_snapshot_reads_ocr_progress_without_schema_or_queue_mutation(self):
        path=self.database('lecture-search/index.sqlite', '''CREATE TABLE docs(path TEXT,kind TEXT,page_count INTEGER);CREATE TABLE ocr_jobs(path TEXT);CREATE TABLE vector_pending(path TEXT);CREATE TABLE pages(path TEXT,page INTEGER,text TEXT,method TEXT,indexed INTEGER);
        INSERT INTO docs VALUES('PDF Library/lecture.pdf','lecture PDF',3);INSERT INTO ocr_jobs VALUES('PDF Library/lecture.pdf');INSERT INTO vector_pending VALUES('x');
        INSERT INTO pages VALUES('PDF Library/lecture.pdf',1,'recognized','ocr',1),('PDF Library/lecture.pdf',2,'','native',1),('PDF Library/lecture.pdf',3,'native','native-image',1);''')
        before=path.read_bytes();c=a.Collector();c.units={'ocr':{'ActiveState':'activating','Result':'success'}};c.search()
        ocr=c.tasks[0];self.assertEqual((ocr['done'],ocr['total'],ocr['queued']),(1,3,1));self.assertEqual(ocr['state'],'running');self.assertEqual(ocr['host'],'Server')
        self.assertEqual(path.read_bytes(),before)
        with a.closing(a.database(path)) as db:
            with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM ocr_jobs')
    def test_idle_watchers_are_not_running_and_children_reveal_actual_stage(self):
        c=a.Collector();c.units={'publish':{'MainPID':'100','ActiveState':'active','Result':'success'}}
        c.procs=[(101,100,1,'node export.mjs')];c.publisher()
        self.assertEqual(c.tasks[0]['stage'],'Exporting vault notes')
        c.tasks=[];c.procs=[];c.publisher();self.assertEqual(c.tasks[0]['state'],'idle')
    def test_off_does_not_become_offline_or_create_fake_pending_work(self):
        c=a.Collector();c.remote={'accepting':False};c.tasks=[a.task('transcription','Transcription','Cyborg'),a.task('queued','Transcription','Cyborg',state='queued',queued=4)]
        c.unavailable=['Cyborg helpers','Cyborg AI'];host=c.helper_task()
        self.assertEqual(host['state'],'paused');self.assertEqual(c.tasks[0]['state'],'paused');self.assertFalse(c.tasks[0]['workPending']);self.assertTrue(c.tasks[1]['workPending']);self.assertEqual(c.unavailable,[])
    def test_lost_cyborg_keeps_work_waiting(self):
        c=a.Collector();c.tasks=[a.task('queued','Transcription','Cyborg',state='queued')]
        self.assertEqual(c.helper_task()['state'],'offline');self.assertEqual(c.tasks[0]['state'],'waiting')
    def test_couchdb_only_reads_active_tasks_and_never_exposes_credentials(self):
        home=self.root/'home';p=home/'.config/goodnotes-links/livesync.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'username':'fixture','password':'private-secret','database':'do-not-display'}))
        c=a.Collector()
        with patch.object(a,'HOME',home),patch.object(a,'http_json',return_value=[{'type':'database_compaction','progress':45,'database':'private-name'}]) as request:
            c.couch()
        self.assertEqual(request.call_args.args[:3],('127.0.0.1',5984,'/_active_tasks'))
        self.assertEqual(c.tasks[0]['progress'],45)
        self.assertNotIn('private',json.dumps(c.tasks))
    def test_unrecognized_journal_text_and_credentials_never_reach_console(self):
        c=a.Collector();lines=[{'__REALTIME_TIMESTAMP':'1000000','MESSAGE':'password=private-secret https://u:secret@private'}, {'__REALTIME_TIMESTAMP':'2000000','MESSAGE':json.dumps({'ocr_pages_processed':3,'path':'lecture.pdf','page':12})}]
        with patch.object(a.subprocess,'run',return_value=type('Result',(),{'stdout':'\n'.join(json.dumps(v) for v in lines)})()):c.journal()
        self.assertEqual(len(c.events),1);self.assertIn('page 12',c.events[0]['message']);self.assertNotIn('private',json.dumps(c.events))
        self.assertNotIn('secret',a.text('https://u:secret@host/path token=secret Bearer secret'))
    def test_media_and_transcription_limit_payload_and_report_real_stages(self):
        self.database('transcription/queue.sqlite', '''CREATE TABLE jobs(id TEXT,title TEXT,state TEXT,stage TEXT,progress REAL,created REAL,updated REAL,lease_until REAL);
        INSERT INTO jobs VALUES('active','Recording','running','Separating vocals',23,1,2,9999999999),('old','Done','completed','Saved',100,1,2,0);''')
        c=a.Collector();c.transcription()
        self.assertEqual(c.tasks[0]['stage'],'Separating vocals');self.assertEqual(c.tasks[0]['progress'],23);self.assertEqual(c.events[0]['state'],'completed')
    def test_ordinary_observation_never_starts_services(self):
        c=a.Collector()
        with patch.object(a,'http_json',return_value={}),patch.object(a,'unit_status',return_value={}),patch.object(a,'processes',return_value=[]),patch.dict('sys.modules',{'tamestorage_containers':type('Broker',(),{'remote_snapshot':staticmethod(lambda:{})})()}):c.base()
        self.assertEqual(c.helper,{})
    def test_cached_and_concurrent_requests_do_not_duplicate_collection(self):
        old=dict(a.CACHE)
        try:
            a.CACHE.update(at=0,data=None)
            with patch.object(a.Collector,'collect',return_value={'version':1}) as collect:
                self.assertEqual(a.snapshot(),{'version':1});self.assertEqual(a.snapshot(),{'version':1});self.assertEqual(collect.call_count,1)
                a.CACHE['at']=0;a.LOCK.acquire()
                try:self.assertTrue(a.snapshot()['stale'])
                finally:a.LOCK.release()
        finally:a.CACHE.clear();a.CACHE.update(old)

class ApiTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('activity_route',ROOT/'activity.py');self.route=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.route)
        app=Flask(__name__);app.config.update(SECRET_KEY='fixture');app.add_url_rule('/login','login',lambda:'login');self.route.register_routes(app);self.client=app.test_client()
    def test_only_admin_can_collect(self):
        with patch.object(self.route,'host') as host:
            for identity in [None,'Guest']:
                with self.client.session_transaction() as s:
                    s.clear()
                    if identity:s.update(logged_in=True,username=identity)
                self.assertEqual(self.client.get('/admin/activity/snapshot').status_code,302)
            host.assert_not_called()
    def test_fixed_read_only_command_and_private_cache_policy(self):
        with self.client.session_transaction() as s:s.update(logged_in=True,username='Admin')
        with patch.object(self.route,'host',return_value={'ok':True,'activity':{'tasks':[]}}) as host:
            response=self.client.get('/admin/activity/snapshot?command=stop&path=/etc/shadow')
            self.assertEqual(response.status_code,200);host.assert_called_once_with({'action':'activity-snapshot'},timeout=12);self.assertIn('no-store',response.headers['Cache-Control'])
            self.assertEqual(self.client.post('/admin/activity/snapshot').status_code,405)
    def test_failure_is_not_an_empty_healthy_snapshot(self):
        with self.client.session_transaction() as s:s.update(logged_in=True,username='Admin')
        with patch.object(self.route,'host',return_value={'ok':False,'error':'Unavailable'}):
            response=self.client.get('/admin/activity/snapshot');self.assertEqual(response.status_code,503);self.assertFalse(response.json['ok'])

if __name__=='__main__':unittest.main()
