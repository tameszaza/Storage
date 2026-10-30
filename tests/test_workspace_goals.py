import json
import unittest
from unittest.mock import patch
from pathlib import Path
from flask import session
import test_workspace_agent as base
choose=base.choose
answer=base.answer
CLOCK=base.CLOCK
from lib.workspace_store import Store
from lib.workspace_tools import registry,permissions
from lib import planner


def plan(*steps):return {'steps':[{'text':text,'tool':tool,'action':action} for text,tool,action in steps]}

class GoalTests(unittest.TestCase):
    setUp=base.AgentTests.setUp
    enqueue=base.AgentTests.enqueue
    get=base.AgentTests.get
    tick=base.AgentTests.tick
    def drain(self,key,replies,**kwargs):
        return base.AgentTests.drain(self,key,[*replies,{'complete':True,'steps':[]}],**kwargs)
    tasks=base.AgentTests.tasks

    def test_entire_compound_request_finishes_after_intermediate_success(self):
        job=self.enqueue('Create a task called Study circuits and create a task called Review mechanics.')
        replies=[plan(('Create Study circuits','manage_task','create'),('Create Review mechanics','manage_task','create')),
            choose('manage_task'),{'action':'create','title':'Study circuits'},answer('Done',[1,2]),
            choose('manage_task'),{'action':'create','title':'Review mechanics'},answer('Done',[1,2,3])]
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual({t['title'] for t in self.tasks()},{'Study circuits','Review mechanics'})
        self.assertEqual(result['activity']['completed'],2)

    def test_many_useful_interactions_continue_past_old_step_limits(self):
        titles=['Study chapter '+str(i) for i in range(14)]
        job=self.enqueue('Create tasks for all fourteen chapters, one for each chapter.')
        replies=[plan(*[(t,'manage_task','create') for t in titles])]
        for t in titles:replies += [choose('manage_task'),{'action':'create','title':t},answer('Done')]
        result=self.drain(job['id'],replies,max_steps=110)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(len(self.tasks()),14)
        self.assertEqual(result['activity']['completed'],14)

    def test_followup_resolves_context_and_refreshes_actual_target(self):
        first=self.enqueue('Create a task Study circuits',key='first')
        result=self.drain(first['id'],[choose('manage_task'),{'action':'create','title':'Study circuits'},answer('Done')])
        self.assertEqual(result['status'],'ready',result)
        second=self.enqueue('Set its priority to high',key='second',context_jobs=[first['id']])
        replies=[choose('manage_task'),{'action':'edit','task_id':self.tasks()[0]['id'],'priority':'high'},answer('Done')]
        result=self.drain(second['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(self.tasks()[0]['priority'],'high')
        self.assertEqual(len(self.tasks()),1)

    def test_read_focus_is_remembered_for_followup_without_any_write_receipt(self):
        with self.app.app_context():item=planner.create_todo('alice',{'title':'Study circuits'})
        first=self.enqueue('Show my Study circuits task',key='first')
        result=self.drain(first['id'],[choose('read_tasks'),{},answer('Study circuits',[1])])
        self.assertTrue(result['result']['workspace_context']['focus'])
        second=self.enqueue('Mark it done',key='second',context_jobs=[first['id']])
        result=self.drain(second['id'],[choose('manage_task'),{'action':'complete','task_id':self.tasks()[0]['id']},answer('Done')])
        self.assertEqual(result['status'],'ready',result)
        self.assertTrue(self.tasks()[0]['completed'])

    def test_missing_path_prerequisites_are_resolved_without_more_model_turns(self):
        folder=self.root/'uploads/alice';(folder/'Done').mkdir();(folder/'report.txt').write_text('data')
        job=self.enqueue('Move file alice/report.txt into alice/Done')
        replies=[choose('move_file'),{'source':'alice/report.txt','destination_folder':'alice/Done'},answer('Done')]
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertTrue((folder/'Done/report.txt').is_file())
        self.assertFalse((folder/'report.txt').exists())

    def test_goal_level_organization_can_create_folder_then_move_multiple_files(self):
        folder=self.root/'uploads/alice'
        for name in ['circuits-a.txt','circuits-b.txt']:(folder/name).write_text(name)
        job=self.enqueue('Organize all files in alice by course.')
        replies=[plan(('Create course folder Circuits','create_folder','create'),('Move circuits-a.txt into Circuits','move_file','move'),('Move circuits-b.txt into Circuits','move_file','move')),
            choose('create_folder'),{'parent':'alice','name':'Circuits'},answer('Done'),
            choose('move_file'),{'source':'alice/circuits-a.txt','destination_folder':'alice/Circuits'},answer('Done'),
            choose('move_file'),{'source':'alice/circuits-b.txt','destination_folder':'alice/Circuits'},answer('Done')]
        result=self.drain(job['id'],replies,max_steps=60)
        self.assertEqual(result['status'],'ready',result)
        self.assertTrue((folder/'Circuits/circuits-a.txt').is_file())
        self.assertTrue((folder/'Circuits/circuits-b.txt').is_file())

    def test_premature_question_triggers_local_search_before_asking_user(self):
        job=self.enqueue('Create a task Study circuits')
        result=self.drain(job['id'],[{'next':'clarify','tool':'none','answer':'Do you have any existing tasks?','evidence':[]},choose('manage_task'),{'action':'create','title':'Study circuits'},answer('Done')])
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(len(self.tasks()),1)

    def test_status_names_actual_task_and_tool_and_wait_reason(self):
        job=self.enqueue('Create a task Study circuits')
        with Store(self.queue) as store:
            row=store.db.execute('SELECT state FROM jobs WHERE id=?',(job['id'],)).fetchone();state=json.loads(row[0])
            state.update(phase='execute',tool='manage_task',arguments={'action':'create','title':'Study circuits'})
            store.checkpoint(job['id'],state)
            active=store.get(job['id'],'alice')['activity']
            self.assertIn('Study circuits',active['step']);self.assertNotIn('Queued',active['step'])
            store.checkpoint(job['id'],state,code='waiting-provider',delay=10)
            self.assertIn('Waiting for Cyborg',store.get(job['id'],'alice')['activity']['step'])

    def test_context_receipt_does_not_include_private_file_body(self):
        (self.root/'uploads/alice/notes.txt').write_text('PRIVATE_DOCUMENT_SENTINEL')
        job=self.enqueue('Read alice/notes.txt')
        result=self.drain(job['id'],[choose('read_text_file'),{'path':'alice/notes.txt'},answer('Read it',[1])])
        context=result['result']['workspace_context']
        self.assertNotIn('PRIVATE_DOCUMENT_SENTINEL',json.dumps(context))
        self.assertEqual(context['focus']['path'],'alice/notes.txt')

    def test_organize_plan_only_does_not_grant_file_changes(self):
        self.assertFalse(any(permissions('Propose how to organize all my files').values()))
        self.assertFalse(any(permissions('Organize my files, but do not change anything').values()))

    def test_out_of_order_effects_are_credited_without_repeating_writes(self):
        job=self.enqueue('Create a task Study circuits and create a task Review mechanics')
        result=self.drain(job['id'],[plan(('Create Study circuits','manage_task','create'),('Create Review mechanics','manage_task','create')),
            choose('manage_task'),{'action':'create','title':'Study circuits'},choose('manage_task'),{'action':'create','title':'Review mechanics'},answer('Done')])
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(result['activity']['completed'],2)
        self.assertEqual(len(self.tasks()),2)

    def test_equivalent_reader_and_out_of_order_changes_fulfill_goal(self):
        folder=self.root/'uploads/alice';(folder/'report.txt').write_text('data');(folder/'Done').mkdir()
        job=self.enqueue('Find report.txt and move the file into alice/Done')
        result=self.drain(job['id'],[plan(('Find report.txt','search_workspace',''),('Move report.txt into Done','move_file','')),
            choose('list_directory'),{'path':'alice'},choose('move_file'),{'source':'alice/report.txt','destination_folder':'alice/Done'},answer('Done')])
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(result['activity']['completed'],2)
        self.assertTrue((folder/'Done/report.txt').is_file())

    def test_completion_review_finds_and_finishes_an_omitted_outcome(self):
        job=self.enqueue('Create a task Study circuits and create a task Review mechanics')
        replies=[plan(('Create Study circuits','manage_task','create')),choose('manage_task'),{'action':'create','title':'Study circuits'},answer('Done'),
            {'complete':False,**plan(('Create Review mechanics','manage_task','create'))},choose('manage_task'),{'action':'create','title':'Review mechanics'},answer('Done')]
        result=self.drain(job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual({t['title'] for t in self.tasks()},{'Study circuits','Review mechanics'})

    def test_account_relative_paths_resolve_without_unnecessary_repair(self):
        folder=self.root/'uploads/alice';(folder/'Done').mkdir();(folder/'report.txt').write_text('data')
        job=self.enqueue('Move file report.txt into Done')
        result=self.drain(job['id'],[choose('move_file'),{'source':'report.txt','destination_folder':'Done'},answer('Done')])
        self.assertEqual(result['status'],'ready',result)
        self.assertTrue((folder/'Done/report.txt').is_file())

    def test_recovered_provider_status_shows_actual_work_instead_of_old_wait(self):
        job=self.enqueue('Create a task Study circuits')
        with Store(self.queue) as q:
            state=json.loads(q.db.execute('SELECT state FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
            q.checkpoint(job['id'],state,code='waiting-provider',delay=0)
            q.claim()
            self.assertNotIn('Waiting for Cyborg',q.get(job['id'],'alice')['activity']['step'])

    def test_completed_review_can_include_redundant_step_metadata(self):
        job=self.enqueue('Create a task Study circuits and create a task Review mechanics')
        replies=[plan(('Create Study circuits','manage_task','create'),('Create Review mechanics','manage_task','create')),
            choose('manage_task'),{'action':'create','title':'Study circuits'},choose('manage_task'),{'action':'create','title':'Review mechanics'},answer('Done'),
            {'complete':True,**plan(('Created Study circuits','manage_task','create'),('Created Review mechanics','manage_task','create'))}]
        result=base.AgentTests.drain(self,job['id'],replies)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(len(self.tasks()),2)

    def test_permanent_provider_configuration_failure_is_actionable(self):
        from lib.workspace_provider import Unavailable
        job=self.enqueue('Hello',provider='google')
        with patch('lib.workspace_agent.generate',side_effect=Unavailable('setup',code='provider-configuration')):self.tick()
        result=self.get(job['id'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['code'],'provider-configuration')
        self.assertIn('configuration',result['result']['response'])
