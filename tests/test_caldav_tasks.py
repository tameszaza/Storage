"""Isolated task deadline reconciliation; no production credentials or storage."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

from flask import Flask
from lib import caldav_sync as sync
from lib import planner


class CalendarBackend:
    def __init__(self):
        self.resources = {}
        self.writes = []
        self.fail = False
        self.revision = 0

    def request(self, method, url, username, password, *, body=None, headers=None):
        if self.fail:
            raise sync.CalendarSyncError("Fixture calendar offline")
        headers = headers or {}
        path = sync.urllib.parse.urlparse(url).path
        if method == "MKCOL":
            return 405, {}, b""
        if method == "REPORT":
            root = ET.Element(f"{{{sync.DAV}}}multistatus")
            for resource, (content, etag) in self.resources.items():
                response = ET.SubElement(root, f"{{{sync.DAV}}}response")
                ET.SubElement(response, f"{{{sync.DAV}}}href").text = resource
                props = ET.SubElement(ET.SubElement(response, f"{{{sync.DAV}}}propstat"), f"{{{sync.DAV}}}prop")
                ET.SubElement(props, f"{{{sync.DAV}}}getetag").text = etag
                ET.SubElement(props, f"{{{sync.CALDAV}}}calendar-data").text = content.decode()
            return 207, {}, ET.tostring(root)
        previous = self.resources.get(path)
        if headers.get("If-None-Match") == "*" and previous:
            return 412, {}, b""
        if headers.get("If-Match") and (not previous or previous[1] != headers["If-Match"]):
            return 412, {}, b""
        self.writes.append((method, path))
        if method == "PUT":
            self.revision += 1
            etag = f'"fixture-{self.revision}"'
            self.resources[path] = (body, etag)
            return 201, {"ETag": etag}, b""
        if method == "DELETE":
            self.resources.pop(path, None)
            return 204, {}, b""
        raise AssertionError(method)


class TaskCalendarTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        app = Flask(__name__)
        app.config.update(PLANNER_DATA_FILE=str(Path(self.directory.name) / "planner.json"),
                          CALDAV_INTERNAL_URL="http://fixture.invalid/calendar/", CALDAV_USERNAME="fixture",
                          CALDAV_PASSWORD="fixture", APP_TIMEZONE="Asia/Singapore")
        self.context = app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.backend = CalendarBackend()
        self.mock = patch.object(sync, "_request", side_effect=self.backend.request)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def task(self, **values):
        return planner.create_todo("Admin", {"title": "Fixture deadline", "due_date": "2026-10-23", **values})

    def content(self, task):
        return self.backend.resources[f"/calendar/{sync._task_uid(task['id'])}.ics"][0].decode()

    def test_date_only_and_timed_deadlines(self):
        day = self.task()
        timed = self.task(due_time="00:30")
        sync.sync_user_events()
        self.assertIn("DTSTART;VALUE=DATE:20261023", self.content(day))
        self.assertIn("DTEND;VALUE=DATE:20261024", self.content(day))
        self.assertIn("DTSTART:20261022T163000Z", self.content(timed))
        self.assertNotIn("DTEND", self.content(timed))
        self.assertIn("TRANSP:TRANSPARENT", self.content(timed))

    def test_only_active_dated_tasks_and_no_duplicate_events(self):
        dated = self.task()
        self.task(due_date="")
        done = self.task()
        planner.toggle_todo("Admin", done["id"])
        canceled = self.task()
        planner.toggle_todo_canceled("Admin", canceled["id"])
        sync.sync_user_events()
        self.assertEqual(len(self.backend.resources), 1)
        self.assertIn("Task: Fixture deadline", self.content(dated))
        self.backend.writes.clear()
        sync.sync_user_events()
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(planner.list_items("Admin")["events"], [])
        self.assertEqual(len(planner.list_items("Admin")["todos"]), 4)

    def test_each_removal_and_restore(self):
        for action in ("complete", "cancel", "delete", "clear_due"):
            with self.subTest(action=action):
                task = self.task()
                sync.sync_user_events()
                if action == "complete":
                    planner.toggle_todo("Admin", task["id"])
                elif action == "cancel":
                    planner.toggle_todo_canceled("Admin", task["id"])
                elif action == "delete":
                    planner.delete_todo("Admin", task["id"])
                else:
                    planner.update_todo("Admin", task["id"], {"due_date": "", "due_time": ""})
                sync.sync_user_events()
                self.assertFalse(self.backend.resources)
                if action == "complete":
                    planner.toggle_todo("Admin", task["id"])
                    sync.sync_user_events()
                    self.assertIn(sync._task_uid(task["id"]), self.content(task))
                    planner.delete_todo("Admin", task["id"])
                    sync.sync_user_events()

    def test_title_date_time_priority_update_and_long_unicode_title(self):
        task = self.task()
        sync.sync_user_events()
        planner.update_todo("Admin", task["id"], {"title": "งานทดสอบ " * 24, "due_date": "2026-10-24", "due_time": "18:15", "priority": "high"})
        result = sync.sync_user_events()
        self.assertEqual(result["tasks_pushed"], 1)
        self.assertEqual(len(self.backend.resources), 1)
        self.assertIn("DTSTART:20261024T101500Z", self.content(task))
        self.assertIn("PRIORITY:1", self.content(task))
        self.backend.writes.clear()
        sync.sync_user_events()
        self.assertEqual(self.backend.writes, [])

    def test_remote_edit_or_delete_is_reconciled_from_web_task(self):
        task = self.task()
        sync.sync_user_events()
        path = next(iter(self.backend.resources))
        body, etag = self.backend.resources[path]
        self.backend.resources[path] = (body.replace(b"Fixture deadline", b"Changed remotely"), etag)
        sync.sync_user_events()
        self.assertIn("Fixture deadline", self.content(task))
        self.backend.resources.clear()
        sync.sync_user_events()
        self.assertEqual(len(self.backend.resources), 1)

    def test_remote_event_sync_remains_two_way(self):
        task = self.task()
        event = planner.create_event("Admin", {"title": "Ordinary fixture event", "date": "2026-10-24", "all_day": True})
        sync.sync_user_events()
        event = planner.get_event("Admin", event["id"])
        path = "/calendar/" + event["caldav_uid"] + ".ics"
        body, etag = self.backend.resources[path]
        self.backend.resources[path] = (body.replace(b"Ordinary fixture event", b"Remote event edit"), etag)
        sync.sync_user_events()
        self.assertEqual(planner.list_items("Admin")["events"][0]["title"], "Remote event edit")
        self.assertEqual(len(planner.list_items("Admin")["events"]), 1)
        planner.delete_todo("Admin", task["id"])
        sync.sync_user_events()
        self.assertEqual(list(self.backend.resources), [path])

    def test_offline_delete_retries_without_losing_task_or_event(self):
        task = self.task()
        sync.sync_user_events()
        planner.toggle_todo("Admin", task["id"])
        original = self.backend.request
        def failing(method, *args, **kwargs):
            if method == "DELETE":
                raise sync.CalendarSyncError("Fixture offline")
            return original(method, *args, **kwargs)
        with patch.object(sync, "_request", side_effect=failing):
            with self.assertRaises(sync.CalendarSyncError):
                sync.sync_user_events()
        self.assertTrue(planner.get_todo("Admin", task["id"])["completed"])
        self.assertEqual(len(self.backend.resources), 1)
        sync.sync_user_events()
        self.assertFalse(self.backend.resources)

    def test_uid_collision_does_not_overwrite_unmanaged_event(self):
        task = self.task()
        content = sync._event_ics({"title": "Independent fixture", "date": "2026-10-23", "all_day": True}, sync._task_uid(task["id"]), ZoneInfo("Asia/Singapore"))
        self.backend.resources[f"/calendar/{sync._task_uid(task['id'])}.ics"] = (content, '"independent"')
        with self.assertRaises(sync.CalendarSyncError):
            sync.sync_user_events()
        self.assertEqual(self.backend.writes, [])

    def test_unrelated_task_marker_is_not_owned(self):
        content = sync._task_ics({"id": "orphan", "title": "Fixture", "due_date": "2026-10-23"}, ZoneInfo("Asia/Singapore"))
        content = content.replace(b"UID:tames-task-orphan", b"UID:independent")
        self.backend.resources["/calendar/independent.ics"] = (content, '"independent"')
        sync.sync_user_events()
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(len(planner.list_items("Admin")["events"]), 1)

    def test_renamed_resource_is_updated_in_place(self):
        task = self.task()
        sync.sync_user_events()
        content = self.backend.resources.pop(next(iter(self.backend.resources)))
        self.backend.resources["/calendar/client-renamed.ics"] = content
        planner.update_todo("Admin", task["id"], {"due_time": "09:00"})
        sync.sync_user_events()
        self.assertEqual(list(self.backend.resources), ["/calendar/client-renamed.ics"])

    def test_etag_conflict_preserves_remote_for_retry(self):
        task = self.task()
        sync.sync_user_events()
        planner.toggle_todo("Admin", task["id"])
        original = self.backend.request
        def conflicting(method, *args, **kwargs):
            return (412, {}, b"") if method == "DELETE" else original(method, *args, **kwargs)
        with patch.object(sync, "_request", side_effect=conflicting):
            with self.assertRaises(sync.CalendarSyncError):
                sync.sync_user_events()
        self.assertEqual(len(self.backend.resources), 1)

    def test_foreign_href_and_non_admin_are_protected(self):
        with self.assertRaises(sync.CalendarSyncError):
            sync._task_resource("http://fixture.invalid/calendar/", "x", {"caldav_href": "https://elsewhere.invalid/event.ics"})
        sync.sync_user_events("Other user")
        self.assertEqual(self.backend.writes, [])

    def legacy_task(self, task, *, linked=True, uid=None):
        uid = uid or "tames-task-legacy-fixture"
        if linked:
            planner.update_todo_metadata("Admin", task["id"], {"caldav_uid": uid})
        content = sync._event_ics({"id": "cached-fixture", "title": "Legacy fixture", "date": "2026-10-23", "all_day": True}, uid, ZoneInfo("Asia/Singapore"))
        content = content.replace(b"END:VEVENT", f"X-TAMESTORAGE-TODO-ID:{task['id']}\r\nEND:VEVENT".encode())
        path = f"/calendar/{uid}.ics"
        self.backend.resources[path] = (content, '"legacy"')
        planner.replace_events("Admin", [{"id": "cached-fixture", **sync._remote_event(content.decode(), path, '"legacy"', ZoneInfo("Asia/Singapore"))}])
        return path

    def test_legacy_task_migrates_without_duplicate_event_or_task_edits(self):
        task = self.task()
        legacy = self.legacy_task(task)
        before = planner.get_todo("Admin", task["id"])
        result = sync.sync_user_events()
        self.assertNotIn(legacy, self.backend.resources)
        self.assertEqual(len(self.backend.resources), 1)
        self.assertEqual(planner.list_items("Admin")["events"], [])
        self.assertEqual(planner.get_todo("Admin", task["id"]), before)
        self.assertEqual(result["tasks_removed"], 1)
        self.assertIn(sync._task_uid(task["id"]), self.content(task))

    def test_completed_legacy_task_is_removed_without_republishing(self):
        task = self.task()
        self.legacy_task(task)
        planner.toggle_todo("Admin", task["id"])
        sync.sync_user_events()
        self.assertFalse(self.backend.resources)
        self.assertEqual(planner.list_items("Admin")["events"], [])
        self.assertTrue(planner.get_todo("Admin", task["id"])["completed"])

    def test_legacy_marker_at_deterministic_uid_is_upgraded_in_place(self):
        task = self.task()
        path = self.legacy_task(task, uid=sync._task_uid(task["id"]))
        sync.sync_user_events()
        self.assertEqual(list(self.backend.resources), [path])
        self.assertIn("X-TAMESTORAGE-TASK-ID", self.content(task))
        self.assertNotIn("X-TAMESTORAGE-TODO-ID", self.content(task))
        self.assertEqual(planner.list_items("Admin")["events"], [])

    def test_unverified_legacy_marker_is_preserved(self):
        task = self.task(due_date="")
        path = self.legacy_task(task, linked=False)
        sync.sync_user_events()
        self.assertIn(path, self.backend.resources)
        self.assertEqual(self.backend.writes, [])


if __name__ == "__main__":
    unittest.main()
