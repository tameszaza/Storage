import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask, g, session
from PIL import Image
from lib import assistant_diary as diary
from lib.ai_client import build_workspace_tools, calendar_image_import_requested


class DiaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY='fixture', ASSISTANT_DIARY_STATE=self.root/'diary.sqlite',
                               ACTIVITY_FILE=str(self.root/'activity.json'))
        self.ctx = self.app.test_request_context('/'); self.ctx.push(); self.addCleanup(self.ctx.pop)
        session.update(logged_in=True, username='Admin')
        g.diary_context = dict(request_id='fixture-message', image_bytes=None,
          clock={'today': '2026-10-22', 'local_datetime': '2026-10-22T23:59:00+08:00'})

    def publish(self, data):
        return dict(success=True, path='Diary/'+data['date']+'.md', timestamp=data['timestamp'], status='saved')

    def test_diary_requests_do_not_enter_calendar_image_import(self):
        for message in ["Record this for me today", "Record this meeting photo in today's diary", "Add this schedule to my Obsidian diary"]:
            self.assertFalse(calendar_image_import_requested(message), message)
        self.assertTrue(calendar_image_import_requested('Add the quiz dates in this image to my calendar'))

    def test_admin_scope_and_tools_disabled_without_request_authority(self):
        self.assertIn('error', diary.append_today_diary('alice', 'No', 'No'))
        self.assertEqual(diary.build_diary_tools('alice'), [])
        del g.diary_context
        self.assertEqual(diary.build_diary_tools('Admin'), [])
        self.assertFalse((self.root/'diary.sqlite').exists())

    def test_single_publish_receipt_and_repeat_tool_calls(self):
        with patch.object(diary, '_publish', side_effect=self.publish) as publish:
            result = diary.append_today_diary('Admin', 'Photo', 'Visible facts')
            self.assertTrue(result['success']); self.assertIn('obsidian://tames-diary?', result['open_url'])
            self.assertEqual(diary.append_today_diary('Admin', 'New title', 'Changed response')['status'], 'already_saved')
        self.assertEqual(publish.call_count, 1); self.assertEqual(len(g.assistant_actions), 1)
        self.assertEqual((self.root/'diary.sqlite').stat().st_mode & 0o777, 0o600)

    def test_lost_response_retry_keeps_original_day_and_body_after_midnight(self):
        with patch.object(diary, '_publish', side_effect=RuntimeError('offline')):
            self.assertIn('error', diary.append_today_diary('Admin', 'Original', 'Preserve first content'))
        g.diary_context['clock'] = {'today': '2026-10-23', 'local_datetime': '2026-10-23T00:01:00+08:00'}
        with patch.object(diary, '_publish', side_effect=self.publish) as publish:
            result = diary.append_today_diary('Admin', 'Different', 'Different')
        data = publish.call_args.args[0]
        self.assertEqual(data['date'], '2026-10-22'); self.assertEqual(data['markdown'], 'Preserve first content')
        self.assertTrue(result['success'])

    def test_reuses_image_store_only_on_save(self):
        g.diary_context['image_bytes'] = b'fixture'
        with patch('routes.obsidian_images.store_image', return_value={'url': 'stored-image'}) as store, patch.object(diary, '_publish', side_effect=self.publish) as publish:
            diary.append_today_diary('Admin', 'Photo', 'Analyse')
        store.assert_called_once_with(b'fixture'); self.assertEqual(publish.call_args.args[0]['image'], 'stored-image')

    def test_save_failure_never_emits_success_receipt(self):
        with patch.object(diary, '_publish', side_effect=RuntimeError('offline')):
            result = diary.append_today_diary('Admin', 'Photo', 'Analyse')
        self.assertIn('error', result); self.assertFalse(getattr(g, 'assistant_actions', []))

    def test_bad_content_cannot_create_intent(self):
        for body in ['', '^log-'+'a'*32, 'x'*16001]:
            self.assertIn('error', diary.append_today_diary('Admin', 'Title', body))
        self.assertFalse((self.root/'diary.sqlite').exists())


