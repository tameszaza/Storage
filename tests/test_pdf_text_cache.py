"""HTTP cache lookup/permission/range regressions; no live writes."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from lib import pdf_text_cache as m
from routes import shares


class CacheLookup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdfs = self.root / 'Goodnotes'
        self.pdfs.mkdir()
        self.source = self.pdfs / 'lecture.pdf'
        self.source.write_bytes(b'%PDF-source-only')
        self.path = 'PDF Library/lecture.pdf'
        self.cache = self.root / 'cache'
        self.directory = self.cache / hashlib.sha256(self.path.encode()).hexdigest()
        self.directory.mkdir(parents=True)
        self.derived = self.directory / 'document.pdf'
        self.derived.write_bytes(b'%PDF-searchable-text-layer')
        stat = self.source.stat()
        self.manifest = dict(path=self.path, fingerprint=f'{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}',
                             policy=m.POLICY, status='ready')
        self.write_manifest()
        self.patches = [patch.object(m, 'PDF_ROOT', self.pdfs), patch.object(m, 'CACHE_ROOT', self.cache)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def write_manifest(self):
        (self.directory / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_ready(self):
        self.assertEqual(m.searchable_pdf(self.source), str(self.derived))

    def test_missing_pending_error_stale_or_corrupt_falls_back(self):
        for change in ({'status': 'pending'}, {'status': 'error'}, {'policy': 'old'}, {'fingerprint': 'stale'}, {'path': 'wrong'}):
            with self.subTest(change=change):
                original = self.manifest.copy()
                self.manifest.update(change)
                self.write_manifest()
                self.assertEqual(m.searchable_pdf(self.source), str(self.source))
                self.manifest = original
        (self.directory / 'manifest.json').write_text('not json')
        self.assertEqual(m.searchable_pdf(self.source), str(self.source))

    def test_missing_derived_or_changed_source(self):
        self.derived.unlink()
        self.assertEqual(m.searchable_pdf(self.source), str(self.source))
        self.derived.write_bytes(b'cached')
        self.source.write_bytes(b'%PDF-new-export')
        self.assertEqual(m.searchable_pdf(self.source), str(self.source))

    def test_outside_and_symlink_never_redirect(self):
        outside = self.root / 'outside.pdf'
        outside.write_bytes(b'%PDF-external')
        self.assertEqual(m.searchable_pdf(outside), str(outside))
        link = self.pdfs / 'link.pdf'
        link.symlink_to(self.source)
        self.assertEqual(m.searchable_pdf(link), str(link))

    def test_existing_route_auth_range_type_filename_and_no_cache(self):
        app = Flask(__name__)
        share = {'note': 'Obsidian PDF++ Goodnotes: fixture'}
        with patch.object(shares, '_guard_share', return_value=(share, {'allowed': True})), \
             patch.object(shares, 'target_inside_share', return_value=('', str(self.source))), \
             patch.object(shares, 'log_share_event'):
            with app.test_request_context('/test', headers={'Range': 'bytes=0-4'}):
                response = shares.shared_raw('fixture')
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.mimetype, 'application/pdf')
                self.assertIn('lecture.pdf', response.headers['Content-Disposition'])
                self.assertIn('no-store', response.headers['Cache-Control'])
                self.assertEqual(b''.join(response.response), b'%PDF-')
                response.close()
        with app.test_request_context('/test'), \
             patch.object(shares, '_guard_share', return_value=({}, {'allowed': False})), \
             patch.object(shares, '_render_gate', return_value='denied'), \
             patch.object(shares, 'searchable_pdf', side_effect=AssertionError('Cache read before auth')):
            self.assertEqual(shares.shared_raw('fixture'), 'denied')


if __name__ == '__main__':
    unittest.main()
