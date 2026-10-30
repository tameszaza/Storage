import hashlib,json,os,pathlib,tempfile,unittest
from unittest.mock import patch
import pymupdf
import analyze,settings,store,service

class Geometry(unittest.TestCase):
 def test_ink_paths_and_source_unchanged(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'fixture.pdf';doc=pymupdf.open();page=doc.new_page();shape=page.new_shape();shape.draw_line((10,10),(82,10));shape.finish(color=(1,0,0),width=1,lineCap=1,lineJoin=1,closePath=False);shape.commit();page.insert_text((10,60),'Printed text');doc.set_metadata({'producer':'Quartz PDFContext'});doc.save(p);doc.close()
   original=hashlib.sha256(p.read_bytes()).digest();r=analyze.analyze(p,1)
   self.assertEqual(r['estimated_strokes'],1);self.assertAlmostEqual(r['length_pt'],72);self.assertEqual(r['colors'],{'#ff0000':1});self.assertEqual(hashlib.sha256(p.read_bytes()).digest(),original)
 def test_cubic_length(self):
  doc=pymupdf.open();p=doc.new_page();s=p.new_shape();s.draw_bezier((0,0),(24,0),(48,0),(72,0));s.finish(color=(0,0,1),width=1,lineCap=1,lineJoin=1,closePath=False);s.commit();r=analyze.page_stats(p,True);self.assertEqual(r['estimated_strokes'],1);self.assertAlmostEqual(r['length_pt'],72);doc.close()
 def test_no_printed_rectangles_or_nonquartz_vectors(self):
  doc=pymupdf.open();p=doc.new_page();p.draw_rect((10,10,50,50),color=(0,0,0),width=1);p.draw_line((0,0),(72,0),color=(1,0,0),width=1);p.insert_text((10,70),'text')
  self.assertEqual(analyze.page_stats(p,True)['estimated_strokes'],0);self.assertEqual(analyze.page_stats(p,False)['estimated_strokes'],0);doc.close()
 def test_native_ink(self):
  doc=pymupdf.open();p=doc.new_page();a=p.add_ink_annot([[(10,10),(82,10)],[(0,20),(0,56)]]);a.update();r=analyze.page_stats(p,False);self.assertEqual(r['recorded_strokes'],2);self.assertAlmostEqual(r['length_pt'],108);doc.close()

class Cache(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name);self.library=self.root/'library';self.library.mkdir();self.patch=patch.multiple(settings,ROOT=self.library,STATE=self.root/'state');self.patch.start();store.initialize()
 def tearDown(self):self.patch.stop();self.temp.cleanup()
 def test_reuse_changes_deletion_and_path_safety(self):
  p=self.library/'Note.pdf';p.write_bytes(b'one');os.utime(p,(0,0));store.inventory()
  with store.connect() as db:db.execute("INSERT INTO pages VALUES('Note.pdf',1,?)",(json.dumps({'estimated_strokes':3,'length_pt':72}),));db.execute("UPDATE files SET total=1 WHERE path='Note.pdf'")
  store.finish('Note.pdf');store.inventory();self.assertEqual(store.summary()['estimated_strokes'],3)
  p.write_bytes(b'different');os.utime(p,(0,0));store.inventory();self.assertEqual(store.summary()['pending_files'],1);self.assertEqual(store.summary()['estimated_strokes'],0)
  for bad in ('../file.pdf','/file.pdf','folder/../file.pdf'):self.assertRaises(ValueError,store.safe_file,bad)
  p.unlink();store.inventory();self.assertEqual(store.summary()['files'],0)
 def test_missing_library_preserves_cache(self):
  p=self.library/'Note.pdf';p.write_bytes(b'x');os.utime(p,(0,0));store.inventory();p.unlink();self.library.rmdir();self.assertRaises(OSError,store.inventory)
  with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM files').fetchone()[0],1)
 def test_paused_worker_does_not_analyze(self):
  store.set_setting('paused',True)
  with patch.object(service,'analyze',side_effect=AssertionError):self.assertFalse(service.step())
 def test_critical_job_preempts(self):
  p=self.library/'Note.pdf';p.write_bytes(b'x');os.utime(p,(0,0))
  with patch.object(service,'critical_busy',return_value='Waiting for OCR'),patch.object(service.subprocess,'Popen',side_effect=AssertionError):
   self.assertFalse(service.step())

if __name__=='__main__':unittest.main()
