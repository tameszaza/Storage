import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('reconciler',Path(__file__).with_name('cleanup.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Presence(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.pdfs=self.root/'Goodnotes';self.pdfs.mkdir();self.state=self.root/'state';self.state.mkdir()
  self.patches=[patch.object(m,'ROOT',self.pdfs),patch.object(m,'STATE',self.state),patch.object(m,'LEDGER',self.state/'ledger.json'),patch.object(m.os.path,'ismount',return_value=True)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def rec(self,data):
  (self.state/'ledger.json').write_text(json.dumps({'version':1,'pdfs':data,'archives':{},'jobs':[]}));return m.Reconciler()
 def test_version_change_keeps_presence_and_sticky_manual_protection(self):
  p=self.pdfs/'a.pdf';p.write_bytes(b'old');r=self.rec({'a.pdf':{'fp':m.fingerprint(p.stat()),'ipad_fp':[1,2,3,4],'ipad_present':True,'explicit':True}})
  p.write_bytes(b'new');r.observe_path(p);r.save()
  self.assertTrue(r.state['pdfs']['a.pdf']['explicit']);self.assertEqual(json.loads((self.state/'ipad-import-status.json').read_text())['files'],[])
 def test_known_absence_beats_old_fingerprint(self):
  r=self.rec({});self.assertFalse(r.presence('a.pdf',{'ipad_present':False,'ipad_fp':[1,2,3,4]},[1,2,3,4]))
 def test_conservative_migration_and_idempotence(self):
  (self.state/'cleanup-last.json').write_text(json.dumps({'notebooks':2,'preserved':['absent.pdf']}))
  for name in ['known.pdf','absent.pdf','uncertain.pdf']:(self.pdfs/name).write_bytes(b'%PDF-')
  r=self.rec({'known.pdf':{'ipad_fp':[1,2,3,4]},'absent.pdf':{'ipad_fp':[1,2,3,4]},'uncertain.pdf':{}})
  data=json.loads((self.state/'ipad-import-status.json').read_text());self.assertEqual({v['path']:v['confidence'] for v in data['files']},{'absent.pdf':'missing','uncertain.pdf':'unconfirmed'})
  self.assertFalse(r.state['latest_inventory']['complete']);old=(self.state/'ledger.json').read_bytes();m.Reconciler();self.assertEqual(old,(self.state/'ledger.json').read_bytes())
 def test_future_complete_snapshot_respects_uploads_after_snapshot(self):
  r=self.rec({});r.state['latest_inventory']={'complete':True,'paths':['known.pdf'],'source_mtime_ns':10}
  self.assertTrue(r.presence('known.pdf',{},[1,2,3,20]));self.assertFalse(r.presence('old.pdf',{},[1,2,3,5]));self.assertIsNone(r.presence('new.pdf',{},[1,2,3,20]))
if __name__=='__main__':unittest.main()
