import importlib.util
import json
from pathlib import Path
import time
import unittest
from tests import test_transcription as fixtures

spec=importlib.util.spec_from_file_location('retention',Path(__file__).resolve().parents[1]/'deploy/tamestorage-transcription-cleanup.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class RecordingRetentionTests(unittest.TestCase):
    setUp=fixtures.TranscriptionTests.setUp
    tearDown=fixtures.TranscriptionTests.tearDown
    queued=fixtures.TranscriptionTests.queued
    ready=fixtures.TranscriptionTests.ready
    def expire(self):return module.cleanup(self.store.root,self.store.inputs,self.store.outputs,now=time.time()+90000)
    def completed(self):
        job=self.ready();self.store.complete(job['id'],job['lease']);return job
    def test_only_old_completed_audio_expires_and_results_remain(self):
        job=self.completed();audio=self.store.input_path(job)
        self.assertEqual(module.cleanup(self.store.root,self.store.inputs,self.store.outputs),0)
        self.assertTrue(audio.exists());self.assertEqual(self.expire(),1);self.assertFalse(audio.exists())
        row=self.store.get(job['id']);self.assertTrue((self.store.outputs/row['result_dir']/'Transcript.txt').exists())
        self.assertEqual(self.expire(),0)
    def test_queued_running_failed_and_cancelled_inputs_remain(self):
        a=self.queued();b=self.queued();c=self.queued();d=self.queued()
        self.store.claim()
        with self.store.transaction() as db:
            db.execute("UPDATE jobs SET state='failed' WHERE id=?",(c['id'],))
            db.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(d['id'],))
        self.assertEqual(self.expire(),0)
        for row in [a,b,c,d]:self.assertTrue(self.store.input_path(row).exists())
    def test_modified_audio_is_preserved(self):
        job=self.completed();audio=self.store.input_path(job);audio.write_bytes(b'changedaudio')
        self.assertEqual(self.expire(),0);self.assertEqual(audio.read_bytes(),b'changedaudio')
    def test_missing_published_result_preserves_original(self):
        job=self.completed();row=self.store.get(job['id']);(self.store.outputs/row['result_dir']/'Transcript.txt').unlink()
        self.assertEqual(self.expire(),0);self.assertTrue(self.store.input_path(job).exists())
    def test_symlink_directory_is_not_followed(self):
        job=self.completed();folder=self.store.inputs/job['id'];outside=self.base/'outside';folder.rename(outside);folder.symlink_to(outside,target_is_directory=True)
        self.assertEqual(self.expire(),0);self.assertTrue((outside/'Recording.wav').exists())
    def test_symlink_or_fifo_audio_cannot_be_read_or_deleted(self):
        import os
        job=self.completed();audio=self.store.input_path(job);original=self.base/'original.wav';audio.rename(original);audio.symlink_to(original)
        self.assertEqual(self.expire(),0);self.assertTrue(original.exists())
        audio.unlink();os.mkfifo(audio);self.assertEqual(self.expire(),0)
    def test_lyrics_workflow_and_unrelated_files_are_preserved(self):
        job=self.completed()
        with self.store.transaction() as db:db.execute('UPDATE jobs SET options=? WHERE id=?',(json.dumps({'kind':'lyrics'}),job['id']))
        self.assertEqual(self.expire(),0);self.assertTrue(self.store.input_path(job).exists())
        with self.store.transaction() as db:db.execute("UPDATE jobs SET options='{}' WHERE id=?",(job['id'],))
        other=self.store.inputs/job['id']/'Keep.txt';other.write_text('keep')
        self.assertEqual(self.expire(),1);self.assertTrue(other.exists())
    def test_unavailable_storage_fails_closed(self):
        job=self.completed();(self.store.root/'READY').unlink()
        with self.assertRaises(OSError):self.expire()
        self.assertTrue(self.store.input_path(job).exists())
