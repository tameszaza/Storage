import ast,base64,importlib.util,json,sqlite3,sys,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import helper_client as h
import search,searchable_pdf as pdf
class FallbackTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  for obj,name,value in [(h,'DATA',self.root),(h,'PENDING',self.root/'pending.json'),(h,'WAKE',self.root/'wake'),(h,'_BACKGROUND',{'until':0.,'ready':False})]:
   p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
 def test_offline_work_runs_locally_once_without_starting_cyborg(self):
  with patch.object(h,'enabled',return_value=True),patch.object(h,'request',side_effect=h.Paused()) as request,patch.object(h,'ensure') as ensure:
   local=Mock(return_value='local');remote=Mock()
   self.assertEqual(h.background_call(remote,local),'local');self.assertEqual(h.background_call(remote,local),'local')
   self.assertEqual(local.call_count,2);request.assert_called_once();ensure.assert_not_called();remote.assert_not_called()
 def test_busy_is_local_and_admission_off_is_local(self):
  for health in [{'accepting':True,'busy':True},{'accepting':False,'busy':False}]:
   h._BACKGROUND['until']=0
   with patch.object(h,'enabled',return_value=True),patch.object(h,'request',return_value=health),patch.object(h,'ensure') as ensure:
    self.assertEqual(h.background_call(lambda:self.fail('remote job'),lambda:'local'),'local');ensure.assert_not_called()
 def test_remote_success_keeps_local_idle(self):
  with patch.object(h,'background_remote',return_value=True):
   local=Mock();self.assertEqual(h.background_call(lambda:'remote',local),'remote');local.assert_not_called()
 def test_mid_job_off_falls_back_and_releases_local_lock(self):
  with patch.object(h,'background_remote',return_value=True):
   self.assertEqual(h.background_call(Mock(side_effect=h.Paused()),lambda:'recovered'),'recovered')
   self.assertFalse(h._BACKGROUND['ready'])
  self.assertEqual(h.local_call(lambda:2),2)
 def test_local_contention_defers_instead_of_holding_another_worker(self):
  import fcntl
  with (self.root/'fallback.lock').open('a') as guard:
   fcntl.flock(guard,fcntl.LOCK_EX)
   with self.assertRaises(h.Paused):h.local_call(lambda:self.fail('overlapping local task'))
  self.assertEqual(h.local_call(lambda:3),3)
 def test_protocol_error_is_not_silently_hidden(self):
  with patch.object(h,'background_remote',return_value=True):
   local=Mock()
   with self.assertRaises(ValueError):h.background_call(Mock(side_effect=ValueError()),local)
   local.assert_not_called()
 def test_background_encoder_uses_existing_model_only_when_needed(self):
  fake=Mock();fake.encode.return_value=['vector'];model=Mock();model.from_pretrained.return_value=fake
  with patch.dict(sys.modules,{'model2vec':types.SimpleNamespace(StaticModel=model)}),patch.object(h,'background_remote',return_value=False):
   encoder=h.Encoder();self.assertEqual(encoder.encode(['one']),['vector']);encoder.encode(['two'])
  model.from_pretrained.assert_called_once_with(str(self.root/'models'));self.assertEqual(fake.encode.call_count,2)
 def test_interactive_semantics_never_loads_local_model(self):
  with patch.object(h,'request',side_effect=h.Paused()),patch.object(h,'background_call') as fallback:
   with self.assertRaises(h.Paused):h.Encoder(interactive=True).encode(['query'])
   fallback.assert_not_called()
 def test_remote_embedding_shape_and_original_vectors_retained(self):
  raw=b'\0'*1024
  with patch.object(h,'background_remote',return_value=True),patch.object(h,'request',return_value={'vectors':base64.b64encode(raw).decode(),'dimensions':256,'count':1}):
   self.assertEqual(h.Encoder().encode(['one'])[0].astype('float32').tobytes(),raw)
 def test_offline_mirror_is_durable_and_does_not_block_primary_work(self):
  db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.execute('CREATE TABLE vector_pending(path TEXT,version INTEGER)');db.execute("INSERT INTO vector_pending VALUES('n.md',1)")
  with patch.object(h,'background_remote',return_value=False):h.sync_vectors(db)
  self.assertEqual(db.execute('SELECT count(*) FROM vector_pending').fetchone()[0],1);self.assertEqual(json.loads(h.PENDING.read_text()),{'vectors':True})
 def test_resume_primary_offline_without_clearing_vector_debt(self):
  h.defer('ocr');h.defer('index');h.defer('vectors')
  with patch.object(h,'background_remote',return_value=False),patch.object(h,'ensure') as ensure:self.assertTrue(h.resume_pending());ensure.assert_not_called()
  self.assertTrue((self.root/'pdf-changes').exists());self.assertEqual(json.loads(h.PENDING.read_text()),{'vectors':True})
 def test_busy_local_slot_keeps_primary_flags_without_retriggering_index(self):
  import fcntl
  h.defer('index');h.defer('ocr')
  with (self.root/'fallback.lock').open('a') as guard:
   fcntl.flock(guard,fcntl.LOCK_EX)
   with patch.object(h,'background_remote',return_value=False):self.assertTrue(h.resume_pending())
  self.assertFalse((self.root/'pdf-changes').exists());self.assertEqual(json.loads(h.PENDING.read_text()),{'index':True,'ocr':True})
  with patch.object(h,'background_remote',return_value=False):self.assertFalse(h.resume_pending())
  self.assertTrue((self.root/'pdf-changes').exists());self.assertFalse(h.pending())
 def test_vector_recovery_wakes_existing_indexer(self):
  h.defer('vectors')
  with patch.object(h,'background_remote',return_value=True):self.assertFalse(h.resume_pending())
  self.assertFalse(h.PENDING.exists());self.assertTrue((self.root/'pdf-changes').exists())
 def test_no_vector_updates_means_no_probe(self):
  db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.execute('CREATE TABLE vector_pending(path TEXT,version INTEGER)')
  with patch.object(h,'enabled',return_value=True),patch.object(h,'background_remote') as probe:h.sync_vectors(db);probe.assert_not_called()
 def test_filename_index_reuses_existing_background_encoder(self):
  import struct
  db=sqlite3.connect(':memory:');self.addCleanup(db.close)
  db.execute('CREATE TABLE docs(path TEXT)');db.execute('CREATE TABLE chunks(path TEXT,page INTEGER,start INTEGER,text TEXT,embedding BLOB)');db.execute("INSERT INTO docs VALUES('note.md')")
  model=Mock();model.encode.return_value=[h.Vector(struct.pack('<256f',*([0]*256)))]
  with patch.object(search,'encoder') as factory:search.index_filenames(db,model=model);factory.assert_not_called()
  self.assertEqual(db.execute('SELECT count(*) FROM chunks').fetchone()[0],1);model.encode.assert_called_once()
 def test_pasted_image_reuses_local_confidence_pipeline(self):
  with patch.object(h,'background_remote',return_value=False),patch.object(search,'ocr_pasted_image_local',return_value='recognized') as local:
   self.assertEqual(search.ocr_pasted_image(Path('fixture'),force=True),'recognized');local.assert_called_once_with(Path('fixture'),True)
 def test_pdf_fallback_reuses_existing_local_layer_pipeline(self):
  source=self.root/'source.pdf';source.write_bytes(b'%PDF-fixture');fp=pdf.fingerprint(source)
  with patch.object(pdf,'ROOT',self.root/'layers'),patch.object(h,'background_remote',return_value=True),patch.object(h,'pdf_page',side_effect=h.Paused()),patch.object(pdf,'recognize_page_local',return_value='local text') as local:
   self.assertEqual(pdf.recognize_page(source,'PDF Library/source.pdf',fp,1,False),'local text');local.assert_called_once()
 def test_pdf_completed_checkpoint_skips_all_probes_and_ocr(self):
  source=self.root/'source.pdf';source.write_bytes(b'%PDF-fixture');fp=pdf.fingerprint(source)
  with patch.object(pdf,'ROOT',self.root/'layers'),patch.object(h,'background_remote') as probe:
   directory=pdf.prepare('PDF Library/source.pdf',fp);(directory/'1.json').write_text(json.dumps({'status':'empty','text':''}))
   self.assertEqual(pdf.recognize_page(source,'PDF Library/source.pdf',fp,1,False),'');probe.assert_not_called()
 def test_inventory_checkpoint_lives_only_in_derived_cache(self):
  import pdf_inventory
  source=self.root/'source.pdf';source.write_bytes(b'%PDF-fixture');cache=self.root/'derived.json';calls=[]
  def run(args,timeout,log,stderr):
   calls.append(args);log.write(b'page num type width height\n1 0 image 400 400\n')
  self.assertTrue(pdf_inventory.scan(source,1,run,self.root,cache=cache)['complete']);self.assertTrue(cache.is_file());self.assertFalse(source.with_suffix('.inventory.json').exists())
  pdf_inventory.scan(source,1,run,self.root,cache=cache);self.assertEqual(len(calls),1)
