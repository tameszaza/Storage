import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import video_downloads as videos


class VideoStorageTests(unittest.TestCase):
    def test_hdwatch_items_share_readable_series_folder(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(videos, 'DB', Path(root) / 'videos.sqlite'), patch.object(videos, 'ROOT', Path(root) / 'Movies'):
                first = videos.enqueue('1-16', 'https://hdwatch.pro/series/the-middle-86450/1-16/')
                second = videos.enqueue('1-17', 'https://hdwatch.pro/series/the-middle-86450/1-17/')
                rows = {row['id']: row for row in videos.items()}
                self.assertEqual(rows[first]['folder_name'], 'The Middle')
                self.assertEqual(rows[second]['folder_name'], 'The Middle')
                self.assertEqual(videos.media_labels(rows[first])[1], 'S01E16 - 1-16')

    def test_migration_merges_uuid_folders_and_keeps_episode_files(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(videos, 'DB', Path(root) / 'videos.sqlite'), patch.object(videos, 'ROOT', Path(root) / 'Movies'):
                first = videos.enqueue('1-16', 'https://hdwatch.pro/series/the-middle-86450/1-16/')
                second = videos.enqueue('1-17', 'https://hdwatch.pro/series/the-middle-86450/1-17/')
                for key, old_name in ((first, '1-16.mp4'), (second, '1-17.mp4')):
                    videos.update(key, status='ready', filename=old_name)
                    old = videos.ROOT / key
                    old.mkdir(parents=True)
                    (old / old_name).write_bytes(b'video')
                moved = videos.migrate_legacy_folders()
                self.assertEqual(len(moved), 2)
                target = videos.ROOT / 'The Middle'
                self.assertEqual(sorted(p.name for p in target.iterdir()), ['S01E16 - 1-16.mp4', 'S01E17 - 1-17.mp4'])
                self.assertFalse((videos.ROOT / first).exists())
                self.assertFalse((videos.ROOT / second).exists())

    def test_removal_does_not_delete_sibling_episode(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(videos, 'DB', Path(root) / 'videos.sqlite'), patch.object(videos, 'ROOT', Path(root) / 'Movies'):
                first = videos.enqueue('1-1', 'https://hdwatch.pro/series/foo-1/1-1/')
                second = videos.enqueue('1-2', 'https://hdwatch.pro/series/foo-1/1-2/')
                folder = videos.ROOT / 'Foo'
                folder.mkdir(parents=True)
                videos.update(first, status='ready', filename='S01E01 - 1-1.mp4')
                videos.update(second, status='ready', filename='S01E02 - 1-2.mp4')
                (folder / 'S01E01 - 1-1.mp4').write_bytes(b'one')
                (folder / 'S01E02 - 1-2.mp4').write_bytes(b'two')
                videos.remove_files(next(row for row in videos.items() if row['id'] == first))
                self.assertFalse((folder / 'S01E01 - 1-1.mp4').exists())
                self.assertTrue((folder / 'S01E02 - 1-2.mp4').exists())


if __name__ == '__main__':
    unittest.main()
