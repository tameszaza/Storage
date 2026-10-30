"""Real PDF/OCR regression tests, private NAS fixtures only."""
import contextlib
import hashlib
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pikepdf
from PIL import Image, ImageChops, ImageDraw, ImageFont
import searchable_pdf as s
import helper_client as h


class TextLayers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-layer-regression-', dir=s.DATA)
        self.root = Path(self.temp.name)
        self.patches = [patch.object(s, 'ROOT', self.root / 'cache'),
                        patch.object(h, 'DATA', self.root),
                        patch.object(h, 'background_remote', return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def source(self, rotation=0, native=False, crop=False):
        im = Image.new('RGB', (1000, 600), 'white')
        draw = ImageDraw.Draw(im)
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 40)
        draw.text((180, 70), 'SEARCHABLE CAPACITOR', fill='black', font=font)
        if native:
            draw.text((180, 220), 'GOODNOTES HANDWRITING', fill='black', font=font)
        base = self.root / 'base.pdf'
        im.rotate(rotation, expand=True).save(base, resolution=100)
        file = self.root / 'source.pdf'
        with pikepdf.open(base) as pdf:
            page = pdf.pages[0]
            page.Rotate = rotation
            if crop:
                page.CropBox = pikepdf.Array([40, 30, 690, 410])
            if native:
                fonts = pikepdf.Dictionary(H1=pikepdf.Dictionary(Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
                page.Resources.Font = fonts
                page.contents_add(b'q BT /H1 28 Tf 0 Tr 125 180 Td (NATIVE WORDS) Tj ET Q\n'
                                  b'q BT /H1 28 Tf 3 Tr 129.6 247 Td (GOODNOTES HANDWRITING) Tj ET Q\n')
            page.Annots = pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link,
                Rect=pikepdf.Array([100, 100, 200, 125]),
                A=pikepdf.Dictionary(S=pikepdf.Name.URI, URI='https://example.com/lecture')))])
            with pdf.open_outline() as outline:
                outline.root.append(pikepdf.OutlineItem('Lecture bookmark', 0))
            pdf.save(file)
        return file

    def extract(self, file):
        return subprocess.check_output(['pdftotext', str(file), '-']).decode()

    def render(self, file, label):
        base = self.root / label
        s.run(['pdftoppm', '-f', '1', '-l', '1', '-singlefile', '-cropbox',
               '-scale-to', '800', '-png', str(file), str(base)], 60)
        return Image.open(base.with_suffix('.png')).convert('RGB')

    def test_rotations_appearance_geometry_native_annotations(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                file = self.source(rotation)
                original = hashlib.sha256(file.read_bytes()).digest()
                path = f'PDF Library/test/rotation{rotation}.pdf'
                fp = s.fingerprint(file)
                text = s.recognize_page(file, path, fp, 1, False)
                self.assertIn('CAPACITOR', text)
                s.assemble(file, path, fp, [1])
                result = s.folder(path) / 'document.pdf'
                self.assertIn('CAPACITOR', self.extract(result))
                tree = ET.fromstring(subprocess.check_output(['pdftotext', '-bbox', str(result), '-']))
                first = tree.find('.//{http://www.w3.org/1999/xhtml}word')
                self.assertAlmostEqual(float(first.get('xMin')), 132, delta=4)
                self.assertAlmostEqual(float(first.get('yMin')), 49, delta=4)
                self.assertIsNone(ImageChops.difference(self.render(file, 'before'), self.render(result, 'after')).getbbox())
                self.assertEqual(hashlib.sha256(file.read_bytes()).digest(), original)
                with pikepdf.open(result) as pdf:
                    self.assertEqual(str(pdf.pages[0].Annots[0].A.URI), 'https://example.com/lecture')
                    with pdf.open_outline() as outline:
                        self.assertEqual(outline.root[0].title, 'Lecture bookmark')

    def test_native_and_goodnotes_text_are_not_recognized_again(self):
        file = self.source(native=True)
        path = 'PDF Library/test/mixed.pdf'
        fp = s.fingerprint(file)
        text = s.recognize_page(file, path, fp, 1, True)
        self.assertIn('CAPACITOR', text)
        self.assertNotIn('GOODNOTES', text)
        self.assertNotIn('NATIVE', text)
        s.assemble(file, path, fp, [1])
        result = s.folder(path) / 'document.pdf'
        text = self.extract(result)
        self.assertEqual(text.count('GOODNOTES HANDWRITING'), 1)
        self.assertEqual(text.count('NATIVE WORDS'), 1)
        self.assertIn('CAPACITOR', text)
        self.assertIsNone(ImageChops.difference(self.render(file, 'before'), self.render(result, 'after')).getbbox())

    def test_crop_origin_preserved_and_text_aligned(self):
        file = self.source(crop=True)
        path = 'PDF Library/test/cropped.pdf'
        fp = s.fingerprint(file)
        self.assertIn('CAPACITOR', s.recognize_page(file, path, fp, 1, False))
        s.assemble(file, path, fp, [1])
        result = s.folder(path) / 'document.pdf'
        self.assertIn('CAPACITOR', self.extract(result))
        self.assertIsNone(ImageChops.difference(self.render(file, 'before'), self.render(result, 'after')).getbbox())
        # Poppler -bbox is in MediaBox coordinates without -cropbox. The
        # recognized word must still land on its original page coordinates.
        tree = ET.fromstring(subprocess.check_output(['pdftotext', '-bbox', str(result), '-']))
        first = tree.find('.//{http://www.w3.org/1999/xhtml}word')
        self.assertAlmostEqual(float(first.get('xMin')), 132, delta=4)
        self.assertAlmostEqual(float(first.get('yMin')), 49, delta=4)

    def test_reuse_and_source_change(self):
        file = self.source()
        path = 'PDF Library/test/cache.pdf'
        fp = s.fingerprint(file)
        s.recognize_page(file, path, fp, 1, False)
        with patch.object(s, 'run', side_effect=AssertionError('Repeated OCR')):
            self.assertIn('CAPACITOR', s.recognize_page(file, path, fp, 1, False))
        with self.assertRaises(RuntimeError):
            s.recognize_page(file, path, 'wrong fingerprint', 1, False)
        marker = s.folder(path) / 'old-only.pdf'
        marker.touch()
        s.prepare(path, fp + '-new')
        self.assertFalse(marker.exists())

    def test_build_resume_queue_and_prune(self):
        file = self.source()
        path = 'PDF Library/test/build.pdf'
        fp = s.fingerprint(file)
        db = sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE pages(path,page,text,method);CREATE TABLE ocr_jobs(path PRIMARY KEY,fingerprint);')
        db.execute('INSERT INTO pages VALUES(?,1,?,?)', (path, 'cached old OCR', 'ocr'))
        self.assertTrue(s.queue(db, path, file, fp))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(s.build(db, path, file, fp))
        self.assertFalse(s.queue(db, path, file, fp))
        self.assertFalse(list(s.folder(path).glob('1.*')))
        with patch.object(s, 'run', side_effect=AssertionError('Repeated OCR')):
            self.assertTrue(s.build(db, path, file, fp))
        self.assertEqual(s.read_json(s.folder(path) / 'manifest.json')['status'], 'ready')
        s.prune({})
        self.assertFalse(s.folder(path).exists())

    def test_mixed_cropped_page_native_preservation(self):
        file = self.source(native=True, crop=True)
        path = 'PDF Library/test/mixed-cropped.pdf'
        fp = s.fingerprint(file)
        text = s.recognize_page(file, path, fp, 1, True)
        self.assertIn('CAPACITOR', text)
        self.assertNotIn('GOODNOTES', text)
        self.assertNotIn('NATIVE', text)
        s.assemble(file, path, fp, [1])
        text = self.extract(s.folder(path) / 'document.pdf')
        self.assertEqual(text.count('GOODNOTES HANDWRITING'), 1)
        self.assertEqual(text.count('NATIVE WORDS'), 1)

    def test_invalid_cache_path(self):
        for path in ('outside.pdf', 'PDF Library/../bad.pdf', 'PDF Library/.hidden/a.pdf'):
            with self.assertRaises(ValueError):
                s.folder(path)


if __name__ == '__main__':
    unittest.main()
