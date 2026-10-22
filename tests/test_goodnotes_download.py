"""Missing-only ZIP contract, access control, filesystem safety and cancellation."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from flask import Flask, render_template
from lib.goodnotes_status import import_warning, ImportStatusUnavailable, open_pdf
from lib.goodnotes_download import CHUNK_SIZE, stream_import_zip

spec = importlib.util.spec_from_file_location('goodnotes_route', str(Path(__file__).with_name('goodnotes.py') if Path(__file__).with_name('goodnotes.py').exists() else Path('/app/routes/goodnotes.py')))
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)


class GoodnotesDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.uploads = self.root/'uploads'
        self.library = self.uploads/'Goodnotes'
        self.library.mkdir(parents=True)
        self.report = self.root/'.tamestorage_state/goodnotes-obsidian-links/ipad-import-status.json'
        self.report.parent.mkdir(parents=True)
        self.state = self.report.parent/'cleanup-state.json'
        self.state.write_text('private-provenance-must-not-change')
        self.app = Flask(__name__, template_folder='/app/templates')
        self.app.config.update(SECRET_KEY='fixture-only', TESTING=True, UPLOAD_FOLDER=str(self.uploads))
        self.app.add_url_rule('/login', 'login', lambda: 'Login')
        self.app.add_url_rule('/index/<path:path>', 'index', lambda path: 'Files')
        route.register_routes(self.app)
        self.client = self.app.test_client()
        self.login('Admin')

    def tearDown(self):
        self.temporary.cleanup()

    def login(self, username):
        with self.client.session_transaction() as session:
            session.clear()
            if username:
                session.update(logged_in=True, username=username)

    def pdf(self, name, payload=b'%PDF-fixture'):
        path = self.library/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def summary(self, files):
        self.report.write_text(json.dumps({'version': 2, 'files': [dict(item,confidence=item.get('confidence','missing')) for item in files]}))

    def test_missing_only_preserves_paths_unicode_content_and_state(self):
        first = 'NTU/Y1SEM1/AB1201/Lecture 10.pdf'
        second = 'Books/ภาษาไทย [notes].pdf'
        self.pdf(first, b'%PDF-one')
        self.pdf(second, b'%PDF-two')
        self.pdf('NTU/Y1SEM1/AB1201/Already on iPad.pdf')
        self.pdf('Manual but previously confirmed.pdf')
        self.summary([{'path': first, 'reason': 'manual'}, {'path': second, 'reason': 'unconfirmed'}])
        before = self.report.read_bytes(), self.state.read_bytes()
        response = self.client.get('/goodnotes/download-for-ipad?existing_folders=yes')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/zip')
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertIn('no-store', response.headers['Cache-Control'])
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(archive.namelist(), [first, second])
            self.assertEqual(archive.read(first), b'%PDF-one')
            self.assertEqual(archive.read(second), b'%PDF-two')
            self.assertTrue(all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist()))
            self.assertIsNone(archive.testzip())
        response.close()
        self.assertEqual((self.report.read_bytes(), self.state.read_bytes()), before)
        self.assertEqual(len(list(self.root.rglob('*.zip'))), 0)
        self.assertEqual(import_warning(self.uploads)['count'], 2)

    def test_unknown_files_excluded_and_direct_zip_import_discouraged(self):
        self.pdf('missing.pdf');self.pdf('uncertain.pdf')
        self.summary([{'path':'missing.pdf'},{'path':'uncertain.pdf','confidence':'unconfirmed'}])
        self.assertEqual(self.client.get('/goodnotes/download-for-ipad').status_code,302)
        response=self.client.get('/goodnotes/download-for-ipad?existing_folders=yes')
        with zipfile.ZipFile(io.BytesIO(response.data)) as z:self.assertEqual(z.namelist(),['missing.pdf'])
        response.close()

    def test_authentication_and_owner_only(self):
        self.pdf('missing.pdf'); self.summary([{'path': 'missing.pdf'}])
        for username, expected in [(None, 302), ('Guest', 403), ('admin', 403)]:
            self.login(username)
            with patch.object(route, 'import_warning', side_effect=AssertionError('must not inspect library')):
                self.assertEqual(self.client.get('/goodnotes/download-for-ipad?existing_folders=yes').status_code, expected)

    def test_unavailable_or_invalid_summary_never_exports_entire_library(self):
        self.pdf('private.pdf')
        for data in [None, 'not json', '[]', '{"version": 3, "files": []}', '{"version": 1, "files": {}}', 'x' * (1024*1024+1)]:
            if data is None:
                if self.report.exists(): self.report.unlink()
            else:
                self.report.write_text(data)
            self.assertIsNone(import_warning(self.uploads))
            self.assertEqual(self.client.get('/goodnotes/download-for-ipad?existing_folders=yes').status_code, 503)

    def test_empty_or_deleted_candidates_do_not_create_empty_archive(self):
        for files in [[], [{'path': 'deleted.pdf'}]]:
            self.summary(files)
            response = self.client.get('/goodnotes/download-for-ipad?existing_folders=yes')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers['Location'].endswith('/goodnotes/import'))

    def test_unsafe_paths_duplicates_and_symlink_parents_excluded(self):
        self.pdf('safe.pdf')
        outside = self.root/'outside'; outside.mkdir(); (outside/'secret.pdf').write_bytes(b'secret')
        (self.library/'escape').symlink_to(outside, target_is_directory=True)
        (self.library/'linked.pdf').symlink_to(outside/'secret.pdf')
        (self.library/'pipe.pdf').touch(); (self.library/'pipe.pdf').unlink(); os.mkfifo(self.library/'pipe.pdf')
        names = ['safe.pdf', 'safe.pdf', '../outside/secret.pdf', '/outside/secret.pdf',
                 'folder/../safe.pdf', './safe.pdf', 'escape/secret.pdf', 'linked.pdf',
                 'bad\\name.pdf', 'bad\x00.pdf', 'pipe.pdf', 'folder//safe.pdf', 'not.pdf.txt']
        self.summary([{'path': name} for name in names])
        warning = import_warning(self.uploads)
        self.assertEqual([item['path'] for item in warning['files']], ['safe.pdf'])
        self.assertEqual(warning['total_bytes'], len(b'%PDF-fixture'))

    def test_stream_is_bounded_and_closes_source_on_disconnect(self):
        self.pdf('large.pdf', b'x' * (3*CHUNK_SIZE+17))
        self.summary([{'path': 'large.pdf'}])
        descriptors = []
        def track(root_fd, relative):
            fd = open_pdf(root_fd, relative); descriptors.append(fd); return fd
        plan = import_warning(self.uploads)['files']
        with patch('lib.goodnotes_download.open_pdf', side_effect=track):
            stream = stream_import_zip(self.uploads, plan)
            next(stream)
            self.assertTrue(descriptors)
            stream.close()
        with self.assertRaises(OSError): os.fstat(descriptors[0])
        pieces = list(stream_import_zip(self.uploads, plan))
        self.assertLessEqual(max(map(len, pieces)), CHUNK_SIZE)
        with zipfile.ZipFile(io.BytesIO(b''.join(pieces))) as archive:
            self.assertEqual(len(archive.read('large.pdf')), 3*CHUNK_SIZE+17)

    def test_source_changes_or_symlink_replacement_abort_instead_of_silent_export(self):
        path = self.pdf('change.pdf')
        self.summary([{'path': 'change.pdf'}])
        plan = import_warning(self.uploads)['files']
        path.write_bytes(b'%PDF-changed')
        with self.assertRaises(OSError): b''.join(stream_import_zip(self.uploads, plan))
        path.unlink(); path.symlink_to(self.state)
        with self.assertRaises(OSError): b''.join(stream_import_zip(self.uploads, plan))

    def test_change_mid_stream_aborts_archive(self):
        path = self.pdf('change.pdf', b'x' * (2*CHUNK_SIZE))
        self.summary([{'path': 'change.pdf'}])
        stream = stream_import_zip(self.uploads, import_warning(self.uploads)['files'])
        next(stream); next(stream)
        with path.open('ab') as handle: handle.write(b'changed')
        with self.assertRaises(OSError): b''.join(stream)

    def test_shared_request_logger_does_not_buffer_archive(self):
        from lib.request_logging import register_request_logging
        self.app.config['DATA_TRANSFER_LOG'] = str(self.root/'transfer.log')
        register_request_logging(self.app)
        self.pdf('missing.pdf'); self.summary([{'path': 'missing.pdf'}])
        consumed = []
        closed = []
        def archive(*args):
            try:
                for i in range(10):
                    consumed.append(i)
                    yield b'chunk'
            finally:
                closed.append(True)
        with patch.object(route, 'stream_import_zip', side_effect=archive):
            response = self.client.get('/goodnotes/download-for-ipad?existing_folders=yes', buffered=False)
            self.assertTrue(response.is_streamed)
            self.assertEqual(consumed, [0])
            response.close()
        self.assertEqual(closed, [True])

    def test_warning_has_one_global_download_and_safe_paths(self):
        self.pdf('NTU/A & B.pdf')
        self.summary([{'path': 'NTU/A & B.pdf'}])
        with self.app.test_request_context('/'):
            html = render_template('_goodnotes_warning.html', goodnotes_import_warning=import_warning(self.uploads))
        self.assertEqual(html.count('href="/goodnotes/import"'), 1)
        self.assertIn('Review iPad imports', html)
        self.assertIn('last iPad inventory', html)
        self.assertIn('NTU/A &amp; B.pdf', html)


if __name__ == '__main__': unittest.main()
