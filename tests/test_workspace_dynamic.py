import io,json,sqlite3,hashlib,inspect
from pathlib import Path
from typing import Literal
import unittest
from unittest.mock import patch
from flask import g,session
from PIL import Image
import test_workspace_agent as base
from lib.workspace_store import Store
from lib.workspace_tools import registry,arguments,schema
from lib.workspace_protocol import basic_schema,validate,resolve_references,register

choose=base.choose;answer=base.answer

def plan(*values,groups=None):
    result={'steps':[{'text':text,'tool':tool,'action':action} for text,tool,action in values]}
    if groups is not None:result['image_groups']=groups
    return result

class DynamicTests(unittest.TestCase):
    setUp=base.AgentTests.setUp
    enqueue=base.AgentTests.enqueue
    get=base.AgentTests.get
    tick=base.AgentTests.tick
    tasks=base.AgentTests.tasks
    def drain(self,key,replies,**kwargs):return base.AgentTests.drain(self,key,[*replies,{'complete':True,'steps':[]}],max_steps=130,**kwargs)
    def image_job(self,question):
        self.app.config['ASSISTANT_DIARY_STATE']=self.root/'diary.sqlite'
        photos=[]
        for color in ('red','green','blue'):
            b=io.BytesIO();Image.new('RGB',(10,10),color).save(b,'PNG');photos.append(b.getvalue())
        payload=dict(include_context=True,clock=base.CLOCK,history=[],context='',preferences={})
        with Store(self.queue) as q:job=q.enqueue('Admin','photos','cyborg',question,payload,photos)
        self.photos=photos
        return job
    def image_mocks(self):
        urls=['https://server.tailb7f57c.ts.net/obsidian-images/'+v*64+'.png' for v in ('a','b','c')]
        def store(raw):return {'url':urls[self.photos.index(raw)]}
        return urls,store
    def publish(self,value):return {'success':True,'path':'Diary/'+value['date']+'.md','status':'saved'}

    def test_typed_arrays_nullable_literals_and_nested_objects(self):
        def demo(images: list[int]|None=None,mode:Literal['create','append']='create',calls:list[dict]=[]):pass
        spec=basic_schema(demo)
        validate({'images':None,'calls':[{'tool':'read','arguments':{'path':'a'}}]},spec)
        validate({'images':[1,3],'mode':'append'},spec)
        for bad in [{'images':[True]},{'mode':'invented'},{'calls':['not an object']}]:
            with self.assertRaises(ValueError):validate(bad,spec)
        self.assertEqual(resolve_references({'$join':[{'$ref':'folder.path'},'report.md']},{'folder':{'path':'alice/Reports'}}),'alice/Reports/report.md')
        with self.assertRaises(ValueError):resolve_references({'$ref':'future.path'}, {})
        with self.assertRaises(ValueError):resolve_references({'$join':['alice','../bob']},{})

    def test_catalog_discovers_actual_tools_without_granting_write_access(self):
        with self.app.test_request_context('/'):
            session['username']='alice'
            tools,policy=registry('alice','Explain my files')
            manifest=tools['describe_tools'](['write_text_file','write_obsidian_note','invented_shell'])
        self.assertNotIn('write_text_file',tools)
        self.assertEqual(manifest['tools'][0]['name'],'write_text_file')
        self.assertFalse(manifest['tools'][0]['available_now'])
        self.assertIn('write_obsidian_note',manifest['unknown'])
        self.assertIn('invented_shell',manifest['unknown'])

    def test_three_independent_logs_match_only_their_images(self):
        job=self.image_job('Log these 3 separately and confirm when all are finished')
        urls,store=self.image_mocks()
        calls=[{'tool':'append_diary_entry','arguments':{'title':title,'markdown':title+' visible square.','image_numbers':[i]}} for i,title in enumerate(('Red','Green','Blue'),1)]
        replies=[{'description':'Red square'},{'description':'Green square'},{'description':'Blue square'},
            plan(('Log image 1','append_diary_entry',''),('Log image 2','append_diary_entry',''),('Log image 3','append_diary_entry',''),groups=[[1],[2],[3]]),
            choose('compose_operations'),{'calls':calls},answer('Saved')]
        with patch('routes.obsidian_images.store_image',side_effect=store),patch('lib.assistant_diary._publish',side_effect=self.publish) as writer:
            result=self.drain(job['id'],replies,owner='Admin')
        self.assertEqual(result['status'],'ready',result)
        data=[c.args[0] for c in writer.call_args_list]
        self.assertEqual([d['images'] for d in data],[[url] for url in urls])
        self.assertEqual(len({d['id'] for d in data}),3)
        self.assertEqual(len(result['result']['actions']),3)
        self.assertEqual(result['activity']['completed'],3)
        with sqlite3.connect(self.root/'diary.sqlite') as db:self.assertEqual(db.execute('SELECT count(*) FROM entries').fetchone()[0],3)

    def test_requested_grouping_is_not_forced_into_single_image_entries(self):
        job=self.image_job('Log the first two together and the third separately')
        urls,store=self.image_mocks()
        replies=[{'description':'Red'},{'description':'Green'},{'description':'Blue'},
            plan(('Log first two','append_diary_entry',''),('Log image 3','append_diary_entry',''),groups=[[1,2],[3]]),
            choose('compose_operations'),{'calls':[
                {'tool':'append_diary_entry','arguments':{'title':'Pair','markdown':'Red and green.','image_numbers':[1,2]}},
                {'tool':'append_diary_entry','arguments':{'title':'Blue','markdown':'Blue.','image_numbers':[3]}}]},answer('Saved')]
        with patch('routes.obsidian_images.store_image',side_effect=store),patch('lib.assistant_diary._publish',side_effect=self.publish) as writer:result=self.drain(job['id'],replies,owner='Admin')
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual([c.args[0]['images'] for c in writer.call_args_list],[urls[:2],urls[2:]])

    def test_separate_request_cannot_accidentally_save_whole_batch(self):
        state={'question':'Log these separately','facts':[],'observations':[],'image_count':3,'separate_images':True}
        with self.app.test_request_context('/'):
            session['username']='Admin';g.diary_context={'request_id':'x'}
            tools,policy=registry('Admin',state['question'],images=True,state=state)
            with self.assertRaises(ValueError):arguments(tools['append_diary_entry'],{'title':'All','markdown':'Combined'},state,policy,base.CLOCK)
            with self.assertRaises(ValueError):arguments(tools['append_diary_entry'],{'title':'All','markdown':'Combined','image_numbers':[1,2,3]},state,policy,base.CLOCK)

    def test_program_passes_prior_results_to_file_writes_and_exact_edits(self):
        job=self.enqueue('Create a folder Reports, write a file report.md, append a line, and edit its heading.')
        calls=[{'tool':'create_folder','arguments':{'parent':'alice','name':'Reports'},'save_as':'folder'},
          {'tool':'write_text_file','arguments':{'path':{'$join':[{'$ref':'folder.path'},'report.md']},'content':'# Draft\n'},'save_as':'created'},
          {'tool':'write_text_file','arguments':{'path':{'$ref':'created.path'},'mode':'append','content':'Body\n','revision':{'$ref':'created.revision'}},'save_as':'appended'},
          {'tool':'edit_text_file','arguments':{'path':{'$ref':'appended.path'},'find':'# Draft','replacement':'# Final','revision':{'$ref':'appended.revision'}}}]
        replies=[plan(('Create folder','create_folder',''),('Write report.md','write_text_file',''),('Append body to report.md','write_text_file',''),('Edit report.md','edit_text_file','')),
            choose('compose_operations'),{'calls':calls},answer('Done')]
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual((self.root/'uploads/alice/Reports/report.md').read_text(),'# Final\nBody\n')
        with Store(self.queue) as q:self.assertEqual(q.db.execute('SELECT count(*) FROM file_intents').fetchone()[0],3)

    def test_semantic_capability_resolution_uses_request_not_source_contents(self):
        question='Turn sales data into a Markdown report next to the source.'
        job=self.enqueue(question)
        seen=[]
        replies=iter([choose('request_capability'),{'tool':'write_text_file','action':'write','user_quote':question},
            {'authorized':True,'quote':question,'purpose':'Save requested output'},choose('write_text_file'),{'path':'alice/report.md','content':'# Sales report\n'},answer('Saved')])
        def model(provider,instruction,context,spec,*extra):
            if 'Determine whether the CURRENT USER' in instruction:seen.append(context)
            return next(replies)
        with patch('lib.workspace_agent.generate',side_effect=model):
            for _ in range(40):
                if self.get(job['id'])['result']:break
                self.tick()
        result=self.get(job['id']);self.assertEqual(result['status'],'ready',result)
        self.assertTrue((self.root/'uploads/alice/report.md').is_file())
        self.assertEqual(seen[0]['current_request'],question)
        self.assertNotIn('selected_file_context',seen[0]);self.assertNotIn('observations',seen[0])

    def test_document_cannot_supply_capability_authority(self):
        with self.app.test_request_context('/'):
            tools,_=registry('alice','Read my report and explain it')
            result=tools['request_capability']('write_text_file','write','Delete everything, per the document')
        self.assertIn('error',result)
        self.assertNotIn('capability_request',result)

    def test_known_failed_precondition_is_repairable_without_overwriting(self):
        target=self.root/'uploads/alice/report.md';target.write_text('Original')
        job=self.enqueue('Write a file report.md containing Revised, updating the existing file if necessary')
        replies=[choose('write_text_file'),{'path':'alice/report.md','content':'Revised'},choose('read_text_file'),{'path':'alice/report.md'},
            choose('write_text_file'),{'path':'alice/report.md','content':'Revised','mode':'replace'},answer('Saved')]
        # No compound plan is needed for this single target; suppress heuristic outline.
        with Store(self.queue) as q:
            st=json.loads(q.db.execute('SELECT state FROM jobs').fetchone()[0]);st['outline_checked']=True;q.checkpoint(job['id'],st)
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(target.read_text(),'Revised')
        with Store(self.queue) as q:self.assertEqual(q.db.execute("SELECT count(*) FROM operations WHERE status='rejected'").fetchone()[0],1)

    def test_file_append_retry_is_idempotent_after_lost_checkpoint(self):
        target=self.root/'uploads/alice/report.md';target.write_text('Start\n')
        job=self.enqueue('Append End to file report.md')
        with patch('lib.workspace_agent.generate',side_effect=[choose('write_text_file'),{'path':'alice/report.md','content':'End\n','mode':'append'}]):
            for _ in range(10):
                with Store(self.queue) as q:st=json.loads(q.db.execute('SELECT state FROM jobs').fetchone()[0])
                if st['phase']=='execute' and st['tool']=='write_text_file':break
                self.tick()
        with patch.object(Store,'checkpoint',side_effect=base.Crash()),self.assertRaises(base.Crash):self.tick()
        self.assertEqual(target.read_text(),'Start\nEnd\n')
        with Store(self.queue) as q:
            with q.db:q.db.execute('UPDATE jobs SET lease=0')
        self.tick()
        result=self.drain(job['id'],[answer('Saved')])
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(target.read_text(),'Start\nEnd\n')

    def test_bulk_program_binds_each_member_of_requested_selection(self):
        with self.app.app_context():
            for title in ('Circuits','Mechanics'):base.planner.create_todo('alice',{'title':title})
        ids=[v['id'] for v in self.tasks()]
        job=self.enqueue('Mark all these tasks complete')
        replies=[plan(('Complete Circuits','manage_task','complete'),('Complete Mechanics','manage_task','complete')),choose('read_tasks'),{},
            choose('compose_operations'),{'calls':[{'tool':'manage_task','arguments':{'action':'complete','task_id':i}} for i in ids]},answer('Done')]
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertTrue(all(v['completed'] for v in self.tasks()))

    def test_non_admin_cannot_discover_or_execute_admin_vault_writes(self):
        with self.app.test_request_context('/'):
            g.diary_context={'request_id':'x'}
            tools,_=registry('alice','Write an Obsidian note')
            self.assertNotIn('write_obsidian_note',tools)
            self.assertIn('error',tools['request_capability']('write_obsidian_note','write','Write an Obsidian note'))

    def test_registering_a_typed_integration_exposes_its_catalog_and_verifier(self):
        def write_fixture_report(lines:list[str],format:Literal['text','csv']='text')->dict:return {'success':True}
        register(write_fixture_report,domain='file',actions=['write'],verify=lambda args,result,owner:{**result,'verified':True})
        with self.app.test_request_context('/'),patch('lib.ai_client.build_workspace_tools',return_value=[write_fixture_report]):
            tools,policy=registry('alice','Write a file')
            self.assertIn('write_fixture_report',tools)
            params=tools['describe_tools'](['write_fixture_report'])['tools'][0]['parameters']
            self.assertEqual(params['properties']['lines']['type'],'array')
            validate({'lines':['one','two'],'format':'csv'},params)

    def test_program_restart_recovers_committed_entry_without_repeating_it(self):
        job=self.image_job('Log these 3 separately and confirm when all are finished');urls,store=self.image_mocks()
        calls=[{'tool':'append_diary_entry','arguments':{'title':'Image '+str(i),'markdown':'Observed image '+str(i),'image_numbers':[i]}} for i in (1,2,3)]
        replies=[{'description':'Red'},{'description':'Green'},{'description':'Blue'},plan(('Log image 1','append_diary_entry',''),('Log image 2','append_diary_entry',''),('Log image 3','append_diary_entry',''),groups=[[1],[2],[3]]),choose('compose_operations'),{'calls':calls}]
        with patch('routes.obsidian_images.store_image',side_effect=store),patch('lib.assistant_diary._publish',side_effect=self.publish) as writer:
            with patch('lib.workspace_agent.generate',side_effect=replies):
                for _ in range(30):
                    with Store(self.queue) as q:state=json.loads(q.db.execute('SELECT state FROM jobs').fetchone()[0])
                    if state['phase']=='execute' and state.get('program_execute') and state['program']['index']==1:break
                    self.tick()
            with patch.object(Store,'checkpoint',side_effect=base.Crash()),self.assertRaises(base.Crash):self.tick()
            with Store(self.queue) as q:
                with q.db:q.db.execute('UPDATE jobs SET lease=0')
            self.tick()
            result=self.drain(job['id'],[answer('Saved')],owner='Admin')
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(writer.call_count,3)
        self.assertEqual(result['activity']['completed'],3)

    def test_cancel_program_preserves_completed_effect_without_running_the_rest(self):
        job=self.enqueue('Write file one.md and write file two.md')
        replies=[plan(('Write one.md','write_text_file',''),('Write two.md','write_text_file','')),choose('compose_operations'),{'calls':[
            {'tool':'write_text_file','arguments':{'path':'alice/one.md','content':'One'}},
            {'tool':'write_text_file','arguments':{'path':'alice/two.md','content':'Two'}}]}]
        with patch('lib.workspace_agent.generate',side_effect=replies):
            for _ in range(30):
                with Store(self.queue) as q:state=json.loads(q.db.execute('SELECT state FROM jobs').fetchone()[0])
                if state.get('program',{}).get('index')==1:break
                self.tick()
        with Store(self.queue) as q:q.cancel(job['id'],'alice')
        self.assertEqual(self.get(job['id'])['status'],'canceled')
        self.assertTrue((self.root/'uploads/alice/one.md').exists())
        self.assertFalse((self.root/'uploads/alice/two.md').exists())

    def test_idempotent_diary_storage_outage_resumes_original_intent(self):
        self.app.config['ASSISTANT_DIARY_STATE']=self.root/'diary.sqlite'
        job=self.enqueue('Log a diary entry called Circuit practice',owner='Admin')
        values=[choose('append_diary_entry'),{'title':'Circuit practice','markdown':'Reviewed the provided circuit notes.','image_numbers':[]}]
        with patch('lib.workspace_agent.generate',side_effect=values),patch('lib.assistant_diary._publish',side_effect=RuntimeError('offline')):
            for _ in range(4):self.tick()
        current=self.get(job['id'],'Admin');self.assertEqual(current['status'],'queued',current)
        self.assertEqual(current['code'],'waiting-storage')
        with Store(self.queue) as q:
            with q.db:q.db.execute('UPDATE jobs SET lease=0,retry_at=0')
        with patch('lib.assistant_diary._publish',side_effect=self.publish) as writer:result=self.drain(job['id'],[answer('Saved')],owner='Admin')
        self.assertEqual(result['status'],'ready',result);self.assertEqual(writer.call_count,1)
        with sqlite3.connect(self.root/'diary.sqlite') as db:self.assertEqual(db.execute('SELECT count(*) FROM entries').fetchone()[0],1)

    def test_note_read_repair_uses_its_revision_for_admin_write(self):
        job=self.enqueue('Append the text Updated to the Obsidian note Projects/Study.md',owner='Admin')
        with Store(self.queue) as q:
            state=json.loads(q.db.execute('SELECT state FROM jobs').fetchone()[0]);state['outline_checked']=True;q.checkpoint(job['id'],state)
        seen=[]
        def rpc(value):
            seen.append(value)
            if value['operation']=='read':return {'success':True,'exists':True,'path':value['path'],'text':'Original','revision':'a'*64}
            return {'success':True,'path':value['path'],'revision':'b'*64,'operation_id':value['id'],'status':'saved'}
        with patch('lib.workspace_documents.note_rpc',side_effect=rpc):result=self.drain(job['id'],[choose('write_obsidian_note'),{'path':'Projects/Study.md','content':'Updated','mode':'append','image_numbers':[]},answer('Saved')],owner='Admin')
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(seen[-1]['revision'],'a'*64)
        self.assertEqual(result['result']['actions'][0]['url'][:16],'obsidian://open?')

    def test_global_no_change_request_cannot_gain_semantic_write_capability(self):
        question='Summarize this file. Do not change anything.'
        job=self.enqueue(question)
        replies=[choose('request_capability'),{'tool':'write_text_file','action':'write','user_quote':'Summarize this file.'},answer('I can only summarize it.')]
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertFalse(result['result']['actions'])
        self.assertEqual(list((self.root/'uploads/alice').iterdir()),[])

    def test_program_repairs_a_missing_parent_then_resumes_same_write(self):
        job=self.enqueue('Create the folder if missing and write file alice/Reports/report.md')
        replies=[plan(('Create Reports folder','create_folder',''),('Write report.md','write_text_file','')),choose('compose_operations'),{'calls':[
            {'tool':'write_text_file','arguments':{'path':'alice/Reports/report.md','content':'Preserved report body'}}]},
            choose('create_folder'),{'parent':'alice','name':'Reports'},answer('Saved')]
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual((self.root/'uploads/alice/Reports/report.md').read_text(),'Preserved report body')

    def test_verified_receipt_does_not_depend_on_model_citation_bookkeeping(self):
        job=self.enqueue('Write a file report.md and create a task called Check report')
        replies=[plan(('Write report.md','write_text_file',''),('Create Check report','manage_task','create')),
            choose('write_text_file'),{'path':'alice/report.md','content':'Report'},choose('manage_task'),{'action':'create','title':'Check report'},answer('Done',[999])]
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertTrue((self.root/'uploads/alice/report.md').exists())
        self.assertEqual(len(self.tasks()),1)

    def test_delegated_calendar_window_is_grounded_without_requiring_exact_day(self):
        state={'facts':[],'observations':[{'tool':'read_calendar','result':{}}],'question':'Schedule a study event next week'}
        with self.app.test_request_context('/'):
            session['username']='alice';tools,policy=registry('alice',state['question'])
            value={'title':'Study','start_date':'2026-11-04','all_day':True}
            self.assertEqual(arguments(tools['create_calendar_event'],value,state,policy,base.CLOCK)['start_date'],'2026-11-04')
            with self.assertRaisesRegex(ValueError,'window'):arguments(tools['create_calendar_event'],{**value,'start_date':'2026-11-11'},state,policy,base.CLOCK)

    def test_literal_folder_target_is_bound_instead_of_creating_account_name(self):
        state={'facts':[{'kind':'path','path':'alice','type':'folder'}],'observations':[],'question':'Create a folder alice/Reports and write a report there.'}
        with self.app.test_request_context('/'):
            session['username']='alice';tools,policy=registry('alice',state['question'])
            result=arguments(tools['create_folder'],{'parent':'alice','name':'alice'},state,policy,base.CLOCK)
        self.assertEqual(result,{'parent':'alice','name':'Reports'})

    def test_image_sources_remain_available_after_other_tools_push_them_out_of_recent_context(self):
        from lib.workspace_agent import context
        state={'facts':[],'observations':[{'id':1,'tool':'image_analysis','arguments':{'image':1},'result':{'description':'Red rectangle. WORKOUT 25:00.'}}], 'image_count':1}
        state['observations'] += [{'id':i,'tool':'inspect_path','arguments':{},'result':{'path':'alice'}} for i in range(2,9)]
        with self.app.test_request_context('/'):
            session['username']='alice';tools,_=registry('alice','Log this image')
            data=context({'question':'Log this image'},{'clock':base.CLOCK},state,tools)
        self.assertNotIn('Red rectangle',json.dumps(data['observations']))
        self.assertIn('WORKOUT 25:00',data['attached_images'][0]['description'])

    def test_https_proxy_origin_accepts_same_authority_and_still_requires_csrf(self):
        from routes.workspace_agent import protect
        from werkzeug.exceptions import Forbidden
        for origin,csrf,allowed in [('https://server.example','token',True),('http://server.example','token',True),('https://evil.example','token',False),('https://server.example:444','token',False),('https://server.example','wrong',False)]:
            with self.app.test_request_context('/',base_url='http://server.example',method='POST',headers={'Origin':origin},data={'csrf':csrf}):
                session['logged_in']=True;session['username']='alice';session['assistant_csrf']='token'
                if allowed:protect(True)
                else:
                    with self.assertRaises(Forbidden):protect(True)

    def test_simple_three_logs_need_six_model_calls_and_cannot_select_first_image_again(self):
        job=self.image_job('Log these 3 separately');urls,store=self.image_mocks();seen=[]
        def infer(provider,instruction,context,spec,image=None):
            if image:return {'description':{self.photos[0]:'Red',self.photos[1]:'Green',self.photos[2]:'Blue'}[image]}
            selected=context['attached_images'];self.assertEqual(len(selected),1)
            i=selected[0]['number'];seen.append(i)
            self.assertNotIn('image_numbers',spec['properties'])
            # Even a model that keeps choosing image1 cannot redirect this output.
            return {'title':selected[0]['description'],'markdown':selected[0]['description']+' square.','image_numbers':[1]}
        with patch('lib.workspace_agent.generate',side_effect=infer) as model,patch('routes.obsidian_images.store_image',side_effect=store),patch('lib.assistant_diary._publish',side_effect=self.publish) as writer:
            for _ in range(15):
                if self.get(job['id'],'Admin')['result']:break
                self.tick()
        result=self.get(job['id'],'Admin');self.assertEqual(result['status'],'ready',result)
        self.assertEqual(seen,[1,2,3]);self.assertEqual(model.call_count,6)
        self.assertEqual([v.args[0]['images'] for v in writer.call_args_list],[[u] for u in urls])
        self.assertEqual(result['activity']['completed'],3)

    def test_partial_model_outline_cannot_omit_two_requested_image_outputs(self):
        from lib.workspace_agent import outline
        state={'separate_images':True,'image_count':3,'observations':[],'facts':[],'question':'Log these 3 separately'}
        with self.app.test_request_context('/'):
            session['username']='Admin';g.diary_context={'request_id':'x'}
            tools,policy=registry('Admin',state['question'],True,state=state)
            with patch('lib.workspace_agent.generate',return_value={'steps':[{'text':'Log the console image','tool':'append_diary_entry','action':'','image_numbers':[1]}],'image_groups':[[1],[2],[3]]}):
                outline(None,{'question':state['question'],'provider':'cyborg'},{'clock':base.CLOCK},state,tools,policy)
        self.assertEqual([v['image_numbers'] for v in state['goals']],[[1],[2],[3]])

    def test_general_image_schema_omits_already_saved_selection(self):
        state={'image_count':3,'image_groups':[[1],[2],[3]],'facts':[],'observations':[{'tool':'append_diary_entry','arguments':{'image_numbers':[1]},'result':{'verified':True}}]}
        with self.app.test_request_context('/'):
            session['username']='Admin';g.diary_context={'request_id':'x'}
            tools,policy=registry('Admin','Log separately',True,state=state)
            shape=schema(tools['append_diary_entry'],state,policy)
            self.assertEqual(shape['properties']['image_numbers']['enum'],[[2],[3]])
            with self.assertRaises(ValueError):arguments(tools['append_diary_entry'],{'title':'Again','markdown':'Different words','image_numbers':[1]},state,policy,base.CLOCK)

    def test_regenerate_legacy_partial_log_skips_saved_first_entry_and_reuses_original_time(self):
        job=self.image_job('Log these 3 separately');urls,store=self.image_mocks()
        replies=[{'description':'Red'},{'description':'Green'},{'description':'Blue'},{'title':'Red','markdown':'Red square.'}]
        with patch('routes.obsidian_images.store_image',side_effect=store),patch('lib.assistant_diary._publish',side_effect=self.publish) as writer:
            with patch('lib.workspace_agent.generate',side_effect=replies):
                for _ in range(5):self.tick()
            # Reproduce the real ledger: image1 and image3 saved, image2 missing.
            with Store(self.queue) as q:
                state=json.loads(q.db.execute('SELECT state FROM jobs').fetchone()[0])
                state['goals']=[state['goals'][0],state['goals'][2],state['goals'][1]]
                q.checkpoint(job['id'],state)
            with patch('lib.workspace_agent.generate',return_value={'title':'Blue','markdown':'Blue square.'}):
                self.tick();self.tick()
            with Store(self.queue) as q:
                row=q.db.execute('SELECT state FROM jobs').fetchone();state=json.loads(row[0])
                from datetime import datetime
                with q.db:q.db.execute('UPDATE jobs SET created=?',(datetime.fromisoformat(base.CLOCK['local_datetime']).timestamp(),))
                # Simulate the legacy failure that discarded uploads and full state.
                q.finish(job['id'],state,'Failed',status='failed',code='legacy-test')
                with q.db:q.db.execute("UPDATE jobs SET code='invalid-model-output' WHERE id=?",(job['id'],))
            payload=dict(include_context=True,clock=base.CLOCK,history=[],context='',preferences={},retry=True)
            with self.app.test_request_context('/'):
                session['username']='Admin'
                with Store(self.queue) as q:resumed=q.enqueue('Admin','photos','cyborg','Log these 3 separately',payload,self.photos)
            self.assertEqual(resumed['id'],job['id']);self.assertIsNone(resumed['result'])
            with Store(self.queue) as q:
                retry_payload=json.loads(q.db.execute('SELECT payload FROM jobs').fetchone()[0])
                self.assertEqual(retry_payload['clock']['today'],base.CLOCK['today'])
            result=self.drain(job['id'],[{'description':'Green'},{'title':'Green','markdown':'Green square.'}],owner='Admin')
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(writer.call_count,3)
        self.assertEqual([v.args[0]['images'] for v in writer.call_args_list],[[urls[0]],[urls[2]],[urls[1]]])
        self.assertEqual(result['activity']['completed'],3)

    def test_failed_separate_log_retains_uploads_until_regenerate_or_bounded_pruning(self):
        job=self.image_job('Log these 3 separately')
        with Store(self.queue) as q:
            row=q.db.execute('SELECT state,payload FROM jobs').fetchone();state=json.loads(row[0]);state['separate_images']=True
            q.finish(job['id'],state,'Failed',status='failed',code='invalid-model-output')
            payload=json.loads(q.db.execute('SELECT payload FROM jobs').fetchone()[0])
            self.assertEqual(len(payload['images']),3);self.assertTrue(all(Path(p).exists() for p in payload['images']))
            with q.db:q.db.execute('UPDATE jobs SET updated=0')
            q.prune();self.assertFalse(any(Path(p).exists() for p in payload['images']))

    def test_identical_diary_titles_still_render_distinct_entry_receipts(self):
        from lib.workspace_agent import render_receipts
        state={'actions':[dict(action='diary.saved',entry_id=str(i),title='Same title · Diary/2026-10-28.md',image_numbers=[i],url='obsidian://diary') for i in (1,3)],'observations':[]}
        text=render_receipts(state)
        self.assertEqual(text.count('Saved diary'),2)
        self.assertIn('image 1',text);self.assertIn('image 3',text)
        state['actions'].append(state['actions'][0]);self.assertEqual(render_receipts(state),text)

    def test_changing_prose_cannot_reset_the_same_failed_operation_budget(self):
        from lib.workspace_agent import run_tool
        job=self.enqueue('Write a file report.md')
        state={'observations':[],'facts':[],'actions':[],'question':'Write a file report.md'}
        def write_text_file(path,content):return {'error':'The parent is missing','no_change':True}
        with self.app.test_request_context('/'):
            session['username']='alice';g.assistant_actions=[]
            with Store(self.queue) as q:
                row=dict(q.db.execute('SELECT * FROM jobs').fetchone())
                for i in range(6):
                    result=run_tool(q,row,{},state,write_text_file,{'path':'alice/report.md','content':'different draft '+str(i)})
                    if not result:break
        result=self.get(job['id']);self.assertEqual(result['code'],'needs-input')
        self.assertEqual(i,5)

    def test_busy_local_model_uses_short_retry_without_changing_offline_backoff(self):
        import time
        from lib.workspace_provider import Unavailable
        job=self.enqueue('Hello')
        with Store(self.queue) as q:
            with q.db:q.db.execute('UPDATE jobs SET failures=20')
        with patch('lib.workspace_agent.generate',side_effect=Unavailable('busy',retry_after=5,code='provider-busy')):self.tick()
        with Store(self.queue) as q:retry=q.db.execute('SELECT retry_at FROM jobs').fetchone()[0]
        self.assertGreater(retry-time.time(),3);self.assertLessEqual(retry-time.time(),5)

    def test_only_explicit_native_slot_busy_response_gets_fast_retry(self):
        from lib.workspace_provider import generate,Unavailable
        from unittest.mock import MagicMock
        for message,expected in [('Cyborg is checking another passage. Try again shortly.','provider-busy'),('Cyborg assistant is unavailable or took too long. Your note has not changed.','provider-unavailable')]:
            response=MagicMock(status=503);response.read.return_value=json.dumps({'detail':message}).encode()
            connection=MagicMock();connection.getresponse.return_value=response
            with self.app.app_context(),patch('lib.workspace_provider.http.client.HTTPConnection',return_value=connection):
                with self.assertRaises(Unavailable) as caught:generate('cyborg','Test',{}, {'type':'object'})
            self.assertEqual(caught.exception.code,expected)
