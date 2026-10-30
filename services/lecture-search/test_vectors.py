"""Derived SQLite mirror: atomic updates, durable dirty state, existing cosine ranking."""
import base64,importlib.util,json,os,sqlite3,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import helper_client as h
spec=importlib.util.spec_from_file_location('helper_worker',Path(__file__).with_name('worker.py'));w=importlib.util.module_from_spec(spec)
with patch.object(Path,'read_text',return_value='{"token":"fixture"}'),patch.dict(os.environ):spec.loader.exec_module(w)
V=struct.pack('<256f',1,*([0]*255));B=base64.b64encode(V).decode();E='a'*32
class MirrorTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.patches=[patch.object(w,'WORK',self.root),patch.object(w,'admitted',return_value=True),patch.object(h,'DATA',self.root)]
  for p in self.patches:p.start();self.addCleanup(p.stop)
 def upload(self,path,rows,epoch=E):
  f=self.root/'fixture.jsonl';f.write_text(json.dumps({'path':path,'epoch':epoch})+'\n'+''.join(json.dumps(r)+'\n' for r in rows));return w.vectors(f)
 def test_replace_is_atomic_and_preserves_other_document(self):
  self.upload('Academic/a.md',[[1,B]]);self.upload('Academic/b.md',[[2,B]])
  with self.assertRaises(ValueError):self.upload('Academic/a.md',[[3,'AA==']])
  with w.vector_db() as db:self.assertEqual(db.execute('select count(*) from chunks').fetchone()[0],2)
  self.upload('Academic/a.md',[[4,B]])
  with w.vector_db() as db:self.assertEqual(db.execute('select id from chunks order by id').fetchall(),[(2,),(4,)])
 def test_deleted_document_and_reset_epoch(self):
  self.upload('Academic/a.md',[[1,B]]);self.upload('Academic/a.md',[])
  with w.vector_db() as db:self.assertEqual(db.execute('select count(*) from chunks').fetchone()[0],0)
  self.upload('Academic/b.md',[[2,B]]);self.upload('Academic/a.md',[[1,B]],epoch='b'*32)
  with w.vector_db() as db:self.assertEqual(db.execute('select id from chunks').fetchall(),[(1,)])
 def test_existing_cosine_and_folder_filter(self):
  self.upload('Academic/a.md',[[1,B]]);self.upload('Private/b.md',[[2,B]])
  with patch.object(w,'embed',return_value={'vectors':B}):r=w.semantic_search({'query':'fixture','folder':'Academic/','epoch':E})
  self.assertEqual(len(r['hits']),1);self.assertEqual(r['hits'][0]['id'],1);self.assertAlmostEqual(r['hits'][0]['distance'],0);self.assertEqual(len(r['hits'][0]['hash']),64)
  with self.assertRaises(InterruptedError):w.semantic_search({'query':'fixture','epoch':'missing'})
 def test_filename_candidate_budget_and_native_exclusions(self):
  self.upload('Academic/body.md',[[i,B] for i in range(1,41)])
  self.upload('Academic/title.md',[[999,B]])
  with patch.object(w,'embed',return_value={'vectors':B}):
   hits=w.semantic_search({'query':'fixture','folder':'Academic/','epoch':E,'filenameIds':[999]})['hits']
   self.assertIn(999,[r['id'] for r in hits]);self.assertLessEqual(len(hits),32)
   hits=w.semantic_search({'query':'fixture','epoch':E,'filenameIds':[999],'excludePaths':['Academic/body.md']})['hits']
   self.assertEqual([r['id'] for r in hits],[999])
  with patch.object(w,'embed') as embed:
   with self.assertRaises(ValueError):w.semantic_search({'query':'fixture','epoch':E,'filenameIds':['invalid']})
   embed.assert_not_called()
 def database(self):
  db=sqlite3.connect(':memory:');db.executescript('create table meta(key text,value text);create table docs(path text);create table chunks(id integer,path text,embedding blob,page integer);create table vector_pending(path text primary key,version integer);');db.execute('insert into meta values(?,?)',('vector_epoch',E));db.execute('insert into docs values(?)',('Academic/a.md',));db.execute('insert into chunks(id,path,embedding) values(?,?,?)',(1,'Academic/a.md',V));db.execute('insert into vector_pending values(?,?)',('Academic/a.md',1));db.commit();return db
 def test_dirty_files_are_excluded_without_blocking_synced_files(self):
  db=self.database()
  with patch.object(h,'defer') as defer,patch.object(h,'request',return_value={'hits':[]}) as request:
   self.assertEqual(h.search(db,'fixture',''),[])
   defer.assert_called_once_with('index');self.assertEqual(request.call_args.args[1]['excludePaths'],['Academic/a.md'])
 def test_large_unsynced_migration_defers_instead_of_searching_stale(self):
  db=self.database();db.executemany('insert into vector_pending values(?,1)',[(f'Academic/{i}.md',) for i in range(101)]);db.commit()
  with patch.object(h,'defer'),patch.object(h,'request') as request:
   with self.assertRaises(h.Paused):h.search(db,'fixture','')
   request.assert_not_called()
 def test_busy_search_does_not_schedule_unnecessary_reindex(self):
  db=self.database();db.execute('delete from vector_pending');db.commit()
  with patch.object(h,'defer') as defer,patch.object(h,'request',side_effect=[h.Paused(),{'vector_epoch':E,'busy':True}]):
   with self.assertRaises(h.Paused):h.search(db,'fixture','')
   defer.assert_not_called()
 def test_offline_search_only_queues_a_wake(self):
  db=self.database();db.execute('delete from vector_pending');db.commit()
  with patch.object(h,'defer') as defer,patch.object(h,'request',side_effect=h.Paused()):
   with self.assertRaises(h.Paused):h.search(db,'fixture','')
   defer.assert_called_once_with('wake')
 def test_incremental_sync_does_not_lose_concurrent_edit(self):
  db=self.database();sent=[]
  def request(route,**kwargs):
   if route=='/health':return {'accepting':True,'busy':False,'vector_epoch':E}
   sent.append(Path(kwargs['file']).read_text())
   if len(sent)==1:db.execute('update vector_pending set version=2');db.commit()
   return {'count':1}
  with patch.object(h,'enabled',return_value=True),patch.object(h,'request',side_effect=request):h.sync_vectors(db)
  self.assertEqual(len(sent),2);self.assertIsNone(db.execute('select 1 from vector_pending').fetchone())
 def test_paused_sync_preserves_pending_and_cleans_temp(self):
  db=self.database()
  with patch.object(h,'enabled',return_value=True),patch.object(h,'request',side_effect=[{'accepting':True,'busy':False,'vector_epoch':E},h.Paused()]):
   with self.assertRaises(h.Paused):h.sync_vectors(db)
  self.assertTrue(db.execute('select 1 from vector_pending').fetchone());self.assertFalse(list(self.root.glob('helper-vectors-*')))

