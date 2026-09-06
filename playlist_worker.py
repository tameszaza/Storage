import os
import shutil
import subprocess
import time
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app import app
from lib.playlist_reconcile import (
    SnapshotError,
    parse_playlist_snapshot,
    reconcile_snapshot,
    record_download_report,
    remove_archive_ids,
    unavailable_video_id,
    write_m3u_playlist,
)
from lib.playlists import (
    append_log,
    begin_sync_log,
    claim_delete,
    claim_next,
    complete_delete_failure,
    complete_sync,
    recover_interrupted,
    remove_record,
)
from lib.lyrics import sync_playlist_lyrics


def _yt_dlp_base() -> list[str]:
    return [
        "yt-dlp",
        "--force-ipv4",
        "--js-runtimes", "deno:/usr/local/bin/deno",
        "--extractor-args", "youtube:player_client=web_embedded",
        "--yes-playlist",
    ]


def _snapshot(item: dict) -> tuple[list[str], str, dict[str, str]]:
    command = [
        *_yt_dlp_base(),
        "--flat-playlist",
        "--dump-single-json",
        "--no-warnings",
        "--",
        item["url"],
    ]
    timeout = max(60, int(app.config.get("PLAYLIST_SNAPSHOT_TIMEOUT_SECONDS", 900)))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError(f"Remote playlist check timed out after {timeout} seconds; nothing was deleted.") from exc
    if result.stderr.strip():
        append_log(item["id"], result.stderr)
    if result.returncode != 0:
        raise SnapshotError(
            f"Remote playlist check failed with status {result.returncode}; nothing was deleted."
        )
    ordered_ids, remote_title = parse_playlist_snapshot(result.stdout)
    titles: dict[str, str] = {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("id") or entry.get("url") or "").strip()
        title = " ".join(str(entry.get("title") or entry.get("fulltitle") or "").split())
        if identifier and title:
            titles[identifier] = title[:240]
    return ordered_ids, remote_title, titles


def _video_label(identifier: str, titles: dict[str, str]) -> str:
    title = titles.get(identifier) or "Title unavailable from YouTube"
    safe_identifier = str(identifier).strip()
    if safe_identifier.startswith(("http://", "https://")):
        url = safe_identifier
    else:
        url = f"https://www.youtube.com/watch?v={urllib.parse.quote(safe_identifier, safe='-_')}"
    return f"{title} [{safe_identifier}] — {url}"


def _log_video_list(playlist_id: str, heading: str, identifiers: set[str], titles: dict[str, str]) -> None:
    append_log(playlist_id, heading)
    for identifier in sorted(identifiers):
        append_log(playlist_id, f" - {_video_label(identifier, titles)}")


