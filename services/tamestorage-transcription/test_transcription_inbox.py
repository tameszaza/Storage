import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,'/srv/docker/tamestorage')
sys.path.insert(0,str(Path.home()/'.local/lib/tamestorage-transcription'))
from lib.transcription_store import Store
from inbox import Inbox

class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();base=Path(self.tmp.name)
        self.queue=Store.initialize(base/'state',base/'temp/Transcription',base/'Admin/Transcripts')
        self.clock=0;self.inbox=Inbox(self.queue,now=lambda:self.clock)
    def tearDown(self):self.tmp.cleanup()
    def tick(self,seconds):self.clock=seconds;return self.inbox.once()
    def test_closed_file_is_queued_once_with_requested_defaults(self):
        source=self.queue.inputs/'Meeting.m4a';source.write_bytes(b'recording')
        self.assertTrue(self.tick(0));self.assertTrue(source.exists())
        self.assertTrue(self.tick(5));self.assertFalse(source.exists())
        self.assertFalse(self.tick(10));rows=self.queue.listing();self.assertEqual(len(rows),1)
        row=self.queue.get(rows[0]['id']);self.assertEqual(row['state'],'queued')
        self.assertEqual(json.loads(row['options']),dict(language='en',vocabulary='',llm=True,speakers=True))
        self.assertEqual(self.queue.input_path(row).read_bytes(),b'recording')
        self.assertEqual(row['sha256'],hashlib.sha256(b'recording').hexdigest())
        self.tick(20);self.assertEqual(len(self.queue.listing()),1)
    def test_partial_files_links_and_portal_job_folders_are_ignored(self):
        (self.queue.inputs/'.upload.m4a').write_bytes(b'partial')
        (self.queue.inputs/'recording.m4a.part').write_bytes(b'partial')
        outside=Path(self.tmp.name)/'outside.wav';outside.write_bytes(b'primary')
        (self.queue.inputs/'linked.wav').symlink_to(outside)
        job=self.queue.create('portal.wav',5,'Portal',{})
        self.queue.input_path(job).write_bytes(b'audio')
        self.assertFalse(self.tick(0));self.assertFalse(self.tick(10))
        self.assertEqual(len(self.queue.listing()),1);self.assertEqual(outside.read_bytes(),b'primary')
    def test_open_writer_growth_is_preserved_after_move(self):
        source=self.queue.inputs/'Growing.wav';source.write_bytes(b'first')
        with source.open('ab') as writer:
            self.tick(0);self.tick(5)
            writer.write(b' final');writer.flush()
            self.assertTrue(self.tick(7));self.assertTrue(self.tick(11))
            self.assertFalse(self.tick(12))
        row=self.queue.get(self.queue.listing()[0]['id'])
        self.assertEqual(row['state'],'queued');self.assertEqual(row['size'],11)
        self.assertEqual(self.queue.input_path(row).read_bytes(),b'first final')
    def test_restart_recovers_moved_recording(self):
        source=self.queue.inputs/'Recover.mp3';source.write_bytes(b'keep me')
        self.tick(0);self.tick(5)
        self.inbox=Inbox(self.queue,now=lambda:self.clock)
        self.assertFalse(self.tick(10));row=self.queue.get(self.queue.listing()[0]['id'])
        self.assertEqual(row['state'],'queued');self.assertEqual(self.queue.input_path(row).read_bytes(),b'keep me')

if __name__=='__main__':unittest.main()
