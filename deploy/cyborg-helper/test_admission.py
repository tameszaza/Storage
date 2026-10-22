import importlib.util,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
spec=importlib.util.spec_from_file_location('admission',Path(__file__).with_name('admission.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
class AdmissionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.state=Path(self.tmp.name)/'state.json'
  self.p=patch.object(a,'STATE',self.state);self.p.start();self.addCleanup(self.p.stop)
 def test_on_without_resume_marker_starts_ai(self):
  with patch.object(a,'helper_control') as helper,patch.object(a.subprocess,'run',return_value=Mock(returncode=0)) as run:
   a.change('on');self.assertTrue(a.accepting());helper.assert_called_once_with('resume')
   self.assertEqual(run.call_args.args[0],['/usr/bin/systemctl','--user','start','--no-block','cyborg-grammar.service'])
 def test_on_removes_legacy_marker(self):
  marker=self.state.parent/'resume-grammar';marker.touch()
  with patch.object(a,'helper_control'),patch.object(a.subprocess,'run',return_value=Mock(returncode=0)):
   a.change('on');self.assertFalse(marker.exists())
 def test_helper_failure_does_not_block_ai_start(self):
  with patch.object(a,'helper_control',side_effect=RuntimeError('fixture')),patch.object(a.subprocess,'run',return_value=Mock(returncode=0)) as run:
   with self.assertRaisesRegex(RuntimeError,'helpers need a refresh'):a.change('on')
   run.assert_called_once();self.assertTrue(a.accepting())
 def test_off_blocks_admission_before_stopping_services(self):
  with patch.object(a,'quiesce',side_effect=lambda:self.assertFalse(a.accepting())) as stop:
   a.change('off');stop.assert_called_once()
 def test_start_failure_is_reported(self):
  with patch.object(a,'helper_control'),patch.object(a.subprocess,'run',return_value=Mock(returncode=1)):
   with self.assertRaisesRegex(RuntimeError,'could not start'):a.change('on')
if __name__=='__main__':unittest.main()