def _download(item: dict, archive: Path, managed_dir: Path, report: Path) -> tuple[int, set[str], list[str]]:
    report.unlink(missing_ok=True)
    output = str(managed_dir / "%(playlist_index)03d - %(title)s [%(id)s].%(ext)s")
    command = [
        *_yt_dlp_base(),
        "--newline",
        "-f", "bestaudio/best",
        "-x",
        "--audio-format", "best",
        "--embed-metadata",
        "--embed-thumbnail",
        "--convert-thumbnails", "jpg",
        "--download-archive", str(archive),
        "--print-to-file", "after_move:%(id)s\t%(filepath)s", str(report),
        "-o", output,
        "--",
        item["url"],
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert process.stdout is not None
    unavailable_ids: set[str] = set()
    other_errors: list[str] = []
    for line in process.stdout:
        append_log(item["id"], line)
        if "ERROR:" in line:
            identifier = unavailable_video_id(line)
            if identifier:
                unavailable_ids.add(identifier)
            else:
                other_errors.append(line.strip())
    return process.wait(), unavailable_ids, other_errors


def sync_playlist(item: dict) -> None:
    playlist_id = item["id"]
    music_folder = Path(app.config["MUSIC_FOLDER"]).resolve()
    runtime_root = Path(app.config["PLAYLIST_RUNTIME_DIR"]).resolve()
    runtime_dir = runtime_root / playlist_id
    library_dir_name = item.get("library_dir_name") or f"Playlist [{playlist_id}]"
    playlist_file_name = item.get("playlist_file_name") or f"Playlist [{playlist_id}].m3u"
    managed_dir = (music_folder / "Managed Playlists" / library_dir_name).resolve()
    playlist_path = (music_folder / "Playlists" / playlist_file_name).resolve()
    if not managed_dir.is_relative_to(music_folder) or not playlist_path.is_relative_to(music_folder):
        complete_sync(playlist_id, False, "Unsafe playlist storage path was rejected.")
        return

    music_folder.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    managed_dir.mkdir(parents=True, exist_ok=True)
    archive = runtime_dir / "archive.txt"
    report = runtime_dir / "download-report.tsv"
    manifest = runtime_dir / "manifest.json"
    snapshot_state = runtime_dir / "snapshot-state.json"

    begin_sync_log(playlist_id, f"Checking complete remote snapshot for {item['name']}: {item['url']}")
    try:
        ordered_ids, remote_title, snapshot_titles = _snapshot(item)
    except SnapshotError as exc:
        append_log(playlist_id, str(exc))
        complete_sync(playlist_id, False, str(exc), {"pending_removals": 0})
        return
    append_log(playlist_id, f"Validated remote snapshot: {len(ordered_ids)} entries ({remote_title}).")

    append_log(playlist_id, "Downloading new playlist entries.")
    try:
        return_code, unavailable_ids, download_errors = _download(item, archive, managed_dir, report)
        recorded = record_download_report(report, manifest, managed_dir)
        if recorded:
            append_log(playlist_id, f"Recorded {recorded} newly downloaded managed file(s).")
    except Exception as exc:
        append_log(playlist_id, f"Sync failed to start: {exc}")
        complete_sync(playlist_id, False, str(exc), {"pending_removals": 0})
        return
    if return_code != 0 and (download_errors or not unavailable_ids):
        message = f"yt-dlp exited with status {return_code}; no songs or Navidrome entries were removed."
        append_log(playlist_id, message)
        complete_sync(playlist_id, False, message, {"pending_removals": 0})
        return

    if app.config.get("LYRICS_ENABLED", True):
        try:
            lyric_summary = sync_playlist_lyrics(
                ordered_ids,
                manifest,
                managed_dir,
                runtime_dir / "lyrics-state.json",
                api_url=str(app.config.get("LYRICS_API_URL") or "https://lrclib.net"),
                provider_order=tuple(
                    provider.strip().lower()
                    for provider in str(
                        app.config.get("LYRICS_PROVIDER_ORDER") or "lrclib,simpmusic,netease"
                    ).split(",")
                    if provider.strip()
                ),
                simpmusic_api_url=str(
                    app.config.get("LYRICS_SIMPMUSIC_API_URL")
                    or "https://api-lyrics.simpmusic.org/v1"
                ),
                netease_api_url=str(
                    app.config.get("LYRICS_NETEASE_API_URL") or "https://music.163.com/api"
                ),
                request_delay=max(0.0, float(app.config.get("LYRICS_REQUEST_DELAY_SECONDS", 0.35))),
                missing_retry_seconds=max(300, int(app.config.get("LYRICS_MISSING_RETRY_SECONDS", 604800))),
                error_retry_seconds=max(300, int(app.config.get("LYRICS_ERROR_RETRY_SECONDS", 21600))),
            )
            fetched = lyric_summary["fetched_synced"] + lyric_summary["fetched_plain"]
            lyric_message = (
                f"Lyrics: {fetched} fetched ({lyric_summary['fetched_synced']} synced, "
                f"{lyric_summary['fetched_plain']} plain), {lyric_summary['existing']} already present, "
                f"{lyric_summary['not_found']} not found, {lyric_summary['deferred']} deferred, "
                f"{lyric_summary['errors']} error(s)."
            )
            if lyric_summary["rate_limited"]:
                lyric_message += (
                    " LRCLIB rate limit reached; remaining tracks will wait for the next "
                    "scheduled or Quick sync."
                )
            append_log(playlist_id, lyric_message)
        except Exception as exc:
            append_log(playlist_id, f"Lyrics lookup failed safely: {exc}. Playlist sync will continue.")
    if unavailable_ids:
        append_log(
            playlist_id,
            f"YouTube reports {len(unavailable_ids)} private/unavailable item(s); keeping them pending without deletion.",
        )
        _log_video_list(
            playlist_id,
            "Unavailable/private videos (not deleted):",
            unavailable_ids,
            snapshot_titles,
        )

    confirmation_seconds = max(60, int(app.config.get("PLAYLIST_DELETE_CONFIRM_SECONDS", 300)))
    result = reconcile_snapshot(
        ordered_ids,
        snapshot_state,
        manifest,
        managed_dir,
        archive,
        confirmation_seconds=confirmation_seconds,
    )
    playlist_order = list(result["effective_order"])
    written, missing = write_m3u_playlist(
        playlist_order, manifest, managed_dir, music_folder, playlist_path
    )
    unexpected_missing = set(missing) - unavailable_ids
    # A previous confirmed snapshot can contain an entry that has since been
    # removed remotely. If that entry never produced a managed file, retaining
    # it for the confirmation window must not block the current playlist from
    # being rewritten (otherwise newly downloaded songs remain absent from
    # Navidrome's M3U file). Keep existing files pending as before, but omit
    # file-less stale entries from the playable order.
    stale_missing = unexpected_missing - set(ordered_ids)
    if stale_missing:
        _log_video_list(
            playlist_id,
            "Remote entries removed without a local audio file:",
            stale_missing,
            snapshot_titles,
        )
        playlist_order = [
            identifier for identifier in playlist_order if identifier not in stale_missing
        ]
        written, missing = write_m3u_playlist(
            playlist_order, manifest, managed_dir, music_folder, playlist_path
        )
        unexpected_missing = set(missing) - unavailable_ids
    if unexpected_missing:
        _log_video_list(
            playlist_id,
            "Expected playlist videos with missing local audio:",
            unexpected_missing,
            snapshot_titles,
        )
        remove_archive_ids(archive, unexpected_missing)
        message = (
            f"{len(unexpected_missing)} expected audio file(s) were missing. The archive was repaired; "
            "run Quick sync again. The existing Navidrome playlist was kept."
        )
        append_log(playlist_id, message)
        complete_sync(
            playlist_id,
            False,
            message,
            {"pending_removals": result["pending_removals"]},
        )
        return
    if missing:
        playable_order = [identifier for identifier in playlist_order if identifier not in set(missing)]
        written, remaining_missing = write_m3u_playlist(
            playable_order, manifest, managed_dir, music_folder, playlist_path
        )
        if remaining_missing:
            message = "Playlist changed while it was being written; the existing Navidrome playlist was kept."
            append_log(playlist_id, message)
            complete_sync(playlist_id, False, message, {"pending_removals": result["pending_removals"]})
            return

    deleted_ids = result["deleted_ids"]
    if deleted_ids:
        message = (
            f"Sync completed with {written} Navidrome playlist tracks. Removed "
            f"{result['deleted_files']} managed audio/lyrics file(s) after "
            f"{result['observations']} confirmed checks."
        )
    elif result["pending_removals"]:
        wait_minutes = max(1, (result["wait_seconds"] + 59) // 60)
        message = (
            f"Sync completed with {written} Navidrome playlist tracks. "
            f"Protecting {result['pending_removals']} possible removal(s): confirmation "
            f"{result['observations']}/{result['required']}; retry in about {wait_minutes} minute(s)."
        )
    elif unavailable_ids:
        message = (
            f"Sync completed; Navidrome playlist updated with {written} track(s). "
            f"Waiting for {len(unavailable_ids)} private/unavailable YouTube item(s)."
        )
    else:
        message = f"Sync completed; Navidrome playlist updated with {written} track(s)."
    append_log(playlist_id, message)
    complete_sync(
        playlist_id,
        True,
        message,
        {
            "pending_removals": result["pending_removals"],
            "navidrome_playlist_file": str(playlist_path),
            "remote_entry_count": len(ordered_ids),
            "sync_success_count": written,
            "sync_unavailable_count": len(unavailable_ids),
            "sync_removed_count": len(deleted_ids),
        },
    )


def delete_playlist_files(item: dict) -> None:
    playlist_id = str(item["id"])
    music_folder = Path(app.config["MUSIC_FOLDER"]).resolve()
    library_dir_name = item.get("library_dir_name") or f"Playlist [{playlist_id}]"
    playlist_file_name = item.get("playlist_file_name") or f"Playlist [{playlist_id}].m3u"
    managed_dir = (music_folder / "Managed Playlists" / library_dir_name).resolve()
    playlist_path = (music_folder / "Playlists" / playlist_file_name).resolve()
    if (
        managed_dir == music_folder
        or not managed_dir.is_relative_to(music_folder)
        or not playlist_path.is_relative_to(music_folder)
    ):
        raise ValueError("Unsafe playlist deletion path was rejected.")

    _delete_navidrome_playlist(item, playlist_path)

    deleted_files = 0
    if managed_dir.is_dir():
        for candidate in sorted(managed_dir.rglob("*"), key=lambda path: len(path.parts), reverse=True):
            if candidate.is_file() and candidate.resolve().is_relative_to(managed_dir):
                candidate.unlink()
                deleted_files += 1
        for directory in sorted(
            (path for path in managed_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            managed_dir.rmdir()
        except OSError:
            pass

    if playlist_path.is_file() or playlist_path.is_symlink():
        playlist_path.unlink()

    runtime_root = Path(app.config["PLAYLIST_RUNTIME_DIR"]).resolve()
    runtime_dir = (runtime_root / playlist_id).resolve()
    if runtime_dir != runtime_root and runtime_dir.is_relative_to(runtime_root) and runtime_dir.is_dir():
        shutil.rmtree(runtime_dir)
    log_dir = Path(app.config["PLAYLIST_LOG_DIR"]).resolve()
    log_path = (log_dir / f"{playlist_id}.log").resolve()
    if log_path != log_dir and log_path.is_relative_to(log_dir):
        log_path.unlink(missing_ok=True)

    # Navidrome's watcher observes these directory changes and removes stale
    # playlist entries and missing-file warnings on its next scan.
    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    os.utime(playlist_path.parent, None)
    music_folder.mkdir(parents=True, exist_ok=True)
    os.utime(music_folder, None)
    if not remove_record(playlist_id):
        raise RuntimeError("Playlist record disappeared before deletion completed.")


def _delete_navidrome_playlist(item: dict, playlist_path: Path) -> None:
    base_url = str(app.config.get("NAVIDROME_URL") or "").rstrip("/")
    username = str(app.config.get("NAVIDROME_USERNAME") or "")
    password = str(app.config.get("NAVIDROME_PASSWORD") or "")
    if not base_url or not username or not password:
        raise RuntimeError("Navidrome deletion credentials are not configured.")

    def request(path: str, *, method: str = "GET", payload: bytes | None = None, cookie: str = ""):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        request_object = urllib.request.Request(
            f"{base_url}{path}", data=payload, headers=headers, method=method
        )
        with urllib.request.urlopen(request_object, timeout=15) as response:
            body = response.read()
            return json.loads(body.decode("utf-8")) if body else None

    login = request(
        "/auth/login",
        method="POST",
        payload=json.dumps({"username": username, "password": password}).encode("utf-8"),
    )
    token = str(login.get("token") or "")
    if not token:
        raise RuntimeError("Navidrome login failed while deleting the playlist.")

    playlists = request("/api/playlist", cookie=f"jwt={token}") or []
    expected_path = "/music/" + str(playlist_path.relative_to(Path(app.config["MUSIC_FOLDER"]).resolve())).replace(os.sep, "/")
    matches = [
        playlist
        for playlist in playlists
        if playlist.get("path") == expected_path
        and playlist.get("name") == item.get("name")
    ]
    for playlist in matches:
        playlist_id = urllib.parse.quote(str(playlist["id"]), safe="")
        request(f"/api/playlist/{playlist_id}", method="DELETE", cookie=f"jwt={token}")


def main() -> None:
    poll_seconds = max(5, int(app.config.get("PLAYLIST_WORKER_POLL_SECONDS", 10)))
    with app.app_context():
        recover_interrupted()
        while True:
            delete_item = claim_delete()
            if delete_item:
                try:
                    delete_playlist_files(delete_item)
                except Exception as exc:
                    complete_delete_failure(delete_item["id"], f"Playlist deletion failed: {exc}")
                continue
            item = claim_next()
            if item:
                sync_playlist(item)
            else:
                time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
