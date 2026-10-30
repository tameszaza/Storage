"""Priority scenarios for the existing host dispatcher; no services or media run."""
import json,sys,types,unittest,os
from pathlib import Path
from unittest.mock import Mock,patch
SOURCE=Path(os.environ.get('WORKSPACE_DISPATCH_SOURCE','/home/tameszaza/.local/lib/tamestorage-transcription/dispatch.py'))
class PriorityTests(unittest.TestCase):
 def scenario(self,audio=False,helper_pending=False,core=False,archive=True,workspace=False,helper_online=True):
  helper=types.ModuleType('helper_client');helper.Paused=type('Paused',(Exception,),{})
  with patch.dict(sys.modules,{'helper_client':helper}):
   module=types.ModuleType('queue_fixture')
   source=SOURCE.read_text().replace('from lib.transcription_store import Store','').replace('from lib.lyric_transcription import publish_ready','')
   exec(compile(source,str(SOURCE),'exec'),module.__dict__)
  helper.pending=Mock(return_value=helper_pending);helper.ensure=Mock();helper.resume_pending=Mock()
  module.helper_available=Mock(return_value=helper_online)
  module.wake_stamp=Mock(return_value=None)
  module.workspace_retry_delay=Mock(return_value=None)
  module.Store=Mock(return_value=types.SimpleNamespace(pending=Mock(return_value=audio)))
  module.workspace_pending=Mock(return_value=workspace);module.workspace_step=Mock(return_value=True)
  module.publish_ready=Mock();module.archive_pending=Mock(return_value=archive);module.archive_step=Mock(return_value=True)
  child=Mock();child.wait.return_value=0;child.__enter__=Mock(return_value=child);child.__exit__=Mock(return_value=None)
  class End(Exception):pass
  with patch.object(module,'Path',return_value=types.SimpleNamespace(exists=lambda:False)),patch.object(module.subprocess,'check_output',return_value=json.dumps({'filesystems':[{'uuid':'4dcef454-5a20-484b-b00e-fe9a136f82b9'}]})),patch.object(module.subprocess,'run',return_value=types.SimpleNamespace(returncode=0 if core else 3)),patch.object(module.subprocess,'Popen',return_value=child),patch.object(module.time,'sleep',side_effect=End):
   try:module.main()
   except End:pass
  return {'archive':module.archive_step.called,'workspace':module.workspace_step.called}
 def test_audio_goes_first(self):self.assertFalse(self.scenario(audio=True)['archive'])
 def test_existing_helper_queue_goes_first(self):self.assertFalse(self.scenario(helper_pending=True)['archive'])
 def test_index_ocr_publish_goes_first(self):self.assertFalse(self.scenario(core=True)['archive'])
 def test_archive_runs_when_existing_work_is_idle(self):self.assertTrue(self.scenario()['archive'])
 def test_empty_queue_keeps_no_worker(self):self.assertFalse(self.scenario(archive=False)['archive'])
 def test_workspace_yields_to_audio(self):self.assertFalse(self.scenario(audio=True,workspace=True)['workspace'])
 def test_workspace_yields_to_helpers(self):self.assertFalse(self.scenario(helper_pending=True,workspace=True)['workspace'])
 def test_workspace_yields_to_primary_indexing(self):self.assertFalse(self.scenario(core=True,workspace=True)['workspace'])
 def test_unreachable_background_helper_does_not_starve_reachable_workspace(self):
  self.assertTrue(self.scenario(helper_pending=True,workspace=True,helper_online=False)['workspace'])
 def test_unreachable_helper_does_not_bypass_active_primary_indexing(self):
  self.assertFalse(self.scenario(helper_pending=True,workspace=True,helper_online=False,core=True)['workspace'])
 def test_workspace_precedes_archive(self):
  ran=self.scenario(workspace=True);self.assertTrue(ran['workspace']);self.assertFalse(ran['archive'])
 def test_workspace_runs_without_an_archive(self):self.assertTrue(self.scenario(workspace=True,archive=False)['workspace'])
class DispatchWakeTests(unittest.TestCase):
 def module(self):
  helper=types.ModuleType('helper_client');helper.Paused=type('Paused',(Exception,),{})
  with patch.dict(sys.modules,{'helper_client':helper}):
   m=types.ModuleType('wake_fixture')
   source=SOURCE.read_text().replace('from lib.transcription_store import Store','').replace('from lib.lyric_transcription import publish_ready','')
   exec(compile(source,str(SOURCE),'exec'),m.__dict__)
  return m
 def test_helper_readiness_is_a_read_only_connection_probe(self):
  m=self.module();m.helper_client.config=lambda:{'url':'http://worker.example:9018','token':'not-used'}
  with patch.object(m.socket,'create_connection',side_effect=ConnectionRefusedError) as connect:self.assertFalse(m.helper_available())
  connect.assert_called_once_with(('worker.example',9018),timeout=1)
  with patch.object(m.socket,'create_connection') as connect:self.assertTrue(m.helper_available())
 def clock(self,m,retry,ready=True):
  elapsed=[0.0];delays=[]
  def sleep(seconds):elapsed[0]+=seconds;delays.append(seconds)
  with patch.object(m,'wake_stamp',return_value=1),patch.object(m,'workspace_retry_delay',return_value=retry),patch.object(m.time,'monotonic',side_effect=lambda:elapsed[0]),patch.object(m.time,'sleep',side_effect=sleep):
   m.wait_for_work(60,1,workspace_ready=ready)
  return sum(delays)
 def test_arrival_during_another_step_interrupts_old_idle_wait(self):
  m=self.module()
  with patch.object(m,'wake_stamp',return_value=2),patch.object(m.time,'sleep') as sleep:m.wait_for_work(60,1)
  sleep.assert_not_called()
 def test_workspace_retry_is_not_delayed_to_sixty_seconds(self):self.assertAlmostEqual(self.clock(self.module(),5.0),5.0)
 def test_primary_work_keeps_existing_backoff_priority(self):self.assertAlmostEqual(self.clock(self.module(),0.0,ready=False),60.0)
 def test_retry_delay_respects_worker_lease_and_earliest_eligible_job(self):
  import sqlite3,tempfile,time
  m=self.module()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   with sqlite3.connect(root/'jobs.sqlite3') as db:
    db.execute('CREATE TABLE jobs(status,retry_at,lease)');db.executemany('INSERT INTO jobs VALUES(?,?,?)',[('running',103,130),('queued',107,0),('ready',0,0)])
   with patch.object(m,'WORKSPACE',root),patch.object(m.time,'time',return_value=100):self.assertEqual(m.workspace_retry_delay(),7)
if __name__=='__main__':unittest.main()
