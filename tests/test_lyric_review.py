import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.test_lyric_transcription import LyricsTests
from lib.lyric_transcription import publish_ready
from lib.transcription_store import QueueError,digest
from lib import lyric_review as review
from routes import music
from routes.transcription import register_routes as transcription_routes
from flask import Flask


class ReviewTests(unittest.TestCase):
    enqueue=LyricsTests.enqueue
    ready=LyricsTests.ready
    tearDown=LyricsTests.tearDown
    def setUp(self):
        LyricsTests.setUp(self)
        self.job=self.ready();self.ident=self.job['id'];publish_ready(self.queue,self.music)
        self.target=self.audio.with_suffix('.lrc')
        raw=b'[by:Cybrog AI; automatic transcription]\n[00:00.00]Transcribed by Cybrog AI\n[00:01.23]The original uncertain words\n[00:05.00]Another correct line\n'
        self.target.write_bytes(raw)
        details=self.queue.work_dir(self.ident)/'details.ready'
        details.write_bytes(gzip.compress(json.dumps({'selected_word_units':[{'text':'uncertain','start':2,'uncertain':True},{'text':'Another','start':5,'uncertain':False}]}).encode()))
        with self.queue.transaction() as db:
            row=self.queue.get(self.ident,db);arts=json.loads(row['artifacts']);arts['transcript']['sha256']=digest(self.target);arts['details']['sha256']=digest(details)
            db.execute('UPDATE jobs SET artifacts=? WHERE id=?',(json.dumps(arts),self.ident))

    def payload(self):
        data=review.read(self.queue,self.ident,self.music)
        return dict(revision=data['revision'],lines=[{k:line[k] for k in ('index','text','confirmed')} for line in data['lines'] if line['editable']])

    def test_uncertainty_maps_to_line_and_save_preserves_timing_and_song(self):
        data=review.read(self.queue,self.ident,self.music)
        self.assertEqual([x['uncertain'] for x in data['lines']],[[],['uncertain'],[]])
        payload=self.payload();payload['lines'][0].update(text='My verified correction',confirmed=True)
        token=review.submit(self.queue,self.ident,payload,self.music)
        self.assertEqual(review.status(self.queue,self.ident,token)['state'],'saving')
        self.assertEqual(review.publish_pending(self.queue,self.music),1)
        self.assertEqual(review.status(self.queue,self.ident,token)['state'],'saved')
        self.assertEqual(self.audio.read_bytes(),b'original music')
        self.assertIn('[00:01.23]My verified correction',self.target.read_text())
        self.assertIn('[00:05.00]Another correct line',self.target.read_text())
        self.assertIn('[00:00.00]Transcribed by Cybrog AI',self.target.read_text())
        self.assertTrue(review.read(self.queue,self.ident,self.music)['lines'][1]['confirmed'])
        self.assertTrue((self.queue.root/'lyrics-review-wake').exists())
        review.publish_pending(self.queue,self.music)
        self.assertFalse((self.queue.root/'lyrics-review-wake').exists())

    def test_stale_window_and_double_submission_rejected(self):
        payload=self.payload();token=review.submit(self.queue,self.ident,payload,self.music)
        with self.assertRaises(QueueError):review.submit(self.queue,self.ident,payload,self.music)
        review.publish_pending(self.queue,self.music)
        payload['lines'][0]['confirmed']=True
        review.submit(self.queue,self.ident,payload,self.music);review.publish_pending(self.queue,self.music)
        with self.assertRaises(QueueError):review.submit(self.queue,self.ident,payload,self.music)

    def test_manual_provider_change_before_publication_is_preserved(self):
        payload=self.payload();token=review.submit(self.queue,self.ident,payload,self.music)
        self.target.write_text('Provider or manual lyrics')
        self.assertEqual(review.publish_pending(self.queue,self.music),0)
        self.assertEqual(self.target.read_text(),'Provider or manual lyrics')
        self.assertEqual(review.status(self.queue,self.ident,token)['state'],'error')

    def test_source_changed_and_deleted_are_preserved(self):
        payload=self.payload();token=review.submit(self.queue,self.ident,payload,self.music)
        self.audio.write_bytes(b'new song')
        self.assertEqual(review.publish_pending(self.queue,self.music),0)
        self.assertEqual(review.status(self.queue,self.ident,token)['state'],'error')
        self.assertEqual(self.audio.read_bytes(),b'new song')

    def test_recovery_after_replace_before_db_commit(self):
        payload=self.payload();payload['lines'][0]['text']='Recovered words'
        token=review.submit(self.queue,self.ident,payload,self.music)
        opt=json.loads(self.queue.get(self.ident)['options']);self.target.write_text(opt['lyrics_review_pending']['text'])
        self.assertEqual(review.publish_pending(self.queue,self.music),1)
        self.assertEqual(review.status(self.queue,self.ident,token)['state'],'saved')

    def test_validation_protects_timestamps_metadata_and_paths(self):
        for mutator in (lambda p:p['lines'].pop(),lambda p:p['lines'][0].update(text='new\n[99:99.99]injected'),lambda p:p['lines'][0].update(index=-1),lambda p:p['lines'][0].update(index=1),lambda p:p['lines'][0].update(confirmed='yes')):
            p=self.payload();mutator(p)
            with self.assertRaises(QueueError):review.submit(self.queue,self.ident,p,self.music)
        saved=self.target.read_bytes();self.target.unlink();self.target.symlink_to(self.audio)
        with self.assertRaises(QueueError):review.read(self.queue,self.ident,self.music)
        self.assertEqual(self.audio.read_bytes(),b'original music')

    def test_legacy_generated_lyrics_remain_reviewable(self):
        with self.queue.transaction() as db:
            opt=json.loads(self.queue.get(self.ident,db)['options']);opt.pop('lyrics_disposition')
            db.execute('UPDATE jobs SET options=? WHERE id=?',(json.dumps(opt),self.ident))
        self.assertEqual(review.listing(self.queue,'Collection')[0]['id'],self.ident)
        self.assertEqual(review.read(self.queue,self.ident,self.music)['id'],self.ident)

    def test_clear_uncertain_words_without_changing_timing(self):
        p=self.payload();p['lines'][0].update(text='',confirmed=True)
        review.submit(self.queue,self.ident,p,self.music);review.publish_pending(self.queue,self.music)
        self.assertIn('[00:01.23]\n',self.target.read_text())
        line=review.read(self.queue,self.ident,self.music)['lines'][1]
        self.assertTrue(line['confirmed']);self.assertTrue(line['editable']);self.assertEqual(line['text'],'')
        p=self.payload();p['lines'][0]['text']='Restored correct words'
        review.submit(self.queue,self.ident,p,self.music);review.publish_pending(self.queue,self.music)
        self.assertIn('[00:01.23]Restored correct words',self.target.read_text())

    def test_legacy_attribution_only_migration_keeps_old_lyrics_reviewable(self):
        original=self.target.read_bytes().replace(b'[by:Cybrog AI; automatic transcription]\n',b'[by:Automatic local transcription]\n').replace(b'[00:00.00]Transcribed by Cybrog AI\n',b'')
        artifact=self.queue.work_dir(self.ident)/'transcript.ready';artifact.write_bytes(original)
        with self.queue.transaction() as db:
            row=self.queue.get(self.ident,db);opt=json.loads(row['options']);opt.pop('lyrics_disposition')
            arts=json.loads(row['artifacts']);arts['transcript']['sha256']=digest(artifact)
            db.execute('UPDATE jobs SET options=?,artifacts=? WHERE id=?',(json.dumps(opt),json.dumps(arts),self.ident))
        self.assertEqual(review.read(self.queue,self.ident,self.music)['id'],self.ident)
        self.target.write_bytes(self.target.read_bytes().replace(b'original uncertain',b'changed by someone'))
        with self.assertRaises(QueueError):review.read(self.queue,self.ident,self.music)

    def test_api_admin_csrf_and_audio_range(self):
        app=Flask(__name__);app.config.update(SECRET_KEY='fixture',MUSIC_FOLDER=str(self.music),UPLOAD_FOLDER=str(self.base/'uploads'),TRANSCRIPTION_ROOT=str(self.queue.root),TRANSCRIPTION_INPUTS=str(self.queue.inputs),TRANSCRIPTION_OUTPUTS=str(self.queue.outputs))
        app.add_url_rule('/login','login',lambda:'login');transcription_routes(app);music.register_routes(app)
        client=app.test_client();url='/music-sync/lyrics/'+self.ident
        self.assertEqual(client.get(url).status_code,302)
        with client.session_transaction() as session:session.update(logged_in=True,username='guest')
        self.assertEqual(client.get(url).status_code,302)
        with client.session_transaction() as session:session.update(username='Admin',transcription_csrf='test')
        self.assertEqual(client.post(url,json=self.payload()).status_code,403)
        self.assertEqual(client.get(url).status_code,200)
        self.assertEqual(client.head(url).status_code,200)
        self.assertFalse((self.queue.root/'lyrics-review-wake').exists())
        self.assertIn('no-store',client.get(url).headers['Cache-Control'])
        response=client.get(url+'/audio',headers={'Range':'bytes=0-3'});self.assertEqual(response.status_code,206);self.assertEqual(response.data,b'orig');self.assertEqual(response.mimetype,'audio/ogg');response.close()
        response=client.post(url,json=self.payload(),headers={'X-CSRF-Token':'test'});self.assertEqual(response.status_code,202)
        self.assertEqual(client.get(url+'/status?token='+response.json['token']).json['state'],'saving')
        self.assertEqual(client.post(url,data=b'x'*300001,headers={'X-CSRF-Token':'test'}).status_code,413)

if __name__=='__main__':unittest.main()
