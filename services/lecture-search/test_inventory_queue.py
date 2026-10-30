import sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import search
class QueueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.file=Path(self.tmp.name)/'source.pdf';self.file.write_bytes(b'%PDF-fixture');self.path='PDF Library/source.pdf';self.fp=search.fingerprint(self.file)
  self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
  self.db.executescript('CREATE TABLE docs(path TEXT,page_count INTEGER);CREATE TABLE raster_scans(path TEXT PRIMARY KEY,fingerprint TEXT,policy TEXT,failed INTEGER);CREATE TABLE pages(path TEXT,page INTEGER,method TEXT,text TEXT);CREATE TABLE ocr_jobs(path TEXT PRIMARY KEY,fingerprint TEXT);')
  self.db.execute('INSERT INTO docs VALUES(?,3)',(self.path,))
  self.db.executemany('INSERT INTO pages VALUES(?,?,?,?)',[(self.path,1,'native','text'),(self.path,2,'native+ocr','already cached'),(self.path,3,'native','text')]);self.db.commit()
 def invoke(self):return search.queue_images(self.db,self.path,{'file':self.file,'fp':self.fp})
 def test_reuses_scan_and_completed_ocr(self):
  with patch.object(search.helper_client,'enabled',return_value=True),patch.object(search.helper_client,'pdf_inventory',return_value={1,2}) as inventory:
   self.assertEqual(self.invoke(),1);self.assertEqual(self.invoke(),0);inventory.assert_called_once()
  self.assertEqual(self.db.execute('SELECT method FROM pages WHERE page=2').fetchone()[0],'native+ocr')
  self.assertEqual(self.db.execute('SELECT failed FROM raster_scans').fetchone()[0],0)
  self.assertEqual(self.db.execute('SELECT count(*) FROM ocr_jobs').fetchone()[0],1)
 def test_paused_helper_does_not_poison_inventory_cache(self):
  with patch.object(search.helper_client,'enabled',return_value=True),patch.object(search.helper_client,'pdf_inventory',side_effect=search.helper_client.Paused):
   with self.assertRaises(search.helper_client.Paused):self.invoke()
  self.assertEqual(self.db.execute('SELECT count(*) FROM raster_scans').fetchone()[0],0)
 def test_changed_source_is_not_published_as_success(self):
  def changed(*a):self.file.write_bytes(b'changed');return {1}
  with patch.object(search.helper_client,'enabled',return_value=True),patch.object(search.helper_client,'pdf_inventory',side_effect=changed):self.invoke()
  self.assertEqual(self.db.execute('SELECT failed FROM raster_scans').fetchone()[0],1)
  self.assertEqual(self.db.execute('SELECT method FROM pages WHERE page=1').fetchone()[0],'native')
if __name__=='__main__':unittest.main()
