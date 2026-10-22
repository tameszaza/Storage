import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace
from flask import Flask
from lib.transcription_store import QueueError
from routes.services import register_routes

spec=importlib.util.spec_from_file_location('container_broker',Path(__file__).resolve().parents[1]/'deploy/tamestorage_containers.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class BrokerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.policy=patch.object(b,'POLICY',Path(self.tmp.name)/'stopped.json');self.policy.start();b.JOBS.clear()
 def tearDown(self):self.policy.stop();self.tmp.cleanup()
 def test_commands_and_unknown_names_rejected(self):
  with patch.object(b,'docker') as docker:
   for name,action in [('navidrome; reboot','stop'),('navidrome','exec'),('livesync-init','start'),('unknown','restart')]:
    with self.assertRaises(b.ControlError):b.action(name,action)
   docker.assert_not_called()
 def test_details_do_not_leak_environment_commands_or_host_paths(self):
  data={'Config':{'Image':'example:1','Env':['SECRET=not-for-browser'],'Cmd':['secret-command']},'State':{'Status':'running'},'HostConfig':{},'Mounts':[{'Source':'/private/secret/path','Destination':'/data','RW':True}]}
  with patch.object(b,'inspect',return_value=data):result=b.detail('navidrome')
  text=json.dumps(result);self.assertNotIn('not-for-browser',text);self.assertNotIn('secret-command',text);self.assertNotIn('/private/secret',text);self.assertEqual(result['volumes'][0]['path'],'/data')
 def test_duplicate_actions_and_policy_persist_across_recovery(self):
  with patch.object(b,'inspect',return_value={'State':{'Status':'running'}}),patch.object(b.EXECUTOR,'submit'):
   ident=b.action('navidrome','stop')['operation']
   with self.assertRaises(b.ControlError):b.action('navidrome','restart')
  with patch.object(b,'docker',return_value={}),patch.object(b.time,'sleep'):
   b._run(ident)
  self.assertEqual(b.operation(ident)['state'],'done');self.assertIn('navidrome',b.stopped())
  with patch.object(b,'inspect',return_value={'State':{'Status':'exited'}}),patch.object(b.EXECUTOR,'submit'):
   ident=b.action('navidrome','start')['operation']
  with patch.object(b,'docker',return_value={}),patch.object(b.time,'sleep'):b._run(ident)
  self.assertNotIn('navidrome',b.stopped())
 def test_failed_change_does_not_claim_success_or_change_stop_preferences(self):
  b.JOBS['test']={'name':'navidrome','action':'stop','state':'queued','at':0}
  with patch.object(b,'docker',side_effect=OSError()),patch.object(b.time,'sleep'):b._run('test')
  self.assertEqual(b.operation('test')['state'],'error');self.assertEqual(b.stopped(),set())
 def test_nas_disconnection_blocks_starts(self):
  with patch('subprocess.run',return_value=SimpleNamespace(stdout=json.dumps({'filesystems':[{'uuid':'wrong-disk'}]}))):
   with self.assertRaises(b.ControlError):b.mounts_ready('tamestorage')
  b.mounts_ready('uptime-kuma')
 def test_list_includes_completed_setup_but_no_unmanaged_containers(self):
  rows=[{'Names':['/navidrome'],'State':'running','Status':'Up 1 hour'},{'Names':['/livesync-init'],'State':'exited'},{'Names':['/private-database'],'State':'running'}]
  with patch.object(b,'docker',return_value=rows):result=b.listing()
  self.assertEqual(len(result),2);self.assertTrue(next(r for r in result if r['name']=='livesync-init')['one_shot'])
 def test_monitor_proxy_is_read_only_and_redacts_docker_inspect(self):
  import threading,urllib.request,urllib.error
  server=b.ThreadingHTTPServer(('127.0.0.1',0),b.ReadOnlyDocker);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  root='http://127.0.0.1:'+str(server.server_port)
  try:
   with patch.object(b,'inspect',return_value={'Id':'test','State':{'Running':True,'Error':'secret','Health':{'Status':'healthy','Log':['secret']}},'Config':{'Env':['secret']}}):
    with urllib.request.urlopen(root+'/v1.45/containers/navidrome/json') as r:data=json.load(r)
    self.assertTrue(data['State']['Running']);self.assertNotIn('secret',json.dumps(data))
   with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(root+'/containers/navidrome/restart',method='POST'))
   self.assertEqual(error.exception.code,405)
  finally:server.shutdown();server.server_close();thread.join()

class ApiTests(unittest.TestCase):
 def setUp(self):
  app=Flask(__name__);app.config.update(SECRET_KEY='fixture',TAILSCALE_CONTROL_SOCKET='/missing.sock')
  app.add_url_rule('/login','login',lambda:'login');register_routes(app);self.client=app.test_client()
 def owner(self):
  with self.client.session_transaction() as s:s.update(logged_in=True,username='Admin',transcription_csrf='fixture-csrf')
 def test_admin_and_csrf_required_before_broker(self):
  with patch('routes.services.host') as host:
   self.assertEqual(self.client.get('/admin/services').status_code,302)
   self.owner();self.assertEqual(self.client.post('/admin/services/navidrome/action',json={'action':'stop'}).status_code,403);host.assert_not_called()
 def test_state_and_start_payloads_are_narrow_and_private(self):
  self.owner()
  with patch('routes.services.host',return_value={'ok':True,'containers':[]}) as host:
   r=self.client.get('/admin/services');self.assertEqual(r.status_code,200);self.assertIn('no-store',r.headers['Cache-Control']);host.assert_called_with({'action':'containers-list'})
  with patch('routes.services.host',return_value={'ok':True,'operation':'id'}) as host:
   r=self.client.post('/admin/services/navidrome/action',json={'action':'restart'},headers={'X-CSRF-Token':'fixture-csrf'})
   self.assertEqual(r.status_code,202);host.assert_called_with({'action':'container-action','name':'navidrome','verb':'restart'})
   self.assertEqual(self.client.post('/admin/services/navidrome/action',json={'action':'remove'},headers={'X-CSRF-Token':'fixture-csrf'}).status_code,400)
 def test_broker_offline_is_an_error_not_empty_healthy_list(self):
  self.owner();r=self.client.get('/admin/services');self.assertEqual(r.status_code,503);self.assertFalse(r.json['ok'])

if __name__=='__main__':unittest.main()
