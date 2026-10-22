import json
import re
import secrets
from functools import lru_cache
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, url_for, jsonify, send_file, session

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
from routes.transcription import admin as review_admin, store as review_store
from lib import lyric_review
from lib.transcription_store import QueueError
from lib.navidrome_stats import playlist_playback_stats


def _file_stamp(path: Path) -> tuple[int, int]:
    try:
        stat_result = path.stat()
        return stat_result.st_mtime_ns, stat_result.st_size
    except OSError:
        return 0, 0


@lru_cache(maxsize=64)
def _cached_lyric_summary(
    ordered_ids: tuple[str, ...], manifest: str, managed: str, state: str,
    manifest_stamp: tuple[int, int], managed_stamp: tuple[int, int],
    state_stamp: tuple[int, int],
) -> dict[str, int]:
    # A changed manifest, lyric state, or managed directory invalidates the
    # count. Editing an existing sidecar in place cannot change its presence.
    return summarize_playlist_lyrics(
        list(ordered_ids), Path(manifest), Path(managed), Path(state),
    )


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
    state_path = runtime_dir / "lyrics-state.json"
    return _cached_lyric_summary(
        tuple(order), str(manifest_path), str(managed_dir), str(state_path),
        _file_stamp(manifest_path), _file_stamp(managed_dir), _file_stamp(state_path),
    ).copy()


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
        lyrics_csrf=session.setdefault("transcription_csrf",secrets.token_urlsafe(32)),
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


@review_admin
def lyrics_list(playlist_id):
    item=next((item for item in list_playlists() if item.get('id')==playlist_id),None)
    if not item: abort(404)
    return jsonify(songs=lyric_review.listing(review_store(),str(item.get('library_dir_name') or '')))


@review_admin
def lyrics_edit(job_id):
    queue=review_store();root=current_app.config['MUSIC_FOLDER']
    if request.method in {'GET','HEAD'}: return jsonify(lyric_review.read(queue,job_id,root))
    if request.content_length is None or request.content_length>300000: raise QueueError('Edits are too large.',413)
    data=request.get_json(silent=True)
    if not isinstance(data,dict): raise QueueError('Invalid lyric edits.',400)
    return jsonify(token=lyric_review.submit(queue,job_id,data,root)),202


@review_admin
def lyrics_status(job_id):
    return jsonify(lyric_review.status(review_store(),job_id,request.args.get('token','')))


@review_admin
def lyrics_audio(job_id):
    row,opt=lyric_review._row(review_store(),job_id)
    source=lyric_review.source_path(opt,current_app.config['MUSIC_FOLDER'])
    if lyric_review.signature(source)!=opt['lyrics_signature']: raise QueueError('The song changed.')
    # .opus here is an Ogg file, not the audio/opus RTP payload format.
    mime='audio/ogg' if source.suffix.lower() in {'.opus','.ogg','.oga'} else None
    return send_file(source,mimetype=mime,conditional=True,max_age=0)


def register_routes(app):
    @app.after_request
    def private_lyric_review(response):
        if (request.endpoint or '').startswith('music_sync_lyrics_'):
            response.headers['Cache-Control']='private, no-store, max-age=0'
            response.headers['X-Content-Type-Options']='nosniff'
        return response

    app.add_url_rule('/music-sync/<playlist_id>/lyrics','music_sync_lyrics_list',lyrics_list)
    app.add_url_rule('/music-sync/lyrics/<job_id>','music_sync_lyrics_edit',lyrics_edit,methods=['GET','POST'])
    app.add_url_rule('/music-sync/lyrics/<job_id>/status','music_sync_lyrics_status',lyrics_status)
    app.add_url_rule('/music-sync/lyrics/<job_id>/audio','music_sync_lyrics_audio',lyrics_audio)
    app.add_url_rule("/music-sync", "music_sync_page", music_sync_page)
    app.add_url_rule("/music-sync/add", "music_sync_add", music_sync_add, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/sync", "music_sync_now", music_sync_now, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/toggle", "music_sync_toggle", music_sync_toggle, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/schedule", "music_sync_schedule", music_sync_schedule, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/delete", "music_sync_delete", music_sync_delete, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/delete-files", "music_sync_delete_files", music_sync_delete_files, methods=["POST"])
    app.add_url_rule("/music-sync/<playlist_id>/log", "music_sync_log", music_sync_log)
