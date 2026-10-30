import base64,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from lib.chat_archive import Archive,exact_answer,validated_answer,redact
from lib.chat_archive_export import Store

def document(cid='speaker',text='Cyborg Bluetooth speaker mode is manual. Keep automatic startup disabled.',role='user',created='2026-10-05T10:00:00Z',branch=True):
    return {'id':cid,'title':cid,'source_updated':'1','updated_at':1,'messages':[{'id':'msg-'+cid,'parent_id':'','role':role,'created_at':created,'on_current_branch':branch,'text':text}]}

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.export=Store(self.root/'data');self.addCleanup(self.export.db.close)
        self.archive=Archive(self.root,initialize=True);self.addCleanup(self.archive.close)
    def save(self,doc,scope='scope'):
        self.export.save(scope,doc);self.archive.index_one(scope,doc['id'])
    def search(self,q,**kwargs):return self.archive.search(q,**kwargs)['sources']
    def test_backfill_and_idempotency(self):
        self.export.save('scope',document());self.assertEqual(self.archive.status()['dirty'],1)
        self.archive.backfill();first=self.archive.status();self.archive.index_one('scope','speaker')
        self.assertEqual(self.archive.status()['passages'],first['passages']);self.assertEqual(first['indexed'],1)
    def test_order_and_exact_quotes(self):
        self.save(document());a=self.search('Bluetooth speaker');b=self.search('Bluetooth speaker')
        self.assertEqual(a,b);self.assertIn('manual',a[0]['text']);self.assertEqual(a[0]['role'],'user');self.assertTrue(a[0]['url'].endswith('#m-msg-speaker'))
    def test_filters_dates_roles_and_alternate_branches(self):
        self.save(document('old','I prefer Python.','user','2025-01-02T10:00:00Z'))
        self.save(document('new','I now prefer Rust.','user','2026-01-02T10:00:00Z'))
        self.save(document('guess','You prefer Java.','assistant'))
        self.save(document('alternate','I prefer BASIC.','user',branch=False))
        self.assertEqual([s['cid'] for s in self.search('prefer',after='2026-01-01',role='user')],['new'])
        self.assertEqual({s['cid'] for s in self.search('prefer')},{'old','new','guess'})
        self.assertEqual(len(self.search('prefer',before='2025-12-31')),1)
    def test_updated_source_replaces_text_and_pending_vectors(self):
        self.save(document());ids=[r[0] for r in self.archive.db.execute('SELECT id FROM archive_embeddings')]
        self.save(document(text='The wired output has changed to headphones.'))
        self.assertEqual(self.search('Bluetooth'),[]);self.assertTrue(self.search('headphones'))
        self.assertFalse(set(ids)&{r[0] for r in self.archive.db.execute('SELECT id FROM archive_embeddings')})
    def test_scope_identity_and_unchanged_data(self):
        self.save(document(),scope='first');self.save(document(text='Second scope Bluetooth configuration.'),scope='second')
        self.assertEqual(self.archive.status()['conversations'],2)
        self.assertEqual(len(self.search('Bluetooth')),2)
    def test_fts_syntax_injection_and_empty_queries(self):
        self.save(document())
        for q in ['', '*', '" OR 1=1 --', 'NEAR(a b)', 'what did I discuss']:
            self.archive.search(q)
        self.assertEqual(self.search('unicornzzy123'),[])
    def test_credentials_redacted_index_and_original_preserved(self):
        source='password=private-secret API_KEY=another-secret Bearer abcdefghijklmnopqrstuvwxyz'
        self.save(document(text=source))
        result=self.search('password')[0]['text'];self.assertNotIn('private-secret',result);self.assertNotIn('another-secret',result)
        self.assertIn(source,self.export.db.execute('SELECT doc FROM conversations').fetchone()[0])
    def test_exact_mode_does_not_call_helper(self):
        self.save(document());helper=unittest.mock.Mock(side_effect=AssertionError('Network not allowed'))
        result=self.archive.search('speaker',semantic=False,helper=helper)
        self.assertTrue(result['sources']);helper.assert_not_called();self.assertEqual(exact_answer(result['sources'])['provider'],'excerpts')
    def test_offline_semantics_preserves_local_results(self):
        self.save(document())
        result=self.archive.search('Bluetooth',semantic=True,helper=lambda *a,**k:(_ for _ in ()).throw(OSError()))
        self.assertTrue(result['sources']);self.assertEqual(result['semantic'],'waiting for Cyborg')
    def test_stale_semantic_hash_is_rejected(self):
        self.save(document());row=self.archive.db.execute('SELECT id FROM archive_chunks').fetchone()
        with self.archive.db:self.archive.db.execute('UPDATE archive_chunks SET vector=?',(b'x'*1024,))
        def helper(*a,**k):return {'hits':[{'id':row[0],'path':'scope/speaker','hash':'a'*64,'distance':.1}]}
        self.assertEqual(self.archive.search('wireless receiver',semantic=True,helper=helper)['sources'],[])
    def test_citation_page_counts_empty_messages(self):
        doc=document('long')
        doc['messages']=[{'id':'empty-'+str(i),'text':'','role':'tool'} for i in range(70)]+doc['messages']
        self.save(doc)
        source=self.search('Bluetooth')[0]
        self.assertIn('?page=1#m-msg-long',source['url'])
    def test_dirty_source_is_not_returned_as_current_evidence(self):
        self.save(document())
        self.export.save('scope',document(text='Source changed and its index is now queued.'))
        self.assertEqual(self.search('Bluetooth'),[])
    def test_index_writer_does_not_block_primary_export(self):
        self.save(document())
        self.archive.db.execute('BEGIN')
        self.archive.db.execute("UPDATE archive_meta SET value=value WHERE key='epoch'")
        try:
            self.export.save('scope',document('independent','The export continues while the derived index is busy.'))
            self.assertTrue(self.export.db.execute("SELECT 1 FROM conversations WHERE id='independent'").fetchone())
        finally:self.archive.db.rollback()
    def test_concurrent_source_edit_keeps_dirty_marker(self):
        original=document();self.export.save('scope',original)
        changed=[False]
        def edit():
            if not changed[0]:
                changed[0]=True;self.export.save('scope',document(text='Updated during index processing.'))
            return 1
        self.archive.db.create_function('fixture_edit',0,edit)
        self.archive.db.execute('CREATE TEMP TRIGGER concurrent_edit AFTER INSERT ON main.archive_docs BEGIN SELECT fixture_edit(); END')
        self.archive.index_one('scope','speaker')
        self.assertTrue(self.export.db.execute('SELECT 1 FROM archive_dirty').fetchone())
        self.archive.index_one('scope','speaker')
        self.assertFalse(self.export.db.execute('SELECT 1 FROM archive_dirty').fetchone())
        self.assertTrue(self.search('Updated'))
    def test_queue_idempotent_and_restart_safe(self):
        self.save(document());sources=self.search('speaker')
        with patch('lib.chat_archive.wake') as wake:
            a=self.archive.enqueue_answer('How was speaker mode configured?',sources)
            b=self.archive.enqueue_answer('How was speaker mode configured?',sources)
        self.assertEqual(a,b);self.assertEqual(self.archive.answer_job(a)['status'],'queued')
        with Archive(self.root) as reopened:self.assertEqual(reopened.answer_job(a)['status'],'queued')
        self.assertEqual(self.archive.status()['answers_pending'],1)
    def test_quote_validation_unknown_ids_and_fabricated_quotes(self):
        self.save(document());sources=self.search('speaker')
        value={'claims':[{'text':'Startup is manual.','evidence':[{'source':'A1','quote':'speaker mode is manual.'}]}]}
        self.assertIn('[1]',validated_answer(value,sources,'fixture')['response'])
        for ref in [{'source':'A9','quote':'speaker mode is manual.'},{'source':'A1','quote':'Startup is always automatic.'}]:
            value['claims'][0]['evidence']=[ref];result=validated_answer(value,sources,'fixture')
            self.assertEqual(result['archive_sources'],[]);self.assertEqual(result['rejected_claims'],1)
    def test_long_messages_and_unicode(self):
        self.save(document('thai','ภาษาไทย แผนงาน '+('กรุงเทพ '*400)))
        self.assertGreater(self.archive.status()['passages'],1)
        self.assertTrue(self.search('ภาษาไทย'))
    def test_bad_filters_fail_closed(self):
        for kwargs in [{'role':"Admin' OR 1=1"},{'after':'yesterday'}]:
            with self.assertRaises(ValueError):self.archive.search('speaker',**kwargs)
    def test_cyborg_queue_returns_to_pending_after_offline(self):
        from lib.chat_archive_jobs import step
        self.save(document());sources=self.search('speaker')
        with patch('lib.chat_archive.wake'):key=self.archive.enqueue_answer('How was speaker mode configured?',sources)
        with patch('lib.chat_archive_jobs.cyborg_answer',side_effect=OSError()),patch('lib.chat_archive_jobs.helper',side_effect=OSError()):step(self.root)
        self.assertEqual(self.archive.answer_job(key)['status'],'queued')
        with self.archive.db:self.archive.db.execute('UPDATE archive_answers SET lease=0')
        answer={'response':'Manual [1]','archive_sources':sources,'provider':'Cyborg'}
        with patch('lib.chat_archive_jobs.cyborg_answer',return_value=answer),patch('lib.chat_archive_jobs.helper',side_effect=OSError()):step(self.root)
        self.assertEqual(self.archive.answer_job(key)['result'],answer)

class RouteTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from routes import chat_archive as routes
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        store=Store(self.root/'data');store.save('scope',document());store.db.close()
        with Archive(self.root,initialize=True) as index:index.backfill()
        self.app=Flask(__name__);self.app.secret_key='fixture'
        self.app.config['CHAT_ARCHIVE_ROOT']=self.root
        self.app.add_url_rule('/login','login',lambda:'Login')
        routes.register_routes(self.app)
        from routes.ai import chat
        self.app.add_url_rule('/chat',view_func=chat,methods=['POST'])
        self.client=self.app.test_client()
        self.state=self.root/'private';self.state.mkdir();(self.state/'collector-key').write_text('fixture-key')
        self.patcher=patch.object(routes,'STATE',self.state);self.patcher.start();self.addCleanup(self.patcher.stop)
        for target in ['lib.chat_archive.wake','routes.chat_archive.wake']:
            p=patch(target);p.start();self.addCleanup(p.stop)
    def login(self,user='Admin'):
        with self.client.session_transaction() as s:s.update(logged_in=True,username=user,assistant_csrf='fixture-csrf')
    def ask(self,**data):return self.client.post('/chat',data={'msg':'Bluetooth speaker','archive_mode':'excerpts','csrf':'fixture-csrf',**data})
    def test_archive_access_admin_only(self):
        self.assertEqual(self.client.get('/admin/chat-archive/search?q=Bluetooth').status_code,302)
        self.login('alice');self.assertIn(self.client.get('/admin/chat-archive/search?q=Bluetooth').status_code,(302,403))
        self.assertEqual(self.ask().status_code,403)
        self.login();self.assertEqual(self.ask().status_code,200)
    def test_excerpts_skip_ai_and_existing_write_tools(self):
        self.login()
        with patch('lib.chat_archive_jobs.google_answer',side_effect=AssertionError()),patch('lib.chat_archive_jobs.cyborg_answer',side_effect=AssertionError()):
            response=self.ask();self.assertEqual(response.json['provider'],'excerpts');self.assertTrue(response.json['archive_sources'])
        self.assertNotIn('actions',response.json)
    def test_cyborg_queues_and_job_is_private(self):
        self.login()
        with patch('lib.chat_archive_jobs.helper',side_effect=OSError()),patch('lib.chat_archive.wake'):
            response=self.ask(archive_mode='cyborg')
        key=response.json['archive_job'];self.assertEqual(self.client.get('/admin/chat-archive/jobs/'+key).json['status'],'queued')
        self.login('alice');self.assertIn(self.client.get('/admin/chat-archive/jobs/'+key).status_code,(302,403))
    def test_archive_csrf_and_origin(self):
        self.login();self.assertEqual(self.ask(csrf='wrong').status_code,403)
        self.assertEqual(self.client.post('/chat',data={'msg':'Bluetooth','archive_mode':'cyborg'},headers={'Origin':'https://evil.invalid'}).status_code,403)
    def test_collector_key_and_payload_guards(self):
        self.assertEqual(self.client.post('/admin/chat-archive/collector',json={'op':'overview'}).status_code,403)
        h={'X-Export-Key':'fixture-key'}
        self.assertEqual(self.client.post('/admin/chat-archive/collector',json={'version':1,'op':'state','scope':'scope'},headers=h).status_code,409)
        self.assertEqual(self.client.post('/admin/chat-archive/collector',json={'version':2,'op':'state','scope':'../escape'},headers=h).status_code,400)
        r=self.client.post('/admin/chat-archive/collector',json={'version':2,'op':'save','scope':'scope','payload':document('new','A new archive message about diagrams.')},headers=h)
        self.assertTrue(r.json['ok']);self.assertEqual(self.client.get('/admin/chat-archive/search?q=diagrams').status_code,302)
        with Archive(self.root) as index:
            self.assertTrue(index.source_db.execute("SELECT 1 FROM archive_dirty WHERE cid='new'").fetchone())
            index.backfill()
        self.login();self.assertTrue(self.client.get('/admin/chat-archive/search?q=diagrams').json['sources'])
    def test_google_explicit_only_and_cache(self):
        self.login();answer={'response':'Manual [1]','archive_sources':[],'provider':'fixture-google'}
        with patch('lib.chat_archive_jobs.helper',side_effect=OSError()),patch('lib.chat_archive_jobs.google_answer',return_value=answer) as provider:
            self.assertEqual(self.ask(archive_mode='google').json,answer)
            self.assertEqual(self.ask(archive_mode='google').json,answer)
            self.assertEqual(provider.call_count,1)
            self.ask(archive_mode='google',archive_refresh='1');self.assertEqual(provider.call_count,2)
    def test_unrelated_question_makes_no_ai_call(self):
        self.login()
        with patch('lib.chat_archive_jobs.google_answer',side_effect=AssertionError()),patch('lib.chat_archive_jobs.helper',side_effect=OSError()):
            response=self.ask(msg='unicornzzxxyy',archive_mode='google')
        self.assertEqual(response.json['archive_sources'],[])

if __name__=='__main__':unittest.main()
