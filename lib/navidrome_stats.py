"""Read-only playback statistics for Navidrome playlists."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import current_app


def _empty(reason: str | None = None) -> dict[str, object]:
    return {
        "available": False if reason else True,
        "total_tracks": 0,
        "played_tracks": 0,
        "never_played": 0,
        "plays": 0,
        "last_played_at": None,
        "reason": reason,
    }


def _format_last_played(value: object) -> str | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            timezone_name = str(current_app.config.get("APP_TIMEZONE") or "UTC")
            try:
                parsed = parsed.astimezone(ZoneInfo(timezone_name))
            except (KeyError, ValueError):
                pass
        return parsed.strftime("%b %-d, %Y, %-I:%M %p")
    except (TypeError, ValueError, OverflowError):
        return str(value)


def playlist_playback_stats(item: dict) -> dict[str, object]:
    """Return aggregate playback information for one managed playlist.

    Navidrome is the source of truth. The database is opened in SQLite
    read-only mode so a dashboard request cannot mutate playback history.
    Missing/stale databases are reported as unavailable rather than raising
    an error on the music-sync page.
    """
    database = str(current_app.config.get("NAVIDROME_DB_PATH") or "").strip()
    playlist_file = str(item.get("playlist_file_name") or "").strip()
    if not database or not playlist_file:
        return _empty("Navidrome playback data is not configured yet.")

    path = Path(database)
    if not path.is_file():
        return _empty("Navidrome playback database is unavailable.")

    expected_path = f"/music/Playlists/{playlist_file}"
    uri = f"file:{path}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=0.5) as connection:
            playlist = connection.execute(
                "SELECT id FROM playlist WHERE path = ? ORDER BY updated_at DESC LIMIT 1",
                (expected_path,),
            ).fetchone()
            if not playlist:
                return _empty("Navidrome playlist has not been indexed yet.")

            playlist_id = str(playlist[0])
            result = connection.execute(
                """
                SELECT
                    COUNT(DISTINCT pt.media_file_id),
                    COUNT(DISTINCT CASE WHEN COALESCE(a.play_count, 0) > 0 THEN pt.media_file_id END),
                    COALESCE(SUM(COALESCE(a.play_count, 0)), 0),
                    MAX(a.play_date)
                FROM playlist_tracks AS pt
                LEFT JOIN annotation AS a
                  ON a.item_id = pt.media_file_id
                 AND a.item_type = 'media_file'
                WHERE pt.playlist_id = ?
                """,
                (playlist_id,),
            ).fetchone()
    except (OSError, sqlite3.Error) as error:
        return _empty(f"Playback data could not be read: {error}")

    total = int(result[0] or 0)
    played = int(result[1] or 0)
    return {
        "available": True,
        "total_tracks": total,
        "played_tracks": played,
        "never_played": max(total - played, 0),
        "plays": int(result[2] or 0),
        "last_played_at": _format_last_played(result[3]),
        "reason": None,
    }
