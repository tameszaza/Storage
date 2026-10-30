import importlib.util,sys,unittest,tempfile,sqlite3,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import search
class ImageTextTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.data=Path(self.tmp.name);self.assets=self.data/'assets';self.assets.mkdir()
  self.name='a'*64+'.jpg';(self.assets/self.name).write_bytes(b'fixture')
  self.db=sqlite3.connect(self.data/'index.sqlite');self.db.execute('CREATE TABLE image_ocr(name TEXT PRIMARY KEY,fingerprint TEXT,policy TEXT,text TEXT,status TEXT)');self.db.executescript('CREATE TABLE image_refs(path TEXT,name TEXT);CREATE TABLE image_notes(path TEXT PRIMARY KEY,signature TEXT,source_fp TEXT);CREATE TABLE docs(path TEXT PRIMARY KEY,fingerprint TEXT);CREATE TABLE chunks(path TEXT,page INTEGER);CREATE TABLE pages(path TEXT,page INTEGER,indexed INTEGER);CREATE TABLE ocr_jobs(path TEXT PRIMARY KEY,fingerprint TEXT);');self.db.commit()
  for attr,val in [('DATA',self.data),('ATTACHMENTS',self.assets),('available',lambda:None)]:p=patch.object(search,attr,val);p.start();self.addCleanup(p.stop)
 def test_recognition_cached_once(self):
  with patch.object(search,'ocr_pasted_image',return_value='Recognized image words') as ocr:
   a=search.image_text({'names':[self.name],'recognize':True})['images'][0]
   b=search.image_text({'names':[self.name],'recognize':True})['images'][0]
   self.assertEqual(a,b);self.assertEqual(a['status'],'ready');self.assertEqual(ocr.call_count,1)
 def test_cache_lookup_never_runs_ocr(self):
  with patch.object(search,'ocr_pasted_image') as ocr:
   self.assertEqual(search.image_text({'names':[self.name]})['images'][0]['status'],'pending');ocr.assert_not_called()
 def test_replaced_image_invalidates_cache(self):
  with patch.object(search,'ocr_pasted_image',side_effect=['first','updated']) as ocr:
   search.image_text({'names':[self.name],'recognize':True});(self.assets/self.name).write_bytes(b'new fixture bytes')
   self.assertEqual(search.image_text({'names':[self.name],'recognize':True})['images'][0]['text'],'updated');self.assertEqual(ocr.call_count,2)
 def test_no_text_cached(self):
  with patch.object(search,'ocr_pasted_image',return_value='  ') as ocr:
   search.image_text({'names':[self.name],'recognize':True});r=search.image_text({'names':[self.name],'recognize':True});self.assertEqual(r['images'][0]['status'],'no-text');self.assertEqual(ocr.call_count,1)
 def test_busy_ocr_is_pending(self):
  import fcntl
  with open(self.data/'ocr.lock','a') as guard:
   fcntl.flock(guard,fcntl.LOCK_EX)
   with patch.object(search,'ocr_pasted_image') as ocr:
    self.assertEqual(search.image_text({'names':[self.name],'recognize':True})['images'][0]['status'],'pending');ocr.assert_not_called()
 def test_missing_symlink_and_traversal(self):
  (self.assets/self.name).unlink();(self.assets/self.name).symlink_to(self.data/'index.sqlite')
  self.assertEqual(search.image_text({'names':[self.name],'recognize':True})['images'][0]['status'],'missing')
  for body in [{'names':['../index.sqlite']},{'names':[self.name]*33},{'names':[self.name,self.name],'recognize':True},{'names':[self.name],'recognize':'yes'}]:
   with self.assertRaises(ValueError):search.image_text(body)
 def test_failure_is_reported_not_empty_success(self):
  with patch.object(search,'ocr_pasted_image',side_effect=RuntimeError('fixture')):
   self.assertEqual(search.image_text({'names':[self.name],'recognize':True})['images'][0]['status'],'error')
 def test_skip_does_not_run_ocr_and_survives_future_requests(self):
  with patch.object(search,'ocr_pasted_image') as ocr:
   r=search.image_text({'names':[self.name],'action':'skip'})
   self.assertEqual(r['images'][0]['status'],'skipped')
   self.assertEqual(search.image_text({'names':[self.name],'recognize':True}),r);ocr.assert_not_called()
 def test_retry_forces_cached_rejection_once(self):
  with patch.object(search,'ocr_pasted_image',return_value=''):
   search.image_text({'names':[self.name],'recognize':True})
  with patch.object(search,'ocr_pasted_image',return_value='faint code') as ocr:
   result=search.image_text({'names':[self.name],'action':'retry','recognize':True})
   self.assertEqual(result['images'][0]['status'],'forced');ocr.assert_called_once_with(self.assets/self.name,force=True)
   self.assertEqual(search.image_text({'names':[self.name],'recognize':True}),result);self.assertEqual(ocr.call_count,1)
 def test_actions_dirty_only_parent_image_passages(self):
  self.db.execute('INSERT INTO image_refs VALUES(?,?)',('note.md',self.name))
  self.db.execute("INSERT INTO docs VALUES('note.md','fp')")
  self.db.execute("INSERT INTO image_notes(path,signature) VALUES('note.md','old')")
  self.db.executemany('INSERT INTO chunks VALUES(?,?)',[('note.md',0),('note.md',1),('other.md',0)])
  self.db.execute("INSERT INTO pages VALUES('note.md',0,1)");self.db.commit()
  search.image_text({'names':[self.name],'action':'skip'})
  self.assertEqual(list(self.db.execute('SELECT * FROM chunks')),[('note.md',1),('other.md',0)])
  self.assertEqual(list(self.db.execute('SELECT * FROM ocr_jobs')),[('note.md','fp')])
  self.assertTrue((self.data/'ocr-pending').exists())
 def test_retry_deferred_preserves_intent(self):
  with patch.object(search,'ocr_pasted_image',side_effect=search.helper_client.Paused),patch.object(search.helper_client,'defer') as defer:
   result=search.image_text({'names':[self.name],'action':'retry','recognize':True})
   self.assertEqual(result['images'][0]['status'],'pending');defer.assert_called_once_with('ocr')
  self.assertEqual(self.db.execute('SELECT status FROM image_ocr').fetchone()[0],'pending-force')
 def test_busy_skip_does_not_claim_it_succeeded(self):
  import fcntl
  with open(self.data/'ocr.lock','a') as guard:
   fcntl.flock(guard,fcntl.LOCK_EX)
   self.assertEqual(search.image_text({'names':[self.name],'action':'skip'})['images'][0]['status'],'busy')
  self.assertEqual(self.db.execute('SELECT count(*) FROM image_ocr').fetchone()[0],0)
 def test_invalid_actions_rejected(self):
  for action in ['exec',True,{},[]]:
   with self.assertRaises(ValueError):search.image_text({'names':[self.name],'action':action})
 def test_explicit_skip_survives_future_policy_refresh(self):
  search.image_text({'names':[self.name],'action':'skip'})
  self.db.execute("UPDATE image_ocr SET policy='previous-policy'")
  self.db.execute("INSERT INTO docs VALUES('note.md','fp')");self.db.commit()
  source={'note.md':{'text':'![](https://server.tailb7f57c.ts.net/obsidian-images/'+self.name+')','kind':'vault note','fp':'fp'}}
  with patch.object(search,'ocr_pasted_image') as ocr:search.sync_image_refs(self.db,source);ocr.assert_not_called()
  self.assertEqual(self.db.execute('SELECT status,policy FROM image_ocr').fetchone(),('skipped',search.PASTED_OCR_POLICY))
 def test_synced_comments_not_embedded_twice(self):
  text='Original prose\n\n<!-- tames-image-ocr:'+self.name+'\nMachine OCR\nrecognized words\n-->'
  self.assertEqual(search.note_text(text,'vault note'),'Original prose')
if __name__=='__main__':unittest.main()
