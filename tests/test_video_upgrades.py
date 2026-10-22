import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
import uuid
import json
from types import SimpleNamespace
from lib import video_downloads as jobs

class UpgradeValidation(unittest.TestCase):
    def check(self, old, new, accepted):
        with patch.object(jobs, 'media_info', side_effect=[old,new]):
            if accepted:
                jobs.validate_upgrade('old','new')
            else:
                with self.assertRaises(ValueError):jobs.validate_upgrade('old','new')
    def test_1080_upgrade(self):self.check((720,1290,True),(1080,1291,True),True)
    def test_no_downgrade(self):self.check((1080,1290,True),(720,1290,True),False)
    def test_same_quality(self):self.check((720,1290,True),(720,1290,True),False)
    def test_wrong_duration(self):self.check((720,1290,True),(1080,900,True),False)
    def test_missing_audio(self):self.check((720,1290,True),(1080,1290,False),False)

class Replacement(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.patches=[patch.object(jobs,'ROOT',self.root),patch.object(jobs,'DB',self.root/'jobs.sqlite')]
        for p in self.patches:p.start()
        self.row={'id':str(uuid.uuid4()),'title':'Test','url':'https://example.com/test.mp4',
                  'filename':'original.mp4','folder_name':'Test','_upgrade':True}
        self.path=jobs.folder(self.row['id'],self.row);self.path.mkdir()
        self.old=self.path/'original.mp4';self.old.write_bytes(b'original')
        with jobs.connection() as db:
            db.execute('INSERT INTO downloads(id,title,url,status,filename,folder_name) VALUES (?,?,?,?,?,?)',
                       (self.row['id'],'Test',self.row['url'],'downloading','original.mp4','Test'))
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_failed_download_keeps_original_ready(self):
        with patch.object(jobs,'direct_download',side_effect=ValueError('offline')):
            jobs.download(self.row)
        self.assertEqual(self.old.read_bytes(),b'original')
        self.assertEqual(jobs.items()[0]['status'],'ready')
    def test_verified_upgrade_replaces_same_path_without_duplicate(self):
        part=self.path/'video.part';part.write_bytes(b'new video')
        result=SimpleNamespace(returncode=0,stdout=json.dumps({'streams':[{'codec_type':'video','height':1080}],
                                                              'format':{'format_name':'mp4'}}).encode())
        with patch.object(jobs,'validate_upgrade'),patch.object(jobs.subprocess,'run',return_value=result):
            jobs.finish(self.row,part,9)
        self.assertEqual(self.old.read_bytes(),b'new video')
        self.assertEqual(jobs.items()[0]['filename'],'original.mp4')
        self.assertEqual(list(self.path.iterdir()),[self.old])

if __name__=='__main__':unittest.main()
