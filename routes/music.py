import json
import re
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, url_for

from lib.playlists import (
    SCHEDULES,
    add_playlist,
    delete_playlist,
    list_playlists,
    read_log,
    request_delete,
    request_sync,
    set_interval,
    set_enabled,
)
from lib.security import admin_required
from lib.storage import format_bytes, get_folder_size
from lib.lyrics import summarize_playlist_lyrics
from lib.navidrome_stats import playlist_playback_stats


def _playlist_lyric_summary(item: dict) -> dict[str, int]:
    runtime_dir = Path(current_app.config["PLAYLIST_RUNTIME_DIR"]) / str(item.get("id") or "")
    manifest_path = runtime_dir / "manifest.json"
    order: list[str] = []
    try:
        snapshot = json.loads((runtime_dir / "snapshot-state.json").read_text(encoding="utf-8"))
        if isinstance(snapshot, dict) and isinstance(snapshot.get("confirmed_order"), list):
            order = [str(value) for value in snapshot["confirmed_order"] if value]
    except (OSError, json.JSONDecodeError):
        pass
    if not order:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
            if isinstance(files, dict):
                order = [str(value) for value in files]
        except (OSError, json.JSONDecodeError):
            pass
    managed_dir = (
        Path(current_app.config["MUSIC_FOLDER"])
        / "Managed Playlists"
        / str(item.get("library_dir_name") or "")
    )
    return summarize_playlist_lyrics(
        order,
        manifest_path,
        managed_dir,
        runtime_dir / "lyrics-state.json",
    )


def _playlist_sync_summary(item: dict, health: dict[str, int]) -> dict[str, int]:
    """Build the latest song-sync counters without running a sync."""
    success = int(item.get("sync_success_count") or 0)
    unavailable = int(item.get("sync_unavailable_count") or 0)
    removed = int(item.get("sync_removed_count") or 0)
    if not success and item.get("status") == "success":
        # Older records did not persist this counter; the current local M3U
        # count is the best exact fallback for those records.
        success = int(health.get("total") or 0)
    if "sync_unavailable_count" not in item:
        log_text = read_log(str(item.get("id") or ""))
        latest_start = log_text.rfind("] Checking complete remote snapshot for ")
        latest_log = log_text[latest_start:] if latest_start >= 0 else log_text
        matches = re.findall(r"YouTube reports (\d+) private/unavailable", latest_log)
        unavailable = int(matches[-1]) if matches else 0
    if not removed:
        match = re.search(r"Removed (\d+) managed (?:audio/lyrics )?file", str(item.get("last_result") or ""))
        if match:
            removed = int(match.group(1))
    return {
        "success": success,
        "unavailable": unavailable,
        "waiting_removal": int(item.get("pending_removals") or 0),
        "removed": removed,
    }


@admin_required
def music_sync_page():
    music_folder = current_app.config["MUSIC_FOLDER"]
    playlists = list_playlists()
    for item in playlists:
        item["lyrics_summary"] = _playlist_lyric_summary(item)
        item["sync_summary"] = _playlist_sync_summary(item, item["lyrics_summary"])
        item["playback_stats"] = playlist_playback_stats(item)
    return render_template(
        "music_sync.html",
        playlists=playlists,
        schedules=SCHEDULES,
        music_folder=music_folder,
        music_size=format_bytes(get_folder_size(music_folder)),
    )


@admin_required
def music_sync_add():
    try:
        interval = int(request.form.get("interval_hours", "24"))
    except ValueError:
        interval = 24
    try:
        item = add_playlist(
            request.form.get("name", ""),
            request.form.get("url", ""),
            interval,
            sync_now=request.form.get("sync_now") == "1",
        )
    except ValueError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Added {item['name']}. The sync worker will process it shortly.", "success")
    return redirect(url_for("music_sync_page"))


@admin_required
def music_sync_now(playlist_id: str):
    if not request_sync(playlist_id):
        abort(404)
    flash("Quick sync queued.", "success")
    return redirect(url_for("music_sync_page"))


@admin_required
def music_sync_toggle(playlist_id: str):
    if not set_enabled(playlist_id, request.form.get("enabled") == "1"):
        abort(404)
    flash("Playlist schedule updated.", "success")
    return redirect(url_for("music_sync_page"))


@admin_required
def music_sync_schedule(playlist_id: str):
    try:
        interval = int(request.form.get("interval_hours", "24"))
        updated = set_interval(playlist_id, interval)
    except ValueError as exc:
        flash(str(exc), "danger")
    else:
        if not updated:
            abort(404)
        flash("Playlist sync frequency updated.", "success")
    return redirect(url_for("music_sync_page"))


@admin_required
def music_sync_delete(playlist_id: str):
    if not delete_playlist(playlist_id):
        flash("That playlist was not found or its file deletion is already queued.", "danger")
    else:
        flash("Sync entry removed. Downloaded music was kept.", "success")
    return redirect(url_for("music_sync_page"))


@admin_required
def music_sync_delete_files(playlist_id: str):
    if not request_delete(playlist_id):
        flash("That playlist is currently syncing or was not found.", "danger")
    else:
        flash("Playlist deletion queued: its downloaded songs and Navidrome playlist will be removed.", "success")
    return redirect(url_for("music_sync_page"))


@admin_required
def music_sync_log(playlist_id: str):
    item = next((row for row in list_playlists() if row.get("id") == playlist_id), None)
    if not item:
        abort(404)
    return render_template("music_sync_log.html", playlist=item, log_text=read_log(playlist_id))


def register_routes(app):
    app.add_url_rule("/music-sync", "music_sync_page", music_sync_page)
    app.add_url_rule("/music-sync/add", "music_sync_add", music_sync_add, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/sync", "music_sync_now", music_sync_now, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/toggle", "music_sync_toggle", music_sync_toggle, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/schedule", "music_sync_schedule", music_sync_schedule, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/delete", "music_sync_delete", music_sync_delete, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/delete-files", "music_sync_delete_files", music_sync_delete_files, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/log", "music_sync_log", music_sync_log)
