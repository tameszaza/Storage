import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask, session, g
from lib.assistant_tools import build_integrated_tools
from lib.ai_client import build_workspace_tools, build_ai_context
from lib import planner, video_downloads


class AssistantToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for user in ('Admin', 'alice', 'bob'):
            (self.root / 'uploads' / user).mkdir(parents=True)
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY='test', UPLOAD_FOLDER=str(self.root/'uploads'),
            PLANNER_DATA_FILE=str(self.root/'planner.json'), ACTIVITY_FILE=str(self.root/'activity.json'),
            PLAYLISTS_FILE=str(self.root/'playlists.json'), PLAYLIST_LOG_DIR=str(self.root/'logs'),
            PLAYLIST_RUNTIME_DIR=str(self.root/'runtime'), MUSIC_FOLDER=str(self.root/'music'), APP_TIMEZONE='Asia/Singapore',
            ICS_CALENDAR_CONFIG_PATH=str(self.root/'ics.json'), ICS_CALENDAR_CACHE_FILE=str(self.root/'ics-cache.json'))
        self.ctx = self.app.test_request_context('/')
        self.ctx.push()
        session['username'] = 'alice'
        self.tools = {f.__name__: f for f in build_integrated_tools('alice')}

    def tearDown(self):
        self.ctx.pop()
        self.tmp.cleanup()

    def test_task_lifecycle_and_account_scope(self):
        manage = self.tools['manage_task']
        task = manage('create', title='Review chapter', due_date='2026-09-08', due_time='12:00')['item']
        key = task['id']
        self.assertTrue(manage('edit', key, title='Review PDF', clear_due=True)['success'])
        self.assertEqual(planner.list_items('alice')['todos'][0]['due_date'], '')
        for _ in range(2): self.assertTrue(manage('complete', key)['item']['completed'])
        self.assertTrue(manage('cancel', key)['item']['canceled'])
        self.assertFalse(manage('reopen', key)['item']['canceled'])
        bob = {f.__name__: f for f in build_integrated_tools('bob')}
        self.assertIn('error', bob['manage_task']('delete', key))
        self.assertEqual(len(planner.list_items('alice')['todos']), 1)
        self.assertTrue(manage('delete', key)['success'])
        self.assertEqual(planner.list_items('alice')['todos'], [])
        self.assertTrue(g.assistant_actions)

    def test_calendar_create_edit_delete(self):
        tools = {f.__name__: f for f in build_workspace_tools('alice', allow_calendar_write=True)}
        item = tools['create_calendar_event']('Reading', '2026-09-08', all_day=True)
        self.assertTrue(item['success'], item)
        key = item['event_id']
        self.assertTrue(tools['edit_calendar_event'](key, title='Study')['success'])
        self.assertTrue(tools['delete_calendar_event'](key)['success'])
        self.assertIn('error', tools['delete_calendar_event'](key))

    def test_pdf_docx_reading_and_protected_paths(self):
        from pypdf import PdfWriter
        from docx import Document
        p = self.root/'uploads/alice/sample.pdf'
        writer = PdfWriter()
        for _ in range(3): writer.add_blank_page(width=100, height=100)
        writer.write(p)
        read = self.tools['read_document']
        result = read('sample.pdf', page_count=1)
        self.assertEqual(result['total_pages'], 3)
        self.assertEqual(result['next_page'], 2)
        self.assertIn('scanned', result['notice'])
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 20 200 Td (The project deadline is October 12.) Tj ET')
        page[NameObject('/Contents')] = stream
        writer.write(self.root/'uploads/alice/text.pdf')
        self.assertIn('October 12', read('text.pdf')['pages'][0]['text'])
        doc = Document(); doc.add_paragraph('Verified document contents.')
        doc.save(self.root/'uploads/alice/test.docx')
        self.assertIn('Verified document contents.', read('test.docx')['content'])
        self.assertIn('error', read('../bob/private.pdf'))
        (self.root/'uploads/alice/link.pdf').symlink_to('/etc/passwd')
        self.assertIn('error', read('link.pdf'))
        self.assertIn('error', read('config.txt'))
        self.assertNotIn('Selected document', build_ai_context('alice', include_tree=False, file_path='alice/sample.pdf'))

    def test_media_scope_and_playlist_queue(self):
        self.assertNotIn('manage_playlist', self.tools)
        admin = {f.__name__: f for f in build_integrated_tools('Admin')}
        manage = admin['manage_playlist']
        row = manage('add', name='Test', url='https://www.youtube.com/playlist?list=PLtest', interval_hours=0)
        self.assertTrue(row['success'], row)
        key = row['item']['id']
        self.assertIn('error', manage('add', name='Duplicate', url='https://www.youtube.com/playlist?list=PLtest'))
        for action in ('pause', 'resume', 'schedule', 'sync'):
            self.assertTrue(manage(action, key)['success'])
        self.assertTrue(manage('detach', key)['success'])

    def test_video_queue_uses_existing_service(self):
        with patch.object(video_downloads, 'DB', self.root/'videos.sqlite'), patch.object(video_downloads, 'ROOT', self.root/'movies'):
            manage = {f.__name__: f for f in build_integrated_tools('Admin')}['manage_video']
            row = manage('add', title='Test video', url='https://example.com/movie.mp4')
            self.assertTrue(row['success'])
            key = row['item']['id']
            self.assertIn('error', manage('retry', key))
            video_downloads.update(key, status='failed')
            self.assertTrue(manage('retry', key)['success'])
            self.assertTrue(manage('delete', key)['success'])
            self.assertEqual(video_downloads.items()[0]['status'], 'deleting')

    def test_read_only_tool_set(self):
        names = {f.__name__ for f in build_workspace_tools('Admin', allow_calendar_write=False)}
        self.assertIn('read_document', names)
        self.assertNotIn('manage_task', names)
        self.assertNotIn('manage_playlist', names)


if __name__ == '__main__': unittest.main()
