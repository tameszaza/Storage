import unittest,tempfile,sqlite3,struct,re,hashlib
from pathlib import Path
from unittest.mock import patch
import search
V=struct.pack('<256f',1,*([0]*255))
class SearchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  for name,value in [('DATA',self.root),('PDFS',self.root),('available',lambda:None)]:
   p=patch.object(search,name,value);p.start();self.addCleanup(p.stop)
  self.db=search.connect(True);self.addCleanup(self.db.close)
  for path,kind in [('PDF Library/a.pdf','lecture PDF'),('Stellar birth.md','vault note')]:
   file=self.root/Path(path).name;file.write_bytes(b'%PDF-fixture')
   self.db.execute('INSERT INTO docs(path,fingerprint,kind,indexed_at,status,page_count) VALUES(?,?,?,?,?,?)',(path,search.fingerprint(file),kind,1,'ready',1))
  text='start numOfNodes(t); numOfNodes(t); finish'
  for start,part in [(0,text[:36]),(18,text[18:])]:self.db.execute('INSERT INTO chunks(path,page,start,text,embedding) VALUES(?,1,?,?,?)',('PDF Library/a.pdf',start,part,V))
  self.db.execute("INSERT INTO pages VALUES('PDF Library/a.pdf',1,?,'native',1)",(text,))
  self.db.execute("INSERT INTO chunks(path,page,start,text,embedding) VALUES('Stellar birth.md',-1,0,'Stellar birth',?)",(V,));self.db.execute('DELETE FROM vector_pending');self.db.commit()
 def hits(self,*args):
  i=self.db.execute("SELECT id FROM chunks WHERE page=-1").fetchone()[0]
  return [{'id':i,'path':'Stellar birth.md','hash':hashlib.sha256(V).hexdigest(),'distance':.1}]
 def test_chat_search_does_not_expand_unrequested_occurrence_payload(self):
  r=search.query({'query':'numOfNodes'})
  self.assertNotIn('matches',r['results'][0])
 def test_exact_pdf_count_deduplicates_overlap_and_keeps_fast_path(self):
  with patch.object(search.helper_client,'search') as remote:
   r=search.query({'query':'numOfNodes','filesOnly':True})
  remote.assert_not_called();hit=r['results'][0];self.assertEqual(hit['matchType'],'exact phrase');self.assertEqual(hit['matchCount'],2);self.assertEqual([x['page'] for x in hit['matches']],[1,1]);self.assertTrue(hit['countIsExact'])
 def test_native_supplement_requests_semantics_even_with_literal_hit(self):
  with patch.object(search.helper_client,'enabled',return_value=True),patch.object(search.helper_client,'search',side_effect=self.hits) as remote:
   r=search.query({'query':'numOfNodes','semantic':True,'filesOnly':True})
  remote.assert_called_once();self.assertEqual(r['results'][0]['matchType'],'exact phrase');self.assertEqual(r['results'][1]['matchType'],'semantic filename');self.assertIsNone(r['results'][1]['page']);self.assertEqual(r['results'][1]['textExtraction'],'filename')
 def test_excluded_native_files_do_not_consume_extra_slots(self):
  with patch.object(search.helper_client,'enabled',return_value=True),patch.object(search.helper_client,'search',side_effect=self.hits):
   r=search.query({'query':'numOfNodes','semantic':True,'filesOnly':True,'excludePaths':['PDF Library/a.pdf']})
  self.assertEqual([h['path'] for h in r['results']],['Stellar birth.md'])
 def test_filename_literal_is_not_semantic(self):
  r=search.query({'query':'Stellar birth'});self.assertEqual(r['results'][0]['matchType'],'filename')
 def test_matching_pdf_filename_does_not_hide_body_occurrences(self):
  self.db.execute("INSERT INTO chunks(path,page,start,text,embedding) VALUES('PDF Library/a.pdf',-1,0,'numOfNodes',?)",(V,));self.db.commit()
  result=search.query({'query':'numOfNodes','filesOnly':True})['results'][0]
  self.assertEqual(result['matchType'],'exact phrase');self.assertEqual(result['matchCount'],2)
 def test_sample_cap_keeps_full_count(self):
  self.db.execute("DELETE FROM chunks WHERE path='PDF Library/a.pdf'")
  self.db.execute("INSERT INTO chunks(path,page,start,text,embedding) VALUES('PDF Library/a.pdf',1,0,?,?)",('numOfNodes '*100,V));self.db.commit()
  r=search.query({'query':'numOfNodes','filesOnly':True})['results'][0]
  self.assertEqual(r['matchCount'],100);self.assertEqual(len(r['matches']),48);self.assertTrue(r['matchesTruncated']);self.assertTrue(r['countIsExact'])
 def test_filename_vectors_cached_independently_of_body(self):
  class Vec:
   def astype(self,*a):return self
   def tobytes(self):return V
  class Encoder:
   def encode(self,values,**kw):return [Vec() for _ in values]
  with patch.object(search,'encoder',return_value=Encoder()) as encoder:
   search.index_filenames(self.db);search.index_filenames(self.db)
  encoder.assert_called_once();self.assertEqual(self.db.execute('SELECT count(*) FROM chunks WHERE page=-1').fetchone()[0],2)
 def test_pause_preserves_literal_matches_and_counts(self):
  with patch.object(search.helper_client,'enabled',return_value=True),patch.object(search.helper_client,'search',side_effect=search.helper_client.Paused()):r=search.query({'query':'numOfNodes','semantic':True,'filesOnly':True})
  self.assertEqual(r['results'][0]['matchCount'],2);self.assertIn('unavailable',r['retrieval'])
if __name__=='__main__':unittest.main()