class KeywordTests(unittest.TestCase):
 def setUp(self):
  import search
  self.search=search;self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
  self.db.executescript('create table chunks(id integer primary key,path text,text text);create virtual table keywords using fts5(text,content="chunks",content_rowid="id");')
  for i,path,text in [(1,'Academic/a.md','binary recursive tree'),(2,'Academic/b.md','recursive binary search'),(3,'Other/c.md','recursive recursive recursive'),(4,'Academic/d.md','clock rising edge')]:
   self.db.execute('insert into chunks(id,path,text) values(?,?,?)',(i,path,text));self.db.execute('insert into keywords(rowid,text) values(?,?)',(i,text))
 def test_phrase_and_all_words_preserved(self):
  self.assertEqual(self.search.keyword_ids(self.db,'"binary recursive"','%'),[1]);self.assertEqual(set(self.search.keyword_ids(self.db,'"binary" AND "recursive"','%')),{1,2})
 def test_folder_and_escaped_literal_paths(self):
  self.assertEqual(set(self.search.keyword_ids(self.db,'recursive','Academic/%')),{1,2});self.assertEqual(self.search.keyword_ids(self.db,'recursive','Missing/%'),[])


class StatusTests(unittest.TestCase):
 def setUp(self):
  import search
  self.search=search;self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  for name,value in [('DATA',Path(self.tmp.name)),('available',lambda:None)]:
   p=patch.object(search,name,value);p.start();self.addCleanup(p.stop)
  self.summary=patch.object(search.searchable_pdf,'summary',return_value={'ready':1,'pending':0,'failed':0,'unchanged':0});self.summary.start();self.addCleanup(self.summary.stop)
  with search.connect(True) as db:db.execute("insert into docs(path,fingerprint,kind,indexed_at,status) values('Academic/a.md','fixture','note',1,'ready')");db.commit()
 def test_snapshot_skips_cache_walk_and_keeps_live_queue_counts(self):
  a=self.search.status(refresh=True)
  with self.search.connect() as db:db.execute("insert into ocr_jobs values('Academic/a.md','fixture')");db.commit()
  with patch.object(self.search.searchable_pdf,'summary',side_effect=AssertionError('Unnecessary cache walk')):b=self.search.status()
  self.assertEqual(b['documents'],1);self.assertEqual(b['searchablePDFs'],a['searchablePDFs']);self.assertEqual(b['ocrQueuedFiles'],1)
 def test_offline_does_not_report_cached_ready_state(self):
  self.search.status(refresh=True)
  with patch.object(self.search,'available',side_effect=RuntimeError('NAS unavailable')):
   with self.assertRaises(RuntimeError):self.search.status()

if __name__=="__main__":unittest.main()
