import io,json,tempfile,unittest,subprocess,hashlib
from pathlib import Path
from unittest.mock import patch
import pdf_inventory as inventory
import helper_client
HEADER='page num type width height color comp bpc enc interp object ID x-ppi y-ppi size ratio\n'
class InventoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.file=self.root/'source.pdf';self.file.write_bytes(b'%PDF-fixture')
 def runner(self,args,timeout,log,stderr):
  self.assertEqual(stderr,subprocess.DEVNULL)
  first,last=int(args[2]),int(args[4]);log.write((HEADER+f'{first} 0 image 500 400 rgb 3 8 image no 1 0 72 72 1K 1%\n').encode())
 def test_bounded_checkpoint_resume_and_completed_cache(self):
  first=inventory.scan(self.file,130,self.runner,self.root);self.assertEqual(first['through'],64);self.assertFalse(first['complete'])
  with self.assertRaises(InterruptedError):inventory.scan(self.file,130,lambda *a,**kw:(_ for _ in ()).throw(InterruptedError()),self.root)
  self.assertEqual(json.loads(self.file.with_suffix('.inventory.json').read_text())['through'],64)
  second=inventory.scan(self.file,130,self.runner,self.root);self.assertEqual(second['through'],128)
  final=inventory.scan(self.file,130,self.runner,self.root);self.assertEqual(final['pages'],[1,65,129]);self.assertTrue(final['complete'])
  final=inventory.scan(self.file,130,lambda *a,**kw:self.fail('Completed inventory repeated'),self.root);self.assertTrue(final['complete'])
 def test_changed_source_invalidates_checkpoint(self):
  inventory.scan(self.file,130,self.runner,self.root);self.file.write_bytes(b'%PDF-different')
  result=inventory.scan(self.file,130,self.runner,self.root);self.assertEqual(result['through'],64)
 def test_header_small_images_masks_and_page_validation(self):
  result=inventory.parse_inventory(io.StringIO(HEADER+'1 0 image 10 10\n2 1 smask 1000 1000\n3 2 image 500 400\n'),1,3);self.assertEqual(result,[3])
  for body in ['','1 0 image 500 400\n',HEADER+'4 0 image 500 400\n',HEADER+'1 0\n']:
   with self.assertRaises(ValueError):inventory.parse_inventory(io.StringIO(body),1,3)
 def test_failed_scan_does_not_publish_checkpoint(self):
  def broken(args,timeout,log,stderr):log.write(b'incomplete')
  with self.assertRaises(ValueError):inventory.scan(self.file,10,broken,self.root)
  self.assertFalse(self.file.with_suffix('.inventory.json').exists())
 def test_client_validates_progress_and_reuses_source(self):
  results=[{'pages':[1],'through':64,'total':130,'complete':False},{'pages':[1,129],'through':130,'total':130,'complete':True}]
  with patch.object(helper_client,'source',return_value='key') as source,patch.object(helper_client,'request',side_effect=results):
   self.assertEqual(helper_client.pdf_inventory('a','b','fp',130),{1,129});source.assert_called_once()
  with patch.object(helper_client,'source',return_value='key'),patch.object(helper_client,'request',return_value={'pages':[],'through':0,'total':130,'complete':False}):
   with self.assertRaises(ValueError):helper_client.pdf_inventory('a','b','fp',130)
 def test_real_poppler_inventory_preserves_source(self):
  import pikepdf
  with pikepdf.Pdf.new() as pdf:
   for _ in range(65):pdf.add_blank_page(page_size=(100,100))
   image=pdf.make_stream(b'\xff'*400*300*3);image.Type=pikepdf.Name.XObject;image.Subtype=pikepdf.Name.Image;image.Width=400;image.Height=300;image.ColorSpace=pikepdf.Name.DeviceRGB;image.BitsPerComponent=8
   form=pdf.make_stream(b'/Im0 Do');form.Type=pikepdf.Name.XObject;form.Subtype=pikepdf.Name.Form;form.BBox=pikepdf.Array([0,0,100,100]);form.Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
   page=pdf.pages[64];page.Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Form0=form));page.Contents=pdf.make_stream(b'/Form0 Do')
   pdf.save(self.file)
  digest=hashlib.sha256(self.file.read_bytes()).digest()
  def real(args,timeout,log,stderr):subprocess.run(args,stdout=log,stderr=stderr,timeout=timeout,check=True)
  inventory.scan(self.file,65,real,self.root);result=inventory.scan(self.file,65,real,self.root)
  self.assertEqual(result['pages'],[65]);self.assertTrue(result['complete']);self.assertEqual(hashlib.sha256(self.file.read_bytes()).digest(),digest)
if __name__=='__main__':unittest.main()
