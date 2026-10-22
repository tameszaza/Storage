import unittest
from unittest.mock import patch
from flask import Flask
from routes.settings import register_routes

class SettingsApiTests(unittest.TestCase):
 def setUp(self):
  app=Flask(__name__);app.config.update(SECRET_KEY='fixture',TAILSCALE_CONTROL_SOCKET='/missing.sock');app.add_url_rule('/login','login',lambda:'login');register_routes(app);self.client=app.test_client()
 def owner(self):
  with self.client.session_transaction() as s:s.update(logged_in=True,username='Admin',transcription_csrf='fixture')
 def test_admin_and_csrf_required(self):
  self.assertEqual(self.client.get('/admin/settings/data').status_code,302);self.owner()
  with patch('routes.settings.host') as host:
   self.assertEqual(self.client.post('/admin/settings/data',json={'action':'save'}).status_code,403);host.assert_not_called()
 def test_get_is_private_and_only_uses_fixed_broker_operation(self):
  self.owner()
  with patch('routes.settings.host',return_value={'ok':True,'fields':[]}) as host:
   r=self.client.get('/admin/settings/data');self.assertEqual(r.status_code,200);self.assertIn('no-store',r.headers['Cache-Control']);host.assert_called_once_with({'action':'settings-get'},timeout=25)
 def test_no_arbitrary_actions_or_oversized_requests(self):
  self.owner();headers={'X-CSRF-Token':'fixture'}
  with patch('routes.settings.host') as host:
   self.assertEqual(self.client.post('/admin/settings/data',json={'action':'exec','revision':'x'*64},headers=headers).status_code,400)
   self.assertEqual(self.client.post('/admin/settings/data',data='x'*15001,headers=headers).status_code,413);host.assert_not_called()
 def test_save_uses_validated_shape(self):
  self.owner()
  with patch('routes.settings.host',return_value={'ok':True,'operation':'fixture'}) as host:
   r=self.client.post('/admin/settings/data',json={'action':'save','revision':'a'*64,'changes':{'storage.temp_hours':48},'apply':False},headers={'X-CSRF-Token':'fixture'})
   self.assertEqual(r.status_code,200);self.assertEqual(host.call_args.args[0]['action'],'settings-save')
