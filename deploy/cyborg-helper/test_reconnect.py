import json,sqlite3,stat,tempfile,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import search,searchable_pdf as p
class ReconnectTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.file=self.root/'source.pdf';self.file.write_bytes(b'%PDF-original')
  self.fp=p.fingerprint(self.file);self.old='999999:'+self.fp.split(':',1)[1]
  self.device=Mock();self.device.stat.return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK,st_rdev=self.file.stat().st_dev)
 def test_verified_uuid_adopts_only_device_number_change(self):
  with patch.object(p,'PRIMARY_DEVICE',self.device):self.assertTrue(p.same_source_after_reconnect(self.file,self.old,self.fp))
 def test_changes_to_inode_size_or_mtime_are_never_adopted(self):
  with patch.object(p,'PRIMARY_DEVICE',self.device):
   for index in [1,2,3]:
    pieces=self.old.split(':');pieces[index]=str(int(pieces[index])+1)
    self.assertFalse(p.same_source_after_reconnect(self.file,':'.join(pieces),self.fp))
 def test_other_or_unavailable_volume_never_reuses_identity(self):
  self.device.stat.return_value.st_rdev=self.file.stat().st_dev+1
  with patch.object(p,'PRIMARY_DEVICE',self.device):self.assertFalse(p.same_source_after_reconnect(self.file,self.old,self.fp))
  self.device.stat.side_effect=FileNotFoundError()
  with patch.object(p,'PRIMARY_DEVICE',self.device):self.assertFalse(p.same_source_after_reconnect(self.file,self.old,self.fp))
 def test_derived_layers_manifest_and_checkpoints_survive_adoption(self):
  with patch.object(p,'ROOT',self.root/'cache'):
   directory=p.prepare('PDF Library/source.pdf',self.old)
   (directory/'1.pdf').write_bytes(b'%PDF-unchanged-layer');(directory/'1.json').write_text('{"status":"ready","text":"cached"}')
   manifest={'path':'PDF Library/source.pdf','fingerprint':self.old,'policy':p.POLICY,'status':'ready'}
   (directory/'manifest.json').write_text(json.dumps(manifest));(directory/'document.pdf').write_bytes(b'%PDF-unchanged-document')
   with patch.object(p,'same_source_after_reconnect',return_value=True):p.prepare('PDF Library/source.pdf',self.fp)
   self.assertEqual((directory/'1.pdf').read_bytes(),b'%PDF-unchanged-layer');self.assertEqual((directory/'document.pdf').read_bytes(),b'%PDF-unchanged-document')
   self.assertEqual(json.loads((directory/'1.json').read_text())['text'],'cached')
   self.assertEqual(json.loads((directory/'source.json').read_text())['fingerprint'],self.fp);self.assertEqual(json.loads((directory/'manifest.json').read_text())['fingerprint'],self.fp)
 def test_real_source_change_invalidates_derived_cache(self):
  with patch.object(p,'ROOT',self.root/'cache'):
   directory=p.prepare('PDF Library/source.pdf',self.old);(directory/'1.pdf').write_bytes(b'old')
   with patch.object(p,'same_source_after_reconnect',return_value=False):p.prepare('PDF Library/source.pdf',self.fp)
   self.assertFalse((directory/'1.pdf').exists())
 def test_cache_symlink_never_adopted(self):
  with patch.object(p,'ROOT',self.root/'cache'):
   directory=p.folder('PDF Library/source.pdf');directory.parent.mkdir();directory.symlink_to(self.root,target_is_directory=True)
   with self.assertRaises(ValueError):p.prepare('PDF Library/source.pdf',self.fp)
 def test_metadata_adoption_preserves_text_vectors_and_ocr_queue(self):
  db=sqlite3.connect(':memory:');self.addCleanup(db.close)
  for table in ['docs','ocr_jobs','raster_scans']:db.execute('CREATE TABLE '+table+'(path TEXT,fingerprint TEXT)');db.execute('INSERT INTO '+table+' VALUES(?,?)',('PDF Library/source.pdf',self.old))
  db.execute('CREATE TABLE pages(text TEXT)');db.execute("INSERT INTO pages VALUES('cached OCR words')");db.execute('CREATE TABLE chunks(embedding BLOB)');db.execute("INSERT INTO chunks VALUES(X'010203')");db.commit()
  current={'PDF Library/source.pdf':{'file':self.file,'fp':self.fp}};old={'PDF Library/source.pdf':self.old}
  with patch.object(p,'PRIMARY_DEVICE',self.device),patch.object(p,'ROOT',self.root/'cache'):search.adopt_reconnected_sources(db,current,old)
  for table in ['docs','ocr_jobs','raster_scans']:self.assertEqual(db.execute('SELECT fingerprint FROM '+table).fetchone()[0],self.fp)
  self.assertEqual(db.execute('SELECT text FROM pages').fetchone()[0],'cached OCR words');self.assertEqual(db.execute('SELECT embedding FROM chunks').fetchone()[0],b'\1\2\3');self.assertEqual(old['PDF Library/source.pdf'],self.fp)
if __name__=='__main__':unittest.main()
