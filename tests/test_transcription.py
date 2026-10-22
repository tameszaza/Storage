import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from lib.transcription_store import Store,QueueError,ARTIFACTS,digest
from routes.transcription import register_routes

class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        # The live container's /tmp is deliberately only 256 MB. Model sufficient
        # NAS capacity explicitly; shortage behavior is tested separately below.
        self.space=patch('lib.transcription_store.shutil.disk_usage',return_value=SimpleNamespace(free=10*1024**3));self.space.start()
        self.store=Store.initialize(self.base/'state',self.base/'temp/Transcription',self.base/'Admin/Transcripts')
        (self.store.root/'worker.key').write_text('fixture-worker-key')
        app=Flask(__name__);app.config.update(SECRET_KEY='fixture-secret',UPLOAD_FOLDER=str(self.base/'uploads'),TRANSCRIPTION_ROOT=str(self.store.root),TRANSCRIPTION_INPUTS=str(self.store.inputs),TRANSCRIPTION_OUTPUTS=str(self.store.outputs))
        app.add_url_rule('/login','login',lambda:'Login');register_routes(app);self.client=app.test_client()
        self.auth={'Authorization':'Bearer fixture-worker-key'}

    def tearDown(self):self.space.stop();self.tmp.cleanup()

    def test_insufficient_space_is_rejected_before_upload(self):
        self.space.stop()
        with patch('lib.transcription_store.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaises(QueueError) as raised:self.store.create('large.wav',100,'Recording',{})
            self.assertEqual(raised.exception.status,507)
        self.space.start()

    def owner(self):
        with self.client.session_transaction() as session:session.update(logged_in=True,username='Admin',transcription_csrf='fixture-csrf')

    def queued(self):
        row=self.store.create('meeting.wav',12,'Meeting',{});self.store.input_path(row).write_bytes(b'fixtureaudio')
        self.store.uploaded(row['id'],hashlib.sha256(b'fixtureaudio').hexdigest());return self.store.get(row['id'])

    def ready(self):
        row=self.queued();job=self.store.claim()
        for name in ARTIFACTS:
            path=self.store.work_dir(row['id'])/(name+'.ready');path.write_bytes((name+' fixture').encode())
            self.store.artifact_saved(row['id'],job['lease'],name,digest(path),path.stat().st_size)
        return job

    def test_pause_keeps_recording_invalidates_lease_and_resumes(self):
        self.queued();job=self.store.claim()
        self.store.progress(job['id'],job['lease'],'recognizing',37)
        url='/api/transcription/jobs/'+job['id']+'/pause'
        self.assertEqual(self.client.post(url,json={}).status_code,401)
        bad={**self.auth,'X-Job-Lease':'wrong'}
        self.assertEqual(self.client.post(url,headers=bad,json={}).status_code,409)
        headers={**self.auth,'X-Job-Lease':job['lease']}
        self.assertEqual(self.client.post(url,headers=headers,json={'reason':'bad'}).status_code,400)
        response=self.client.post(url,headers=headers,json={'reason':'Server tasks off'})
        self.assertEqual(response.status_code,200)
        paused=self.store.get(job['id'])
        self.assertEqual(paused['state'],'queued');self.assertEqual(paused['progress'],37)
        self.assertEqual(paused['stage'],'Server tasks off')
        self.assertTrue(self.store.input_path(paused).is_file())
        with self.assertRaises(QueueError):self.store.progress(job['id'],job['lease'],'stale',90)
        resumed=self.store.claim();self.assertEqual(resumed['id'],job['id'])
        self.assertNotEqual(resumed['lease'],job['lease'])

    def test_authentication_and_csrf(self):
        self.assertEqual(self.client.get('/admin/transcription/jobs').status_code,302)
        with self.client.session_transaction() as session:session.update(logged_in=True,username='guest')
        self.assertEqual(self.client.get('/admin/transcription/jobs').status_code,302)
        self.owner();self.assertEqual(self.client.post('/admin/transcription/jobs',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/transcription/claim').status_code,401)
        self.assertEqual(self.client.get('/admin/transcription/jobs').json,{'jobs':[]})

    def test_upload_stream_integrity_and_safe_paths(self):
        self.owner();headers={'X-CSRF-Token':'fixture-csrf'}
        created=self.client.post('/admin/transcription/jobs',json={'filename':'../../meeting.wav','size':12,'options':{}},headers=headers)
        self.assertEqual(created.status_code,201);job=created.json['job'];self.assertEqual(job['filename'],'meeting.wav')
        uploaded=self.client.put(created.json['upload_url'],data=b'fixtureaudio',headers=headers)
        self.assertEqual(uploaded.status_code,200);self.assertEqual(uploaded.json['job']['state'],'queued')
        self.assertTrue((self.store.root/'wake').exists())
        self.assertNotIn('lease',uploaded.json['job'])
        self.assertEqual(self.client.get('/admin/transcription/jobs/not-a-job/download/transcript').status_code,404)

    def test_single_claim_across_concurrent_workers_and_resume(self):
        self.queued()
        with ThreadPoolExecutor(max_workers=8) as pool:claims=list(pool.map(lambda _:self.store.claim(),range(8)))
        assigned=[c for c in claims if c];self.assertEqual(len(assigned),1);first=assigned[0]
        with self.store.transaction() as db:db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(first['id'],))
        resumed=self.store.claim();self.assertEqual(resumed['id'],first['id']);self.assertNotEqual(resumed['lease'],first['lease'])
        with self.assertRaises(QueueError):self.store.progress(first['id'],first['lease'],'old worker',0)

    def test_result_publication_preserves_primary_files_and_audio_until_temp_expiry(self):
        job=self.ready();result=self.store.complete(job['id'],job['lease'])
        output=self.store.outputs/result['result_dir']/'Transcript.txt';self.assertEqual(output.read_text(),'transcript fixture')
        self.assertTrue(self.store.input_path(job).is_file());self.assertFalse((self.store.root/'wake').exists())
        self.assertEqual(self.store.complete(job['id'],job['lease'])['state'],'completed')
        self.store.action(job['id'],'remove');self.assertTrue(output.exists())

    def test_changed_audio_is_not_deleted(self):
        job=self.ready();self.store.input_path(job).write_bytes(b'user changed recording')
        self.store.complete(job['id'],job['lease']);self.assertEqual(self.store.input_path(job).read_bytes(),b'user changed recording')

    def test_cancellation_rejects_old_worker_and_retry_keeps_source(self):
        self.queued();job=self.store.claim();self.store.action(job['id'],'cancel')
        with self.assertRaises(QueueError):self.store.progress(job['id'],job['lease'],'still running',5)
        self.store.action(job['id'],'retry');again=self.store.claim();self.assertNotEqual(job['lease'],again['lease'])

    def test_result_upload_rejects_bad_checksum_or_non_text(self):
        self.queued();job=self.store.claim();headers={**self.auth,'X-Job-Lease':job['lease'],'X-Content-SHA256':'0'*64}
        url='/api/transcription/jobs/'+job['id']+'/artifacts/transcript'
        self.assertEqual(self.client.put(url,data=b'wrong checksum',headers=headers).status_code,400)
        invalid=b'\xff\x00';headers['X-Content-SHA256']=hashlib.sha256(invalid).hexdigest()
        self.assertEqual(self.client.put(url,data=invalid,headers=headers).status_code,400)
        self.assertFalse((self.store.work_dir(job['id'])/'transcript.ready').exists())

    def test_missing_disk_refuses_to_create_substitute_paths(self):
        (self.store.root/'READY').unlink()
        with self.assertRaises(OSError):Store(self.store.root,self.store.inputs,self.store.outputs)
        self.assertEqual(self.client.post('/api/transcription/claim',headers=self.auth).status_code,503)

if __name__=='__main__':unittest.main()