class ChatAuthorityTests(unittest.TestCase):
    def setUp(self):
        from routes.ai import chat
        self.app = Flask(__name__); self.app.secret_key = 'fixture'
        self.app.add_url_rule('/chat', view_func=chat, methods=['POST'])
        self.client = self.app.test_client();self.client.environ_base["HTTP_X_WORKSPACE_CLIENT"]="1"
        with self.client.session_transaction() as session:
            session.update(logged_in=True, username='Admin', assistant_csrf='fixture-token')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app.config.update(WORKSPACE_AGENT_ROOT=Path(self.tmp.name)/'assistant',ICS_CALENDAR_CONFIG_PATH=str(Path(self.tmp.name)/'ics.json'),APP_TIMEZONE='Asia/Singapore')

    def test_queued_diary_requires_csrf_and_preserves_context_setting(self):
        from lib.workspace_store import Store
        for index,(csrf,context,expected) in enumerate([('fixture-token','1',200),('wrong','1',403),('ผิด','1',403),('fixture-token','0',200)]):
            with patch('lib.ai_client.build_ai_context',return_value=''):
                response=self.client.post('/chat',data=dict(msg='Log this for me',csrf=csrf,include_context=context,request_id='message-'+str(index)))
            self.assertEqual(response.status_code,expected)
            if expected==200:
                with Store(self.app.config['WORKSPACE_AGENT_ROOT']) as queue:
                    row=queue.db.execute('SELECT payload FROM jobs WHERE id=?',(response.json['workspace_job'],)).fetchone()
                    self.assertEqual(json.loads(row[0])['include_context'],context=='1')
    def test_cross_origin_cannot_save(self):
        response = self.client.post('/chat', data={'msg':'Log this'}, headers={'Origin':'https://other.invalid'})
        self.assertEqual(response.status_code,403)


class PhotoLogGenerationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['GEMINI_MODEL'] = 'fixture-model'
        self.ctx = self.app.test_request_context('/')
        self.ctx.push(); self.addCleanup(self.ctx.pop)
        buffer = io.BytesIO(); Image.new('RGB', (8, 8), 'red').save(buffer, 'PNG')
        self.image = buffer.getvalue()
        self.generate = Mock(return_value=SimpleNamespace(text='Fixture response'))
        self.client = SimpleNamespace(models=SimpleNamespace(generate_content=self.generate))

    def test_richer_photo_settings_require_authorized_diary_context(self):
        from lib.ai_client import ask_image
        for username, allowed, context in [('Admin',True,True), ('Admin',False,True),
                                           ('alice',True,True), ('Admin',True,False)]:
            if context:
                g.diary_context = {'request_id': 'fixture'}
            elif hasattr(g, 'diary_context'):
                del g.diary_context
            with self.subTest(username=username, tools=allowed, context=context), \
                 patch('lib.ai_client.get_client', return_value=self.client), \
                 patch('lib.ai_client.build_workspace_tools', return_value=[]):
                ask_image([], self.image, 'Describe this image.', username=username, allow_tools=allowed)
                config = self.generate.call_args.kwargs['config']
                if username == 'Admin' and allowed and context:
                    self.assertEqual(config.temperature, 0.45)
                    self.assertEqual(config.max_output_tokens, 8192)
                    self.assertIn(diary.PHOTO_LOG_WRITING_GUIDANCE, config.system_instruction)
                    tool = diary.build_diary_tools('Admin')[0]
                    self.assertIn(diary.PHOTO_LOG_WRITING_GUIDANCE, tool.__doc__)
                else:
                    self.assertEqual(config.temperature, 0.2)
                    self.assertIsNone(config.max_output_tokens)
                    self.assertNotIn(diary.PHOTO_LOG_WRITING_GUIDANCE, config.system_instruction)

    def test_ordinary_text_keeps_generation_settings(self):
        from lib.ai_client import ask_text
        g.diary_context = {'request_id': 'fixture'}
        with patch('lib.ai_client.get_client', return_value=self.client), \
             patch('lib.ai_client.build_workspace_tools', return_value=[]):
            ask_text([], 'What tasks are due?', username='Admin')
        config = self.generate.call_args.kwargs['config']
        self.assertEqual(config.temperature, 0.2)
        self.assertIsNone(config.max_output_tokens)

    def test_schedule_image_retains_two_deterministic_json_passes(self):
        from lib.ai_client import ask_image
        self.generate.return_value = SimpleNamespace(text='{"events": []}')
        g.diary_context = {'request_id': 'fixture'}
        with patch('lib.ai_client.get_client', return_value=self.client), \
             patch('lib.ai_client.calendar_clock', return_value={'timezone': 'Asia/Singapore'}):
            ask_image([], self.image, 'Describe this image.',
                      'Add the quiz dates in this image to my calendar', username='Admin')
        self.assertEqual(self.generate.call_count, 2)
        for call in self.generate.call_args_list:
            config = call.kwargs['config']
            self.assertEqual(config.temperature, 0)
            self.assertEqual(config.response_mime_type, 'application/json')
            self.assertIsNone(config.max_output_tokens)


if __name__ == '__main__': unittest.main()
