import tempfile
import unittest
import io
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask, session, g
from lib.assistant_tools import build_integrated_tools
from lib.ai_client import ask_image, ask_text, build_workspace_tools, build_ai_context, calendar_write_requested, calendar_image_import_requested, explicit_calendar_year
from lib import planner, video_downloads
from PIL import Image


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
            ICS_CALENDAR_CONFIG_PATH=str(self.root/'ics.json'), ICS_CALENDAR_CACHE_FILE=str(self.root/'ics-cache.json'),
            CALENDAR_IMPORT_DIR=str(self.root/'calendar-imports'),
            GEMINI_MODEL='test-model')
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
        repeated = tools['create_calendar_event']('reading', '2026-09-08', all_day=True)
        self.assertEqual(repeated['status'], 'already_exists')
        self.assertEqual(len(planner.list_items('alice')['events']), 1)
        key = item['event_id']
        self.assertTrue(tools['edit_calendar_event'](key, title='Study')['success'])
        self.assertTrue(tools['delete_calendar_event'](key)['success'])
        self.assertIn('error', tools['delete_calendar_event'](key))

    def test_text_calendar_create_checks_published_events_before_writing(self):
        from lib.ics_calendar import PublishedCalendarError
        tools = {f.__name__: f for f in build_workspace_tools('Admin', allow_calendar_write=True)}
        create = tools['create_calendar_event']
        published = {
            'events': [{'title': 'CC0015 quiz', 'start_date': '2026-09-25',
                        'end_date': '2026-09-25', 'all_day': True, 'source': 'published'}],
            'published_calendar_stale': False, 'truncated': False,
        }
        with patch('lib.calendar_conflicts.cache_needs_refresh', return_value=True), patch(
            'lib.calendar_conflicts.sync_calendar'
        ) as refresh, patch('lib.ai_client.read_calendar_range', return_value=published):
            result = create('CC0015 quiz', '2026-09-25')
        self.assertEqual(result['status'], 'already_exists')
        self.assertEqual(result['source'], 'published')
        refresh.assert_called_once_with(date(2026, 9, 25), date(2026, 9, 26))
        self.assertEqual(planner.list_items('Admin')['events'], [])

        with patch('lib.calendar_conflicts.cache_needs_refresh', return_value=True), patch(
            'lib.calendar_conflicts.sync_calendar', side_effect=PublishedCalendarError('offline')
        ):
            result = create('Another quiz', '2026-09-26')
        self.assertIn('could not be refreshed', result['error'])
        self.assertEqual(planner.list_items('Admin')['events'], [])

        with patch('lib.calendar_conflicts.cache_needs_refresh', return_value=False), patch(
            'lib.ai_client.read_calendar_range', return_value=published,
        ):
            result = create('CC0015 quiz 1', '2026-09-25')
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(planner.list_items('Admin')['events'], [])

        numbered = dict(published, events=[dict(published['events'][0], title='CC0015 quiz 2')])
        with patch('lib.calendar_conflicts.cache_needs_refresh', return_value=False), patch(
            'lib.ai_client.read_calendar_range', return_value=numbered,
        ):
            result = create('CC0015 quiz 1', '2026-09-25')
        self.assertEqual(result['status'], 'created')

    def test_calendar_intent_keeps_positive_request_with_negative_guard(self):
        self.assertTrue(calendar_write_requested(
            "Add these quiz dates to my calendar, but don't add duplicates or unclear dates."
        ))
        self.assertTrue(calendar_write_requested(
            "Don't add duplicates; add the clear quiz dates to my calendar."
        ))
        self.assertFalse(calendar_write_requested("Don't add these quiz dates to my calendar."))
        self.assertFalse(calendar_write_requested(
            "Don't add these to my calendar; create a task for tomorrow."
        ))
        self.assertFalse(calendar_write_requested("How can I add quiz dates to my calendar?"))
        self.assertTrue(calendar_write_requested("Delete the calendar event on Friday."))
        self.assertFalse(calendar_write_requested("Don't delete the calendar event on Friday."))
        self.assertTrue(calendar_write_requested(
            "Add the dates in this image to my calendar after I review them. Flag uncertain entries and do not save anything yet."
        ))
        self.assertTrue(calendar_image_import_requested("Add the quiz dates in this image to my calendar."))
        self.assertFalse(calendar_image_import_requested("Delete the calendar event shown in this screenshot."))
        self.assertFalse(calendar_image_import_requested("Don't add these quiz dates to my calendar; delete the old event."))
        self.assertFalse(calendar_image_import_requested("How can I add these dates to my calendar?"))
        self.assertFalse(calendar_image_import_requested("Schedule the task shown in this image for Friday."))
        self.assertFalse(calendar_write_requested("Schedule my todo for Friday."))
        self.assertTrue(calendar_write_requested("Update my weekly schedule."))

    def test_image_calendar_import_rechecks_and_skips_duplicates(self):
        from lib.assistant_calendar_import import import_verified_events
        row = {'title': 'CC0015 quiz', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
               'all_day': True, 'start_time': '', 'end_time': ''}
        first = import_verified_events('alice', [row, dict(row)], [row])
        self.assertEqual(len(first['created']), 1)
        self.assertEqual(len(planner.list_items('alice')['events']), 1)
        again = import_verified_events('alice', [row], [row])
        self.assertEqual(len(again['created']), 0)
        self.assertEqual(len(again['existing']), 1)
        mismatch = import_verified_events('alice', [dict(row, title='CC0015 final')], [row])
        self.assertEqual(len(mismatch['created']), 0)
        self.assertEqual(len(mismatch['review']), 2)
        unknown_year = import_verified_events('alice', [dict(row, title='Next quiz', date='')], [row])
        self.assertEqual(len(unknown_year['created']), 0)
        self.assertEqual(len(unknown_year['review']), 2)
        inferred_year = dict(row, title='Third quiz', date_evidence='25 Sep')
        self.assertEqual(len(import_verified_events('alice', [inferred_year], [inferred_year])['created']), 0)

    def test_image_calendar_import_requires_matching_unambiguous_date_evidence(self):
        from lib.assistant_calendar_import import import_verified_events

        def check(title, iso_date, evidence, *, hint=None):
            row = {'title': title, 'date': iso_date, 'date_evidence': evidence,
                   'all_day': True, 'start_time': '', 'end_time': ''}
            return import_verified_events('alice', [row], [row], year_hint=hint)

        self.assertEqual(len(check('Wrong day', '2026-09-25', '26 Sep 2026')['review']), 1)
        self.assertEqual(len(check('Wrong month', '2026-09-25', '25 Oct 2026')['review']), 1)
        self.assertEqual(len(check('Ambiguous', '2026-09-05', '05/09/2026')['review']), 1)
        self.assertEqual(len(check('Multiple dates', '2026-09-25', '25 Sep 2026 / 26 Sep 2026')['review']), 1)
        self.assertEqual(len(check('No year', '2026-09-25', '25 Sep')['review']), 1)
        self.assertEqual(len(check('Year supplied', '2026-09-25', '25 Sep', hint=2026)['created']), 1)
        self.assertEqual(len(check('ISO date', '2026-09-05', '2026-09-05')['created']), 1)
        self.assertEqual(len(check('Thai date', '2026-09-25', '25 ก.ย. 2569')['created']), 1)
        self.assertEqual(len(check('Wrong Thai year', '2026-09-25', '25 ก.ย. 2568')['review']), 1)
        self.assertEqual(len(check('Numeric date', '2026-09-25', '25/09/2026')['created']), 1)
        self.assertEqual(len(check('Short year', '2026-09-25', '25 Sep 26')['created']), 1)
        self.assertEqual(len(check('Matching weekday', '2026-09-25', 'Fri 25 Sep 2026')['created']), 1)
        self.assertEqual(len(check('Wrong weekday', '2026-09-25', 'Thu 25 Sep 2026')['review']), 1)
        self.assertEqual(len(check('Siamese date example', '2026-09-25', 'วันศุกร์ 25 ก.ย. 2569')['created']), 1)
        self.assertEqual(len(check('Mismatched Thai day', '2026-09-25', 'วันพฤหัสบดี 25 ก.ย. 2569')['review']), 1)

    def test_image_calendar_import_checks_events_spanning_the_target_day(self):
        from lib.assistant_calendar_import import import_verified_events
        planner.create_event_once('alice', {
            'title': 'Quiz week', 'date': '2026-09-23', 'end_date': '2026-09-27',
            'all_day': True,
        })
        row = {'title': 'Quiz week', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
               'all_day': True, 'start_time': '', 'end_time': ''}
        result = import_verified_events('alice', [row], [row])
        self.assertEqual(result['created'], [])
        self.assertEqual(len(result['review']), 1)
        self.assertEqual(len(planner.list_items('alice')['events']), 1)

        published = {'events': [{'title': 'Quiz week', 'start_date': '2026-09-23',
                                  'end_date': '2026-09-27', 'all_day': True}],
                     'published_calendar_stale': False, 'truncated': False}
        with patch('lib.calendar_conflicts.cache_needs_refresh', return_value=False), patch(
            'lib.assistant_calendar_import.read_calendar_range', return_value=published,
        ):
            admin_result = import_verified_events('Admin', [row], [row])
        self.assertEqual(admin_result['created'], [])
        self.assertEqual(len(admin_result['review']), 1)
        self.assertEqual(planner.list_items('Admin')['events'], [])

    def test_image_calendar_import_allows_same_title_on_another_day(self):
        from lib.assistant_calendar_import import import_verified_events
        planner.create_event_once('alice', {'title': 'Weekly quiz', 'date': '2026-09-18', 'all_day': True})
        row = {'title': 'Weekly quiz', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
               'all_day': True, 'start_time': '', 'end_time': ''}
        result = import_verified_events('alice', [row], [row])
        self.assertEqual(len(result['created']), 1)
        self.assertEqual(len(planner.list_items('alice')['events']), 2)

    def test_image_calendar_import_requires_visible_clock_evidence(self):
        from lib.assistant_calendar_import import import_verified_events
        row = {'title': 'Timed quiz', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
               'all_day': False, 'start_time': '09:30', 'end_time': '10:45',
               'time_evidence': '9.30 am – 10:45 am'}
        created = import_verified_events('alice', [row], [row])
        self.assertEqual(len(created['created']), 1)
        self.assertEqual(created['created'][0]['start_time'], '09:30')
        missing = dict(row, title='No visible clock', time_evidence='')
        result = import_verified_events('alice', [missing], [missing])
        self.assertEqual(len(result['created']), 0)
        self.assertIn('time does not match', result['review'][0])
        wrong = dict(row, title='Wrong clock', time_evidence='9:30 am – 11:45 am')
        result = import_verified_events('alice', [wrong], [wrong])
        self.assertEqual(len(result['created']), 0)
        self.assertIn('time does not match', result['review'][0])
        afternoon = dict(row, title='Afternoon quiz', start_time='13:30', end_time='',
                         time_evidence='1:30 p.m.')
        self.assertEqual(len(import_verified_events('alice', [afternoon], [afternoon])['created']), 1)
        end_disagreement = import_verified_events('alice', [dict(row, title='Different end')],
                                                   [dict(row, title='Different end', end_time='10:30',
                                                         time_evidence='9:30 am – 10:30 am')])
        self.assertEqual(len(end_disagreement['created']), 0)
        self.assertIn('image checks disagreed', end_disagreement['review'][0])
        inconsistent = dict(row, title='All-day conflict', all_day=True)
        result = import_verified_events('alice', [inconsistent], [inconsistent])
        self.assertEqual(len(result['created']), 0)
        self.assertIn('all-day event', result['review'][0])

    def test_image_calendar_import_refreshes_published_calendar_before_writing(self):
        from lib.assistant_calendar_import import import_verified_events
        row = {'title': 'Quiz 1', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
               'all_day': True, 'start_time': '', 'end_time': ''}
        published = {'events': [{'title': 'Quiz 1', 'start_date': '2026-09-25',
                                  'all_day': True, 'start_time': ''}],
                     'published_calendar_stale': False, 'truncated': False}
        with patch('lib.calendar_conflicts.cache_needs_refresh', return_value=True), patch(
            'lib.calendar_conflicts.sync_calendar'
        ) as refresh, patch('lib.assistant_calendar_import.read_calendar_range', return_value=published):
            result = import_verified_events('Admin', [row], [row])
        refresh.assert_called_once_with(date(2026, 9, 25), date(2026, 9, 26))
        self.assertEqual(len(result['existing']), 1)
        self.assertEqual(planner.list_items('Admin')['events'], [])

        from lib.ics_calendar import PublishedCalendarError
        with patch('lib.calendar_conflicts.cache_needs_refresh', return_value=True), patch(
            'lib.calendar_conflicts.sync_calendar', side_effect=PublishedCalendarError('offline')
        ):
            result = import_verified_events('Admin', [row], [row])
        self.assertEqual(len(result['created']), 0)
        self.assertIn('could not be refreshed', result['review'][0])
        self.assertEqual(planner.list_items('Admin')['events'], [])

        with patch('lib.calendar_conflicts.cache_needs_refresh', return_value=False), patch(
            'lib.assistant_calendar_import.read_calendar_range', return_value={
                'events': [], 'published_calendar_stale': False, 'truncated': True,
            }
        ):
            result = import_verified_events('Admin', [row], [row])
        self.assertEqual(len(result['created']), 0)
        self.assertIn('could not be fully verified', result['review'][0])

    def test_image_verification_does_not_see_first_extraction(self):
        rows = {'events': [{'title': 'Untrusted proposed title', 'date': '2026-09-25',
                            'date_evidence': '25 Sep 2026', 'all_day': True,
                            'start_time': '', 'end_time': ''}]}
        calls = []

        class FakeModels:
            def generate_content(self, *, model, contents, config):
                calls.append(contents)
                return SimpleNamespace(text=json.dumps(rows))

        image = io.BytesIO()
        Image.new('RGB', (20, 20), 'white').save(image, format='PNG')
        with patch('lib.ai_client.get_client', return_value=SimpleNamespace(models=FakeModels())), patch(
            'lib.ai_client.url_for', return_value='/chat/calendar-import/test',
        ):
            answer, _ = ask_image([], image.getvalue(), 'Add these dates to my calendar', username='alice')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('Untrusted proposed title', calls[1][0])
        self.assertIn('Nothing has been added yet', answer)
        self.assertEqual(planner.list_items('alice')['events'], [])
        self.assertEqual(g.assistant_review_url, '/chat/calendar-import/test')

    def test_image_calendar_draft_requires_owner_and_confirming_rechecks_duplicates(self):
        from lib.assistant_calendar_drafts import confirm_draft, create_draft, draft_image_path, load_draft
        row = {'title': 'CC0015 quiz', 'date': '2026-09-25', 'all_day': True,
               'start_time': '', 'end_time': '', 'date_evidence': '25 Sep 2026', 'time_evidence': ''}
        token = create_draft('alice', [row], [], Image.new('RGB', (20, 20), 'white'))
        self.assertIsNotNone(load_draft('alice', token))
        self.assertIsNone(load_draft('bob', token))
        self.assertIsNone(draft_image_path('bob', token))
        self.assertEqual(planner.list_items('alice')['events'], [])
        with self.assertRaises(ValueError):
            confirm_draft('alice', token, [dict(row, date='bad-date')])
        self.assertFalse(load_draft('alice', token)['confirmed'])
        result = confirm_draft('alice', token, [row])
        self.assertEqual(len(result['created']), 1)
        self.assertEqual(len(confirm_draft('alice', token, [row])['created']), 1)
        self.assertEqual(len(planner.list_items('alice')['events']), 1)

    def test_image_calendar_draft_expires(self):
        from lib.assistant_calendar_drafts import create_draft, load_draft
        token = create_draft('alice', [{'title': 'Quiz', 'date': '2026-09-25'}], [], Image.new('RGB', (20, 20), 'white'))
        with patch('lib.assistant_calendar_drafts.time.time', return_value=10000000000):
            self.assertIsNone(load_draft('alice', token))

    def test_disagreed_image_date_is_reviewable_but_unselected(self):
        from lib.assistant_calendar_import import prepare_verified_events
        first = {'title': 'Quiz 1', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
                 'all_day': True, 'start_time': '', 'end_time': ''}
        second = dict(first, date='2026-09-26', date_evidence='26 Sep 2026')
        prepared = prepare_verified_events([first], [second])
        self.assertEqual(len(prepared['candidates']), 2)
        self.assertFalse(prepared['candidates'][0]['verified'])
        self.assertFalse(prepared['candidates'][1]['verified'])
        self.assertEqual(prepared['candidates'][1]['date'], '2026-09-26')
        self.assertIn('select only the correct one', prepared['candidates'][1]['review_reason'])
        self.assertEqual(prepared['candidates'][0]['conflict_group'], prepared['candidates'][1]['conflict_group'])
        self.assertEqual(planner.list_items('alice')['events'], [])

        class FakeModels:
            def __init__(self): self.calls = 0
            def generate_content(self, *, model, contents, config):
                self.calls += 1
                return SimpleNamespace(text=json.dumps({'events': [first if self.calls == 1 else second]}))

        image = io.BytesIO()
        Image.new('RGB', (20, 20), 'white').save(image, format='PNG')
        with patch('lib.ai_client.get_client', return_value=SimpleNamespace(models=FakeModels())), patch(
            'lib.ai_client.url_for', return_value='/chat/calendar-import/test',
        ):
            response, _ = ask_image([], image.getvalue(), 'Add quiz dates to my calendar', username='alice')
        self.assertIn('2 uncertain entries', response)
        self.assertEqual(g.assistant_review_label, 'Review dates')
        self.assertEqual(planner.list_items('alice')['events'], [])

    def test_second_image_read_can_propose_an_omitted_event_for_manual_review(self):
        from lib.assistant_calendar_import import prepare_verified_events
        first = {'title': 'Quiz 1', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
                 'all_day': True, 'start_time': '', 'end_time': ''}
        second = {'title': 'Quiz 2', 'date': '2026-09-26', 'date_evidence': '26 Sep 2026',
                  'all_day': True, 'start_time': '', 'end_time': ''}
        prepared = prepare_verified_events([first], [first, second])
        self.assertEqual(len(prepared['candidates']), 2)
        self.assertTrue(prepared['candidates'][0]['verified'])
        self.assertFalse(prepared['candidates'][1]['verified'])
        self.assertEqual(prepared['candidates'][1]['title'], 'Quiz 2')
        self.assertEqual(planner.list_items('alice')['events'], [])

    def test_conflicting_image_dates_cannot_both_be_confirmed(self):
        from lib.assistant_calendar_import import import_confirmed_events, prepare_verified_events
        first = {'title': 'Quiz 1', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
                 'all_day': True, 'start_time': '', 'end_time': ''}
        second = dict(first, date='2026-09-26', date_evidence='26 Sep 2026')
        candidates = prepare_verified_events([first], [second])['candidates']
        with self.assertRaisesRegex(ValueError, 'Select only the correct date'):
            import_confirmed_events('alice', candidates)
        self.assertEqual(planner.list_items('alice')['events'], [])
        result = import_confirmed_events('alice', [candidates[1]])
        self.assertEqual(len(result['created']), 1)
        self.assertEqual(result['created'][0]['date'], '2026-09-26')

    def test_near_matching_image_titles_cannot_create_two_dates(self):
        from lib.assistant_calendar_import import import_confirmed_events, prepare_verified_events
        first = {'title': 'CC0015 Quiz 1', 'date': '2026-09-25', 'date_evidence': '25 Sep 2026',
                 'all_day': True, 'start_time': '', 'end_time': ''}
        second = dict(first, title='CC0015 Quiz', date='2026-09-26', date_evidence='26 Sep 2026')
        candidates = prepare_verified_events([first], [second])['candidates']
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]['conflict_group'], candidates[1]['conflict_group'])
        with self.assertRaisesRegex(ValueError, 'Select only the correct date'):
            import_confirmed_events('alice', candidates)
        self.assertEqual(planner.list_items('alice')['events'], [])

        separate = dict(second, title='CC0015 Quiz 2')
        distinct = prepare_verified_events([first], [separate])['candidates']
        self.assertFalse(any(candidate.get('conflict_group') for candidate in distinct))

    def test_image_calendar_review_route_requires_owner_and_csrf(self):
        from lib.assistant_calendar_drafts import create_draft
        from routes.ai import calendar_import_image, calendar_import_review
        row = {'title': 'CC0015 quiz', 'date': '2026-09-25', 'all_day': True,
               'start_time': '', 'end_time': '', 'date_evidence': '25 Sep 2026'}
        token = create_draft('alice', [row], [], Image.new('RGB', (20, 20), 'white'))
        app = Flask(__name__)
        app.config.update(self.app.config)
        app.add_url_rule('/review/<token>', 'calendar_import_review', calendar_import_review, methods=['GET', 'POST'])
        app.add_url_rule('/review/<token>/image', 'calendar_import_image', calendar_import_image)
        client = app.test_client()
        with client.session_transaction() as stored:
            stored['logged_in'] = True
            stored['username'] = 'bob'
        with patch('routes.ai.render_template', return_value='review'):
            self.assertEqual(client.get(f'/review/{token}').status_code, 404)
            self.assertEqual(client.get(f'/review/{token}/image').status_code, 404)
            with client.session_transaction() as stored:
                stored['username'] = 'alice'
                stored['calendar_import_csrf'] = 'test-csrf'
            self.assertEqual(client.get(f'/review/{token}').status_code, 200)
            image_response = client.get(f'/review/{token}/image')
            self.assertEqual(image_response.status_code, 200)
            image_response.close()
            payload = {'selected': '0', 'title_0': row['title'], 'date_0': row['date'], 'timing_0': 'all_day'}
            self.assertEqual(client.post(f'/review/{token}', data=payload).status_code, 403)
            self.assertEqual(planner.list_items('alice')['events'], [])
            payload['csrf'] = 'test-csrf'
            self.assertEqual(client.post(f'/review/{token}', data=payload).status_code, 303)
            self.assertEqual(len(planner.list_items('alice')['events']), 1)

    def test_chat_returns_calendar_review_action_without_writing_event(self):
        from routes.ai import chat
        app = Flask(__name__)
        app.config.update(self.app.config)
        app.add_url_rule('/chat', 'chat', chat, methods=['POST'])
        client = app.test_client()
        with client.session_transaction() as stored:
            stored['logged_in'] = True
            stored['username'] = 'alice'

        def staged_image(*args, **kwargs):
            g.assistant_review_url = '/chat/calendar-import/review-token'
            g.assistant_review_label = 'Review dates'
            return 'Nothing has been added yet.', []

        with patch('routes.ai.ask_image', side_effect=staged_image), patch(
            'routes.ai.build_ai_context', return_value='',
        ), patch('routes.ai.initial_history', return_value=[]):
            response = client.post('/chat', data={
                'msg': 'Add the dates to my calendar',
                'image': (io.BytesIO(b'image-bytes'), 'schedule.png'),
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['action_url'], '/chat/calendar-import/review-token')
        self.assertEqual(response.json['action_label'], 'Review dates')
        self.assertEqual(planner.list_items('alice')['events'], [])

    def test_chat_photo_calendar_prompt_needs_an_attached_image(self):
        from routes.ai import chat
        app = Flask(__name__)
        app.config.update(self.app.config)
        app.add_url_rule('/chat', 'chat', chat, methods=['POST'])
        client = app.test_client()
        with client.session_transaction() as stored:
            stored['logged_in'] = True
            stored['username'] = 'alice'
        response = client.post('/chat', data={
            'msg': 'Add the dates in this image to my calendar after I review them.',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('Attach the schedule image first', response.json['response'])
        self.assertEqual(planner.list_items('alice')['events'], [])

    def test_explicit_calendar_year_accepts_thai_buddhist_year(self):
        self.assertEqual(explicit_calendar_year('Add these quiz dates for 2569'), 2026)
        self.assertEqual(explicit_calendar_year('Use the 2026 timetable'), 2026)
        self.assertIsNone(explicit_calendar_year('Dates for 2025 or 2026'))

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

    def test_text_calendar_tools_require_an_explicit_calendar_change(self):
        offered = []

        class FakeModels:
            def generate_content(self, *, model, contents, config):
                offered.append({tool.__name__ for tool in config.tools})
                return SimpleNamespace(text='Done')

        with patch('lib.ai_client.get_client', return_value=SimpleNamespace(models=FakeModels())):
            ask_text([], 'What is on my calendar this week?', username='alice')
            ask_text([], 'Add a quiz to my calendar on Friday.', username='alice')
        self.assertIn('manage_task', offered[0])
        self.assertNotIn('create_calendar_event', offered[0])
        self.assertNotIn('edit_calendar_event', offered[0])
        self.assertNotIn('delete_calendar_event', offered[0])
        self.assertIn('create_calendar_event', offered[1])
        self.assertIn('edit_calendar_event', offered[1])
        self.assertIn('delete_calendar_event', offered[1])

    def test_context_does_not_duplicate_system_rules_or_tool_schemas(self):
        context = build_ai_context('alice')
        self.assertNotIn('Grounding rules:', context)
        self.assertNotIn('manage_playlist:', context)
        self.assertIn('No full directory tree', context)


if __name__ == '__main__': unittest.main()
