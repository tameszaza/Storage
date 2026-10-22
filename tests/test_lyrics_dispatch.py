import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from lib.transcription_store import Store,digest


class StopRetry(Exception):pass


class OfflineTests(unittest.TestCase):
    def test_unavailable_cyborg_keeps_durable_job_and_retries_only_pending(self):
        source=os.environ.get('LYRICS_DISPATCH_SOURCE',str(Path.home()/'.local/lib/tamestorage-transcription/dispatch.py'))
        spec=importlib.util.spec_from_file_location('lyrics_dispatch_fixture',source)
        dispatch=importlib.util.module_from_spec(spec);spec.loader.exec_module(dispatch)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('lib.transcription_store.shutil.disk_usage',return_value=SimpleNamespace(free=10*1024**3)):
                queue=Store.initialize(root/'state',root/'inputs',root/'outputs')
                row=queue.create('song.opus',5,'Lyrics: song',{'llm':False,'speakers':False})
            queue.input_path(row).write_bytes(b'audio');queue.uploaded(row['id'],digest(queue.input_path(row)))
            mount=json.dumps({'filesystems':[{'uuid':'4dcef454-5a20-484b-b00e-fe9a136f82b9'}]})
            with patch.object(dispatch,'Store',return_value=queue),patch.object(dispatch.subprocess,'check_output',return_value=mount),\
                 patch.object(dispatch.subprocess,'run',return_value=SimpleNamespace(returncode=255)) as ssh,\
                 patch.object(dispatch.time,'sleep',side_effect=StopRetry) as wait:
                with self.assertRaises(StopRetry):dispatch.main()
                ssh.assert_called_once();wait.assert_called_once_with(60)
            self.assertEqual(queue.get(row['id'])['state'],'queued')
            self.assertEqual(queue.input_path(row).read_bytes(),b'audio')
            self.assertTrue((queue.root/'wake').is_file())
            queue.action(row['id'],'cancel')
            with patch.object(dispatch,'Store',return_value=queue),patch.object(dispatch.subprocess,'check_output',return_value=mount),\
                 patch.object(dispatch.subprocess,'run') as ssh:
                self.assertEqual(dispatch.main(),0);ssh.assert_not_called()


if __name__=='__main__':unittest.main()