class QuartzTests(unittest.TestCase):
 def test_interrupted_remote_build_rebuilds_cleanly_and_publishes_atomically(self):
  import queue,os,shutil,subprocess
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as td:
   data=Path(td);(data/'source').mkdir();(data/'site-a').mkdir();(data/'site-a/index.html').write_text('old');(data/'public').symlink_to('site-a')
   (data/'build-required').touch()
   info={'changed':True,'files':1}
   def remote(*args):
    args[2].mkdir();(args[2]/'partial').write_text('partial');raise h.Paused()
   def run(args,**kwargs):
    build=data/'public-build';self.assertFalse((build/'partial').exists());build.mkdir();(build/'index.html').write_text('new');return SimpleNamespace(returncode=0)
   module=ast.parse(Path(__file__).with_name('quartz_watch.py').read_text());function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='update')
   globals={'latest':'seq','exported':'seq','export_info':info,'DATA':data,'BASE':data,'NODE':Path('node'),'os':os,'shutil':shutil,'helper_client':h,'SimpleNamespace':SimpleNamespace,'subprocess':subprocess,'json':json,'Path':Path}
   exec(compile(ast.Module(body=[function],type_ignores=[]),'watch.py','exec'),globals)
   with patch.object(os.path,'ismount',return_value=True),patch.object(h,'background_remote',return_value=True),patch.object(h,'quartz',side_effect=remote),patch.object(h,'DATA',data),patch.object(subprocess,'run',side_effect=run):globals['update']()
   self.assertEqual((data/'public/index.html').read_text(),'new');self.assertFalse((data/'build-required').exists());self.assertEqual(json.loads((data/'checkpoint.json').read_text()),'seq')
if __name__=='__main__':unittest.main()
