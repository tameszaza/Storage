import importlib.util,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
spec=importlib.util.spec_from_file_location('webdav_settings',Path(__file__).with_name('tamestorage_settings.py'));m=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'tamestorage_containers':MagicMock()}):spec.loader.exec_module(m)
class WebDAVSettings(unittest.TestCase):
 def test_native_apache_fields_and_no_legacy_rclone_flags(self):
  self.assertNotIn('goodnotes.cache',m.FIELDS);self.assertNotIn('goodnotes.write_timeout',m.FIELDS)
  self.assertEqual(m.impacts(['goodnotes.read_timeout','goodnotes.workers','goodnotes.log_level'])['containers'],['tamestorage-webdav'])
  self.assertTrue(all(f['source'][0]!='flag' for k,f in m.FIELDS.items() if k.startswith('goodnotes.')))
 def test_read_write_updates_thread_pair_preserves_auth_mounts_and_checks_revision(self):
  with tempfile.TemporaryDirectory() as d,patch.object(m,'BASE',Path(d)):
   p=Path(d)/'webdav/httpd.conf';p.parent.mkdir();p.write_text('AuthUserFile /config/htpasswd\nTimeout 3600\n<IfModule mpm_event_module>\n ThreadsPerChild 8\n MaxSpareThreads 8\n MaxRequestWorkers 8\n</IfModule>\nLogLevel warn\n')
   snap=object.__new__(m.Snapshot);snap.paths=set();snap.docs={};snap.values={};snap.environment={};snap.containers={}
   self.assertEqual(snap.read(m.FIELDS['goodnotes.read_timeout']),3600);self.assertIn(p,snap.paths)
   writes=m.make_writes(snap,{'goodnotes.read_timeout':120,'goodnotes.workers':4,'goodnotes.log_level':'error'});self.assertEqual(list(writes),[p]);s=writes[p].decode()
   self.assertIn('Timeout 120\n',s);self.assertIn('ThreadsPerChild 4\n',s);self.assertIn('MaxRequestWorkers 4\n',s);self.assertIn('LogLevel error\n',s);self.assertIn('AuthUserFile /config/htpasswd\n',s);self.assertNotIn('--',s)
 def test_rejects_unsafe_values(self):
  for key,value in [('goodnotes.workers',1),('goodnotes.read_timeout',-1),('goodnotes.log_level','warn\nAuthType None')]:
   with self.assertRaises(m.SettingsError):m.validate_value(m.FIELDS[key],value)
