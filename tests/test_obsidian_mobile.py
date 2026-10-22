"""Mobile relay request isolation and lifecycle contracts; no model/network."""
import fcntl
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
route=Path(__file__).resolve().parents[1]/'routes/obsidian_mobile.py'
if not route.is_file():route=Path(__file__).with_name('obsidian_mobile.py')
spec=importlib.util.spec_from_file_location('mobile',route)
mobile=importlib.util.module_from_spec(spec);spec.loader.exec_module(mobile)
class RelayTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);mobile.ROOT=self.root
  for n in ('home','jobs','assets'):(self.root/n).mkdir()
  (self.root/'bridge.key').write_text('test-key');(self.root/'home/auth.json').write_text('{}');(self.root/'codex').touch()
  app=Flask(__name__);mobile.register_routes(app);self.client=app.test_client();self.headers={'Authorization':'Bearer test-key'}
 def tearDown(self):self.temp.cleanup()
 def post(self,route,data,auth=True):return self.client.post('/obsidian-mobile/'+route,json=data,headers=self.headers if auth else {})
 def start(self):return self.post('start',{'prompt':'Read the fixture','tools':[{'name':'find_files','inputSchema':{'type':'object'}}]})
 def test_auth_and_input_boundaries(self):
  self.assertEqual(self.post('start',{},False).status_code,401)
  self.assertEqual(self.post('start',{'prompt':'x','tools':[{'name':'shell'}]}).status_code,400)
  self.assertEqual(self.post('poll',{'job':'../../home/auth.json'}).status_code,400)
  self.assertEqual(self.client.get('/obsidian-mobile/assets/auth.json').status_code,404)
 def test_single_active_job(self):
  with (self.root/'active.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   self.assertEqual(self.start().status_code,409)
 def test_result_isolation_and_completion_cleanup(self):
  with patch.object(mobile.subprocess,'Popen'):
   response=self.start();self.assertEqual(response.status_code,200);job=response.json['job']
  directory=self.root/'jobs'/job;mobile.atomic(directory/'tool.json',{'id':'tool-1','name':'read_file','arguments':{'path':'Fixture.md'}})
  self.assertEqual(self.post('poll',{'job':job}).json['tool']['name'],'read_file')
  self.assertEqual(self.post('result',{'job':job,'id':'old','result':{}}).status_code,409)
  self.assertEqual(self.post('result',{'job':job,'id':'tool-1','result':{'content':[]}}).status_code,200)
  self.assertEqual(json.loads((directory/'result.json').read_text())['id'],'tool-1')
  mobile.atomic(directory/'state.json',{'state':'done','answer':'Fixture answer'})
  self.assertEqual(self.post('poll',{'job':job,'heartbeat':True}).json['answer'],'Fixture answer');self.assertTrue(directory.exists())
  self.assertEqual(self.post('poll',{'job':job}).json['answer'],'Fixture answer');self.assertFalse(directory.exists())
 def test_cancel_only_marks_requested_job(self):
  with patch.object(mobile.subprocess,'Popen'):job=self.start().json['job']
  self.assertEqual(self.post('cancel',{'job':job}).status_code,200)
  self.assertTrue((self.root/'jobs'/job/'cancel').exists())
 def test_failed_start_does_not_retain_passage(self):
  with patch.object(mobile.subprocess,'Popen',side_effect=OSError()):self.assertEqual(self.start().status_code,503)
  self.assertEqual(list((self.root/'jobs').iterdir()),[])
if __name__=='__main__':unittest.main()
