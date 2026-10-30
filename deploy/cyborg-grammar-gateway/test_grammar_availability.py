import io,json,socket,threading,time,unittest
from unittest.mock import Mock,patch
import gateway as g
BASE={'matches':[{'offset':5,'length':3,'message':'agreement','replacements':[{'value':'is'}],'rule':{'id':'FIXTURE'}}],'language':{'code':'en-US'}}
class GrammarTests(unittest.TestCase):
 def invoke(self,forward,enabled=True,params=None):
  handler=object.__new__(g.Handler);handler.path='/v2/check';body=json.dumps(params or {'text':'This are wrong.','language':'en-US'}).encode();handler.headers={'Content-Length':str(len(body)),'Content-Type':'application/json'};handler.rfile=io.BytesIO(body);handler.reply=Mock()
  with patch.object(g,'forward',side_effect=forward),patch.object(g,'AI_ENABLED',enabled):handler.do_POST()
  return handler
 def test_offline_ai_keeps_native_corrections_and_200(self):
  def forward(host,*args,**kwargs):
   if host=='127.0.0.1':return 200,json.dumps(BASE).encode()
   self.assertEqual(args[2],'/health');raise TimeoutError()
  h=self.invoke(forward);code,data=h.reply.call_args.args[:2];self.assertEqual(code,200);self.assertEqual(json.loads(data)['matches'],BASE['matches']);self.assertEqual(h.ai_state,'unavailable')
 def test_ai_off_never_contacts_cyborg(self):
  def forward(host,*args,**kwargs):self.assertEqual(host,'127.0.0.1');return 200,json.dumps(BASE).encode()
  h=self.invoke(forward,enabled=False);self.assertEqual(h.reply.call_args.args[0],200);self.assertEqual(h.ai_state,'disabled')
 def test_disabled_ai_rule_skips_health_and_inference(self):
  def forward(host,*args,**kwargs):self.assertEqual(host,'127.0.0.1');return 200,json.dumps(BASE).encode()
  h=self.invoke(forward,params={'text':'This are wrong.','language':'en-US','disabledRules':'LLM_RULE'});self.assertEqual(h.reply.call_args.args[0],200)
 def test_healthy_ai_then_disconnect_keeps_native_result(self):
  def forward(host,port,method,path,*args,**kwargs):
   if host=='127.0.0.1':return 200,json.dumps(BASE).encode()
   if path=='/health':return 200,b'{"ready":true}'
   raise ConnectionResetError()
  h=self.invoke(forward);self.assertEqual(json.loads(h.reply.call_args.args[1])['matches'],BASE['matches']);self.assertEqual(h.ai_state,'unavailable')
 def test_healthy_ai_combines_without_changing_baseline(self):
  def forward(host,port,method,path,*args,**kwargs):
   if host=='127.0.0.1':return 200,json.dumps(BASE).encode()
   return (200,b'{"ready":true}') if path=='/health' else (200,b'{"matches":[]}')
  h=self.invoke(forward);self.assertEqual(h.reply.call_args.args[2],'hybrid');self.assertEqual(h.ai_state,'available');self.assertEqual(json.loads(h.reply.call_args.args[1])['matches'],BASE['matches'])
 def test_malformed_health_never_submits_text_to_ai(self):
  with patch.object(g,'forward',return_value=(200,b'invalid')) as f:self.assertFalse(g.ai_available());self.assertEqual(f.call_args.args[2:4],('GET','/health'))
 def test_accepted_connection_without_http_response_has_short_read_deadline(self):
  original=g.forward;listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
  def blackhole():
   connection,_=listener.accept()
   with connection:time.sleep(1.2)
   listener.close()
  thread=threading.Thread(target=blackhole,daemon=True);thread.start()
  def forward(host,remote_port,*args,**kwargs):return original('127.0.0.1',port,*args,**kwargs)
  started=time.monotonic()
  with patch.object(g,'forward',side_effect=forward):self.assertFalse(g.ai_available())
  self.assertLess(time.monotonic()-started,1.05);thread.join(2)
if __name__=='__main__':unittest.main()
