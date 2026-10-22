import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session.update(logged_in=True, username='Admin', assistant_csrf='fixture-token')

    def test_diary_tools_require_page_csrf_and_workspace_context(self):
        for csrf, context, allowed in [('fixture-token','1',True), ('wrong','1',False), ('ผิด','1',False), ('fixture-token','0',False)]:
            observed = []
            def respond(*args, **kwargs):
                observed.append(bool(getattr(g, 'diary_context', None))); return 'Reply', []
            with patch('routes.ai.calendar_clock', return_value={'today':'2026-10-22','local_datetime':'2026-10-22T23:59:00+08:00'}), patch('routes.ai.build_ai_context', return_value=''), patch('routes.ai.handle_file_command', return_value=None), patch('routes.ai.ask_text', side_effect=respond):
                response = self.client.post('/chat', data=dict(msg='Log this for me',csrf=csrf,include_context=context,request_id='same-id'))
            self.assertEqual(response.status_code,200); self.assertEqual(observed,[allowed])

    def test_cross_origin_cannot_save(self):
        response = self.client.post('/chat', data={'msg':'Log this'}, headers={'Origin':'https://other.invalid'})
        self.assertEqual(response.status_code,403)


if __name__ == '__main__': unittest.main()
