import tempfile,unittest
from pathlib import Path
from atomic_upload import save_atomic
class UploadTests(unittest.TestCase):
 def test_interrupted_upload_preserves_original_and_removes_stage(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'lecture.pdf';p.write_bytes(b'original')
   class Broken:
    def save(self,out):out.write(b'partial');raise OSError('interrupted')
   with self.assertRaises(OSError):save_atomic(Broken(),p)
   self.assertEqual(p.read_bytes(),b'original');self.assertEqual(list(Path(d).iterdir()),[p])
 def test_complete_upload_publishes_only_after_save_and_versions_old_content(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'lecture.pdf';p.write_bytes(b'original');seen=[]
   class Complete:
    def save(self,out):out.write(b'complete');seen.append(p.read_bytes())
   save_atomic(Complete(),p,lambda:seen.append(p.read_bytes()))
   self.assertEqual(seen,[b'original',b'original']);self.assertEqual(p.read_bytes(),b'complete');self.assertEqual(list(Path(d).iterdir()),[p])
 def test_concurrent_destination_change_is_kept(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'lecture.pdf';p.write_bytes(b'original')
   class Concurrent:
    def save(self,out):out.write(b'complete');p.write_bytes(b'newer-export')
   with self.assertRaises(OSError):save_atomic(Concurrent(),p)
   self.assertEqual(p.read_bytes(),b'newer-export')
