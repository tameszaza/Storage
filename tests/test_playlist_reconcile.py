import json
import tempfile
import unittest
from pathlib import Path

from lib.playlist_reconcile import (
    SnapshotError,
    parse_playlist_snapshot,
    reconcile_snapshot,
    unavailable_video_id,
    write_m3u_playlist,
)


class PlaylistSnapshotTests(unittest.TestCase):
    def test_only_explicit_unavailable_errors_are_classified(self):
        self.assertEqual(
            unavailable_video_id("ERROR: [youtube] abc-123: Video unavailable. This video is not available"),
            "abc-123",
        )
        self.assertEqual(
            unavailable_video_id("ERROR: [youtube] private_id: Private video. Sign in if granted access"),
            "private_id",
        )
        self.assertEqual(
            unavailable_video_id("ERROR: [youtube] another_id: This video is private"),
            "another_id",
        )
        self.assertIsNone(unavailable_video_id("ERROR: connection timed out"))
        self.assertIsNone(unavailable_video_id("WARNING: [youtube] abc-123: Signature solving failed"))

    def test_complete_snapshot_preserves_order_and_title(self):
        payload = {
            "title": "first100",
            "playlist_count": 3,
            "entries": [{"id": "c"}, {"id": "a"}, {"id": "b"}],
        }
        identifiers, title = parse_playlist_snapshot(json.dumps(payload))
        self.assertEqual(identifiers, ["c", "a", "b"])
        self.assertEqual(title, "first100")

    def test_partial_or_invalid_snapshot_is_rejected(self):
        with self.assertRaises(SnapshotError):
            parse_playlist_snapshot("not json")
        with self.assertRaises(SnapshotError):
            parse_playlist_snapshot(json.dumps({"entries": [{"id": "one"}], "playlist_count": 2}))
        with self.assertRaises(SnapshotError):
            parse_playlist_snapshot(json.dumps({"entries": [{"id": "one"}, None]}))


class PlaylistReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.music = root / "music"
        self.managed = self.music / "Managed Playlists" / "Test"
        self.managed.mkdir(parents=True)
        self.state = root / "state.json"
        self.manifest = root / "manifest.json"
        self.archive = root / "archive.txt"
        self.playlist = self.music / "Playlists" / "Test.m3u"

    def tearDown(self):
        self.temporary.cleanup()

    def _add_file(self, identifier: str) -> Path:
        path = self.managed / f"001 - Track [{identifier}].m4a"
        path.write_text(identifier, encoding="utf-8")
        data = {"files": {}}
        if self.manifest.exists():
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["files"][identifier] = [str(path)]
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _initialize(self, identifiers):
        for identifier in identifiers:
            self._add_file(identifier)
        self.archive.write_text(
            "".join(f"youtube {identifier}\n" for identifier in identifiers), encoding="utf-8"
        )
        return reconcile_snapshot(
            identifiers, self.state, self.manifest, self.managed, self.archive, now=1000
        )

    def test_normal_removal_needs_two_spaced_complete_snapshots(self):
        self._initialize(["a", "b", "c", "d"])
        unrelated = self.managed / "keep-me.txt"
        unrelated.write_text("safe", encoding="utf-8")

        first = reconcile_snapshot(
            ["a", "b", "c"], self.state, self.manifest, self.managed, self.archive,
            now=1100, confirmation_seconds=300,
        )
        too_soon = reconcile_snapshot(
            ["a", "b", "c"], self.state, self.manifest, self.managed, self.archive,
            now=1200, confirmation_seconds=300,
        )
        confirmed = reconcile_snapshot(
            ["a", "b", "c"], self.state, self.manifest, self.managed, self.archive,
            now=1400, confirmation_seconds=300,
        )

        self.assertEqual(first["observations"], 1)
        self.assertEqual(too_soon["observations"], 1)
        self.assertEqual(first["effective_order"], ["a", "b", "c", "d"])
        self.assertEqual(confirmed["deleted_ids"], ["d"])
        self.assertFalse(any("[d]" in path.name for path in self.managed.iterdir()))
        self.assertTrue(unrelated.exists())
        self.assertNotIn("youtube d", self.archive.read_text(encoding="utf-8"))

    def test_large_drop_needs_three_spaced_snapshots(self):
        self._initialize(["a", "b", "c", "d"])
        one = reconcile_snapshot(
            ["a"], self.state, self.manifest, self.managed, self.archive,
            now=1400, confirmation_seconds=300,
        )
        two = reconcile_snapshot(
            ["a"], self.state, self.manifest, self.managed, self.archive,
            now=1700, confirmation_seconds=300,
        )
        three = reconcile_snapshot(
            ["a"], self.state, self.manifest, self.managed, self.archive,
            now=2000, confirmation_seconds=300,
        )
        self.assertEqual((one["observations"], one["required"]), (1, 3))
        self.assertEqual((two["observations"], two["required"]), (2, 3))
        self.assertEqual(three["deleted_ids"], ["b", "c", "d"])

    def test_changed_snapshot_restarts_confirmation(self):
        self._initialize(["a", "b", "c", "d"])
        reconcile_snapshot(
            ["a", "b", "c"], self.state, self.manifest, self.managed, self.archive,
            now=1400, confirmation_seconds=300,
        )
        changed = reconcile_snapshot(
            ["a", "b", "d"], self.state, self.manifest, self.managed, self.archive,
            now=1700, confirmation_seconds=300,
        )
        self.assertEqual(changed["observations"], 1)
        self.assertEqual(changed["deleted_ids"], [])

    def test_m3u_is_ordered_relative_and_not_replaced_when_audio_missing(self):
        a = self._add_file("a")
        b = self._add_file("b")
        written, missing = write_m3u_playlist(
            ["b", "a"], self.manifest, self.managed, self.music, self.playlist
        )
        self.assertEqual((written, missing), (2, []))
        self.assertEqual(
            self.playlist.read_text(encoding="utf-8").splitlines(),
            [
                "#EXTM3U",
                "../" + str(b.relative_to(self.music)),
                "../" + str(a.relative_to(self.music)),
            ],
        )

        previous = self.playlist.read_text(encoding="utf-8")
        written, missing = write_m3u_playlist(
            ["a", "missing"], self.manifest, self.managed, self.music, self.playlist
        )
        self.assertEqual(missing, ["missing"])
        self.assertEqual(self.playlist.read_text(encoding="utf-8"), previous)


if __name__ == "__main__":
    unittest.main()
