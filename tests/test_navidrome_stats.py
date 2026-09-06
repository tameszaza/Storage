import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from lib.navidrome_stats import playlist_playback_stats


class NavidromeStatsTests(unittest.TestCase):
    def test_aggregates_playlist_annotations(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "navidrome.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE playlist (id TEXT PRIMARY KEY, path TEXT, updated_at TEXT);
                CREATE TABLE playlist_tracks (id INTEGER, playlist_id TEXT, media_file_id TEXT);
                CREATE TABLE annotation (item_id TEXT, item_type TEXT, play_count INTEGER, play_date TEXT);
                INSERT INTO playlist VALUES ('p1', '/music/Playlists/pop.m3u', '2026-09-05');
                INSERT INTO playlist_tracks VALUES (1, 'p1', 'track-1');
                INSERT INTO playlist_tracks VALUES (2, 'p1', 'track-2');
                INSERT INTO annotation VALUES ('track-1', 'media_file', 3, '2026-09-05 18:30:00+00:00');
                INSERT INTO annotation VALUES ('track-2', 'media_file', 0, NULL);
                """
            )
            connection.commit()
            connection.close()

            app = Flask(__name__)
            app.config.update(NAVIDROME_DB_PATH=str(database), APP_TIMEZONE="Asia/Singapore")
            with app.app_context():
                stats = playlist_playback_stats({"playlist_file_name": "pop.m3u"})

            self.assertEqual(stats["total_tracks"], 2)
            self.assertEqual(stats["played_tracks"], 1)
            self.assertEqual(stats["never_played"], 1)
            self.assertEqual(stats["plays"], 3)
            self.assertEqual(stats["last_played_at"], "Sep 6, 2026, 2:30 AM")


if __name__ == "__main__":
    unittest.main()
