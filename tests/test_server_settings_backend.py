"""Run on the host: reuse its installed YAML parser and standard unittest."""
import copy,json,math,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
import tamestorage_settings as m

class SettingsBackendTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.patches=[];self.commands=[]
  for name,value in [('BASE',self.base/'docker'),('STATE',self.base/'private/settings.json'),('OPS',self.base/'operations.conf'),('APP_ENV',self.base/'app.env'),('UNIT_ROOT',self.base/'units'),('INI',{'couchdb':self.base/'couchdb.ini','radicale':self.base/'radicale.ini'})]:
   p=patch.object(m,name,value);p.start();self.patches.append(p)
  m.APP_ENV.write_text('SECRET_KEY=private-key\n');m.INI['couchdb'].write_text('[couchdb]\nmax_document_size=52428800\n[chttpd]\nmax_http_request_size=524288000\n[httpd]\nWWW-Authenticate=Basic realm="CouchDB"\n')
  m.INI['radicale'].write_text('[server]\nmax_connections=20\ntimeout=30\n')
  self.rows={};projects={}
  for name,(group,project,service,label,minimum) in m.DOCKERS.items():
   doc=projects.setdefault(project,{'services':{}});doc['services'][service]={'image':'fixture:current','environment':{'KEEP_ME':'unchanged'}}
   self.rows[name]={'Name':'/'+name,'Image':'sha256:current-image','Config':{'Env':['CALDAV_PASSWORD=private-secret','NAVIDROME_PASSWORD=private-secret'],'Cmd':['gunicorn','--preload','--workers','2','--threads','4','--timeout','3600','app:app']},'HostConfig':{'Memory':1536*1024**2 if name=='languagetool' else 384*1024**2 if name=='uptime-kuma' else 0,'NanoCpus':0,'LogConfig':{'Config':{}},'RestartPolicy':{'Name':'unless-stopped'}},'State':{'Running':True}}
  for project,doc in projects.items():
   p=m.BASE/project/'compose.yaml';p.parent.mkdir(parents=True);p.write_text(m.yaml.safe_dump(doc,sort_keys=False))
  m.UNIT_ROOT.mkdir()
  for name,(_,_,memory,cpu,_) in m.UNITS.items():(m.UNIT_ROOT/name).write_text(f'[Service]\nMemoryMax={memory}M\nCPUQuota={cpu}%\n')
  for name in ['tamestorage-backup.timer','tamestorage-primary-recover.timer']:(m.UNIT_ROOT/name).write_text('[Timer]\nOnUnitActiveSec=6h\nOnUnitInactiveSec=1min\nRandomizedDelaySec=5min\n')
  def cmd(args,**kw):
   self.commands.append(args)
   return json.dumps([self.rows[n] for n in args[2:] if n in self.rows]) if args[:2]==['docker','inspect'] else ''
  p=patch.object(m,'command',side_effect=cmd);p.start();self.patches.append(p);m.controls.JOBS.clear()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def snapshot(self):return m.Snapshot()
 def test_catalog_is_broad_and_never_returns_stored_secrets(self):
  snap=self.snapshot();data=snap.public();self.assertGreater(len(data['fields']),150)
  raw=json.dumps(data);self.assertNotIn('private-secret',raw);self.assertNotIn('private-key',raw)
  field=next(f for f in data['fields'] if f['key']=='music.password');self.assertIsNone(field['value']);self.assertTrue(field['configured'])
 def test_types_ranges_unknown_keys_and_url_credentials_are_rejected(self):
  snap=self.snapshot()
  for changes in [{'shell.command':'reboot'},{'grammar.heap':float('nan')},{'grammar.threads':-1},{'music.lyrics':1},{'server.timezone':'../etc/passwd'},{'music.url':'file:///etc/passwd'},{'music.url':'http://user:pass@example.com'},{'music.password':'line\nbreak'},{'tamestorage.restart':'exec'}]:
   with self.subTest(keys=list(changes)),self.assertRaises(m.SettingsError):m.plan(snap,changes,snap.revision)
 def test_cross_limits_protect_memory_and_request_budgets(self):
  snap=self.snapshot()
  for changes in [{'grammar.heap':2048},{'uptime.heap':500},{'gateway.budget':20},{'grammar.heap_min':1200}]:
   with self.assertRaises(m.SettingsError):m.plan(snap,changes,snap.revision)
 def test_external_edits_reject_stale_window(self):
  snap=self.snapshot();p=m.compose_path('navidrome');p.write_text(p.read_text()+'\n# external edit\n')
  with self.assertRaises(m.SettingsError):m.plan(self.snapshot(),{'navidrome.image_cache':600},snap.revision)
 def test_saved_values_persist_and_preserve_unrelated_config(self):
  snap=self.snapshot();changes=m.plan(snap,{'music.lyrics_delay':.5,'server.workers':3,'music.password':'literal$word','livesync.document_mb':64},snap.revision)
  m.save_files(snap,changes);new=self.snapshot();self.assertEqual(new.values['music.lyrics_delay'],.5);self.assertEqual(new.values['server.workers'],3);self.assertEqual(new.values['music.password'],'literal$word');self.assertEqual(new.values['livesync.document_mb'],64)
  doc=m.load_yaml(m.compose_path('tamestorage'))
  self.assertEqual(doc['services']['web']['environment']['KEEP_ME'],'unchanged');self.assertIn('--preload',doc['services']['web']['command'])
  self.assertIn('WWW-Authenticate',m.INI['couchdb'].read_text());self.assertEqual(set(m.state()['pending']),set(changes));self.assertNotIn('literal$word',m.STATE.read_text())
 def test_preview_redacts_password_changes(self):
  snap=self.snapshot();with_changes={'music.password':'new-private-password'}
  value=m.preview({'revision':snap.revision,'changes':with_changes});self.assertNotIn('new-private-password',json.dumps(value));self.assertNotIn('private-secret',json.dumps(value))
 def test_write_failure_restores_files_and_metadata(self):
  snap=self.snapshot();changes=m.plan(snap,{'music.lyrics_delay':.5,'grammar.heap':800},snap.revision);paths=[m.compose_path('tamestorage'),m.compose_path('languagetool')];before={p:p.read_bytes() for p in paths};original=m.atomic;failed=[False]
  def fail_once(path,data,mode=None):
   if path==paths[1] and not failed[0]:failed[0]=True;raise OSError('fixture write failure')
   return original(path,data,mode)
  with patch.object(m,'atomic',side_effect=fail_once),self.assertRaises(OSError):m.save_files(snap,changes)
  self.assertEqual({p:p.read_bytes() for p in paths},before);self.assertFalse(m.STATE.exists())
 def test_apply_preserves_running_image_and_manual_stop(self):
  name='tamestorage-control-check';job={}
  with patch.object(m.controls,'inspect',return_value=self.rows[name]),patch.object(m.controls,'stopped',return_value={name}),patch.object(m.controls,'mounts_ready'):
   m.apply_saved([name+'.memory'],job)
  args=next(a for a in self.commands if a[:2]==['docker','compose'] and 'up' in a)
  self.assertIn('--no-start',args);self.assertIn('--no-build',args);self.assertEqual(args[args.index('--pull')+1],'never')
  pin=m.load_yaml(m.compose_path(name).with_name('compose.settings-images.yaml'));self.assertEqual(pin['services']['check']['image'],'sha256:current-image')
 def test_on_demand_settings_reload_without_starting_processing(self):
  snap=self.snapshot();changes=m.plan(snap,{'search.results':12},snap.revision);m.save_files(snap,changes);m.apply_saved(changes,{})
  self.assertIn(['systemctl','daemon-reload'],self.commands)
  self.assertFalse(any(a[0]=='systemctl' and any(x in a for x in ['start','restart']) for a in self.commands))
  self.assertIn('TAMES_SEARCH_MAX_RESULTS=12',m.unit_path('lecture-search@.service').read_text());self.assertIn('RuntimeMaxSec=100',m.unit_path('lecture-search@.service').read_text())
 def test_busy_operations_are_serialized_and_secret_not_in_job_status(self):
  snap=self.snapshot()
  with patch.object(m.controls.EXECUTOR,'submit'):
   result=m.save({'revision':snap.revision,'changes':{'music.password':'new-password'},'apply':False})
   self.assertNotIn('new-password',json.dumps(m.controls.JOBS))
   with self.assertRaises(m.SettingsError):m.save({'revision':snap.revision,'changes':{'music.lyrics':False}})
 def test_retention_writes_only_typed_operations_file(self):
  snap=self.snapshot();changes=m.plan(snap,{'storage.temp_hours':48,'storage.audio_hours':72},snap.revision);m.save_files(snap,changes)
  self.assertEqual(m.lines_values(m.OPS)['TEMP_RETENTION_HOURS'],'48');self.assertEqual(m.lines_values(m.OPS)['RECORDING_RETENTION_HOURS'],'72')
  self.assertIn('backup.timer',m.timer_path('tamestorage-backup.timer').as_posix())

 def test_applying_a_service_includes_its_previously_saved_changes(self):
  keys=m.expanded_keys(['grammar.threads'],['grammar.heap','music.lyrics'])
  self.assertIn('grammar.heap',keys);self.assertNotIn('music.lyrics',keys)
 def test_dynamic_retention_has_no_false_pending_restart(self):
  snap=self.snapshot();changes=m.plan(snap,{'storage.temp_hours':48},snap.revision);m.save_files(snap,changes)
  self.assertNotIn('storage.temp_hours',m.state()['pending']);self.assertTrue(m.impacts(changes)['next_cleanup'])
 def test_native_compose_validation_preserves_literal_password(self):
  import subprocess
  snap=self.snapshot();changes=m.plan(snap,{'music.password':'literal$word'},snap.revision);writes=m.make_writes(snap,changes)
  path=m.compose_path('tamestorage');raw=subprocess.check_output(['docker','compose','--project-directory',str(path.parent),'-f','-','config','--format','json'],input=writes[path],text=False)
  parsed=json.loads(raw);self.assertEqual(parsed['services']['web']['environment']['NAVIDROME_PASSWORD'],'literal$$word') # Compose's config format retains escapes for safe round trips.


 def test_services_without_healthcheck_do_not_use_compose_wait(self):
  name='tamestorage-control-check'
  with patch.object(m.controls,'inspect',return_value=self.rows[name]),patch.object(m.controls,'stopped',return_value=set()):m.apply_saved([name+'.memory'],{})
  args=next(a for a in self.commands if a[:2]==['docker','compose'] and 'up' in a)
  self.assertNotIn('--wait',args);self.assertNotIn('--no-start',args)

if __name__=='__main__':unittest.main()
