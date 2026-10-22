import base64,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).parent))
import helper_client as h
spec=importlib.util.spec_from_file_location('control',Path(__file__).with_name('control.py'));control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)
spec=importlib.util.spec_from_file_location('broker',Path(__file__).with_name('tamestorage_containers.py'));broker=importlib.util.module_from_spec(spec);spec.loader.exec_module(broker)
class ClientTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  for name,value in [('DATA',self.root),('PENDING',self.root/'pending.json'),('WAKE',self.root/'wake')]:p=patch.object(h,name,value);p.start();self.addCleanup(p.stop)
 def test_disabled_by_default(self):
  with patch.object(h,'CONFIG',self.root/'missing'):self.assertFalse(h.enabled())
 def test_defer_persists_and_uses_existing_wake(self):
  h.defer('ocr');h.defer('index');self.assertEqual(json.loads(h.PENDING.read_text()),{'ocr':True,'index':True});self.assertTrue(h.WAKE.exists());self.assertTrue(h.pending())
 def test_offline_keeps_pending(self):
  h.defer('ocr')
  with patch.object(h,'ensure',side_effect=h.Paused()):
   with self.assertRaises(h.Paused):h.resume_pending()
  self.assertTrue(h.pending())
 def test_busy_keeps_pending(self):
  h.defer('index')
  with patch.object(h,'ensure'),patch.object(h,'request',return_value={'accepting':True,'busy':True}):self.assertTrue(h.resume_pending())
  self.assertTrue(h.pending())
 def test_resume_wakes_real_index_and_clears_only_marker(self):
  h.defer('ocr')
  with patch.object(h,'ensure'),patch.object(h,'request',return_value={'accepting':True,'busy':False}):self.assertFalse(h.resume_pending())
  self.assertFalse(h.pending());self.assertTrue((self.root/'pdf-changes').exists())
 def test_embedding_size_and_dimensions_validated(self):
  raw=b'\0'*1024
  with patch.object(h,'request',return_value={'vectors':base64.b64encode(raw).decode(),'dimensions':256,'count':1}):self.assertEqual(h.Encoder().encode(['test'])[0].astype('float32').tobytes(),raw)
  with patch.object(h,'request',return_value={'vectors':'AA==','dimensions':256,'count':1}):
   with self.assertRaises(ValueError):h.Encoder().encode(['test'])
 def test_source_upload_is_reused(self):
  with patch.object(h,'request',return_value={'ready':True}) as request:
   a=h.source('not-read.pdf','PDF Library/a.pdf','fp');b=h.source('not-read.pdf','PDF Library/a.pdf','fp');self.assertEqual(a,b);self.assertEqual(request.call_count,2)
 def test_no_source_read_after_offline_admission(self):
  with patch.object(h,'config',return_value={'url':'http://127.0.0.1:1','token':'fixture'}),patch.object(h,'ensure',side_effect=h.Paused()):
   with self.assertRaises(h.Paused):h.request('/health',method='GET')
class ControlTests(unittest.TestCase):
 def test_unknown_actions_never_run_command(self):
  with patch.object(control,'run') as run:
   for args in [['exec','helper'],['start','other'],['status',';reboot']]:
    with self.assertRaises(ValueError):control.main(args)
   run.assert_not_called()
 def test_on_gate_prevents_start(self):
  with patch.object(control,'accepting',return_value=False),patch.object(control,'run') as run:
   with self.assertRaises(RuntimeError):control.ensure_docker()
   run.assert_not_called()
class BrokerTests(unittest.TestCase):
 def test_cached_remote_status_never_blocks_local_ui(self):
  broker.CONTROL_CACHE.update(at=0,data={},refreshing=False)
  with patch.object(broker.EXECUTOR,'submit') as submit:
   self.assertEqual(broker.remote_snapshot(),{});self.assertEqual(broker.remote_snapshot(),{});self.assertEqual(submit.call_count,1)
 def test_unknown_remote_commands_rejected_before_ssh(self):
  with patch('subprocess.run') as run:
   with self.assertRaises(broker.ControlError):broker.remote_control('exec helper sh')
   run.assert_not_called()
 def test_remote_off_prevents_start_but_does_not_mutate_local_policy(self):
  with patch.object(broker,'inspect',return_value={'State':{'Status':'exited'},'remote':{'controls_blocked':True}}):
   with self.assertRaises(broker.ControlError):broker.action('cyborg-helper','start')
if __name__=='__main__':unittest.main()
