from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


AUDIO_SUFFIXES = {
    ".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".wma"
}
LYRIC_SUFFIXES = (".ttml", ".yaml", ".yml", ".elrc", ".lrc", ".srt", ".txt")
DEFAULT_API_URL = "https://lrclib.net"
DEFAULT_SIMPMUSIC_API_URL = "https://api-lyrics.simpmusic.org/v1"
DEFAULT_NETEASE_API_URL = "https://music.163.com/api"
DEFAULT_PROVIDER_ORDER = ("lrclib", "simpmusic", "netease")
USER_AGENT = "TamestorageLyrics/1.0 (self-hosted Navidrome integration)"


class LyricsLookupError(RuntimeError):
    pass


class LyricsRateLimitError(LyricsLookupError):
    pass


@dataclass(frozen=True)
class AudioMetadata:
    title: str
    artist: str
    album: str
    duration: float


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value.rstrip() + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _fallback_title(path: Path) -> str:
    title = re.sub(r"^\d{1,4}\s*-\s*", "", path.stem)
    title = re.sub(r"\s*\[[A-Za-z0-9_-]{6,}\]\s*$", "", title)
    return title.strip()


def probe_audio(path: Path) -> AudioMetadata:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:format_tags:stream=duration:stream_tags", "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise LyricsLookupError(f"ffprobe failed for {path.name}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LyricsLookupError(f"ffprobe returned invalid metadata for {path.name}") from exc

    format_data = payload.get("format") if isinstance(payload, dict) else {}
    format_data = format_data if isinstance(format_data, dict) else {}
    merged_tags: dict[str, str] = {}
    format_tags = format_data.get("tags")
    if isinstance(format_tags, dict):
        merged_tags.update({str(key).lower(): str(value) for key, value in format_tags.items()})

    durations: list[float] = []
    try:
        durations.append(float(format_data.get("duration") or 0))
    except (TypeError, ValueError):
        pass
    streams = payload.get("streams", []) if isinstance(payload, dict) else []
    if isinstance(streams, list):
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            tags = stream.get("tags")
            if isinstance(tags, dict):
                for key, value in tags.items():
                    merged_tags.setdefault(str(key).lower(), str(value))
            try:
                durations.append(float(stream.get("duration") or 0))
            except (TypeError, ValueError):
                pass

    return AudioMetadata(
        title=str(merged_tags.get("title") or _fallback_title(path)).strip(),
        artist=str(merged_tags.get("artist") or merged_tags.get("album_artist") or "").strip(),
        album=str(merged_tags.get("album") or "").strip(),
        duration=max(durations, default=0.0),
    )


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"\b(?:official|audio|video|lyrics?|lyric video|music video|hd|hq)\b", " ", value)
    value = "".join(
        character if unicodedata.category(character)[0] in {"L", "M", "N"} else " "
        for character in value
    )
    return " ".join(value.split())


def _similarity(left: str, right: str) -> float:
    left_normalized = _normalize(left)
    right_normalized = _normalize(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    if left_normalized in right_normalized or right_normalized in left_normalized:
        shorter = min(len(left_normalized), len(right_normalized))
        longer = max(len(left_normalized), len(right_normalized))
        return max(0.86, shorter / longer)
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def choose_candidate(results: list[dict], metadata: AudioMetadata) -> dict | None:
    scored: list[tuple[float, dict]] = []
    for candidate in results:
        if not isinstance(candidate, dict):
            continue
        title_score = _similarity(metadata.title, str(candidate.get("trackName") or ""))
        if title_score < 0.62:
            continue
        artist_score = _similarity(metadata.artist, str(candidate.get("artistName") or ""))
        try:
            candidate_duration = float(candidate.get("duration") or 0)
        except (TypeError, ValueError):
            candidate_duration = 0.0
        duration_delta = abs(metadata.duration - candidate_duration) if metadata.duration and candidate_duration else 0
        duration_score = max(0.0, 1.0 - duration_delta / 30.0) if duration_delta else 0.7
        if duration_delta > max(35.0, metadata.duration * 0.15):
            continue
        score = title_score * 0.62 + artist_score * 0.28 + duration_score * 0.10
        if candidate.get("syncedLyrics"):
            score += 0.04
        if metadata.artist and artist_score < 0.30:
            score -= 0.15
        scored.append((score, candidate))
    if not scored:
        return None
    score, selected = max(scored, key=lambda item: item[0])
    return selected if score >= 0.62 else None


def _request_json(
    url: str,
    timeout: int = 20,
    *,
    source: str = "lyrics provider",
    headers: dict[str, str] | None = None,
) -> object:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        if exc.code == 429:
            raise LyricsRateLimitError(
                f"{source} rate limit reached; the song is eligible on the next playlist sync"
            ) from exc
        raise LyricsLookupError(f"{source} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LyricsLookupError(f"{source} request failed: {exc}") from exc


def _search(metadata: AudioMetadata, api_url: str) -> list[dict]:
    query = {"track_name": metadata.title}
    if metadata.artist:
        query["artist_name"] = metadata.artist
    url = f"{api_url.rstrip('/')}/api/search?{urllib.parse.urlencode(query)}"
    payload = _request_json(url, source="LRCLIB")
    results = payload if isinstance(payload, list) else []
    if results or not metadata.artist:
        return [item for item in results if isinstance(item, dict)]
    fallback_url = f"{api_url.rstrip('/')}/api/search?{urllib.parse.urlencode({'q': metadata.title})}"
    payload = _request_json(fallback_url, source="LRCLIB")
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _youtube_id(path: Path) -> str:
    match = re.search(r"\[([A-Za-z0-9_-]{11})\]\s*$", path.stem)
    return match.group(1) if match else ""


def _payload_items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    return []


def _search_simpmusic(metadata: AudioMetadata, api_url: str) -> list[dict]:
    query = " ".join(value for value in (metadata.title, metadata.artist) if value).strip()
    url = f"{api_url.rstrip('/')}/search?{urllib.parse.urlencode({'q': query})}"
    payload = _request_json(url, source="SimpMusic")
    results = []
    for item in _payload_items(payload):
        results.append(
            {
                "trackName": item.get("songTitle") or item.get("title") or "",
                "artistName": item.get("artistName") or item.get("artist") or "",
                "duration": item.get("durationSeconds") or item.get("duration") or 0,
                "syncedLyrics": item.get("syncedLyrics") or "",
                "plainLyrics": item.get("plainLyric") or item.get("plainLyrics") or "",
                "providerId": item.get("videoId") or item.get("id") or "",
            }
        )
    return results


def _fetch_simpmusic(identifier: str, api_url: str) -> tuple[str, str]:
    url = f"{api_url.rstrip('/')}/{urllib.parse.quote(identifier)}"
    items = _payload_items(_request_json(url, source="SimpMusic"))
    if not items:
        return "", ""
    item = items[0]
    return (
        str(item.get("syncedLyrics") or "").strip(),
        str(item.get("plainLyric") or item.get("plainLyrics") or "").strip(),
    )


def _search_netease(metadata: AudioMetadata, api_url: str) -> list[dict]:
    query = " ".join(value for value in (metadata.title, metadata.artist) if value).strip()
    params = urllib.parse.urlencode({"s": query, "type": 1, "limit": 8, "offset": 0})
    payload = _request_json(
        f"{api_url.rstrip('/')}/search/get?{params}",
        source="NetEase",
        headers={"Referer": "https://music.163.com/"},
    )
    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    songs = result.get("songs", []) if isinstance(result, dict) else []
    candidates = []
    for song in songs if isinstance(songs, list) else []:
        if not isinstance(song, dict):
            continue
        artists = song.get("artists") or song.get("ar") or []
        artist = ", ".join(
            str(item.get("name") or "") for item in artists if isinstance(item, dict)
        ).strip(", ")
        duration = song.get("duration") or song.get("dt") or 0
        try:
            duration = float(duration)
            if duration > 10000:
                duration /= 1000
        except (TypeError, ValueError):
            duration = 0
        candidates.append(
            {
                "trackName": song.get("name") or "",
                "artistName": artist,
                "duration": duration,
                "providerId": song.get("id"),
            }
        )
    return candidates


def _fetch_netease(identifier: str | int, api_url: str) -> tuple[str, str]:
    params = urllib.parse.urlencode({"id": identifier, "kv": -1, "lv": -1, "tv": -1})
    payload = _request_json(
        f"{api_url.rstrip('/')}/song/lyric?{params}",
        source="NetEase",
        headers={"Referer": "https://music.163.com/"},
    )
    if not isinstance(payload, dict):
        return "", ""
    lyric = payload.get("lrc", {})
    original = str(lyric.get("lyric") or "").strip() if isinstance(lyric, dict) else ""
    if re.search(r"\[\d{1,3}:\d{2}(?:[.:]\d+)?\]", original):
        return original, ""
    return "", original


def _write_lyrics(path: Path, synced: str, plain: str) -> tuple[str, Path | None]:
    if synced:
        target = path.with_suffix(".lrc")
        _atomic_text(target, synced)
        return "fetched_synced", target
    if plain:
        target = path.with_suffix(".txt")
        _atomic_text(target, plain)
        return "fetched_plain", target
    return "not_found", None


def existing_lyrics(path: Path) -> Path | None:
    for suffix in LYRIC_SUFFIXES:
        candidate = path.with_suffix(suffix)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def fetch_lyrics(
    path: Path,
    api_url: str = DEFAULT_API_URL,
    *,
    provider_order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER,
    simpmusic_api_url: str = DEFAULT_SIMPMUSIC_API_URL,
    netease_api_url: str = DEFAULT_NETEASE_API_URL,
) -> tuple[str, Path | None]:
    current = existing_lyrics(path)
    if current:
        return "existing", current
    metadata = probe_audio(path)
    if not metadata.title:
        raise LyricsLookupError(f"No title metadata for {path.name}")
    errors: list[str] = []
    rate_limited = False
    instrumental = False
    video_id = _youtube_id(path)
    for provider in provider_order:
        try:
            if provider == "lrclib":
                selected = choose_candidate(_search(metadata, api_url), metadata)
                if not selected:
                    continue
                synced = str(selected.get("syncedLyrics") or "").strip()
                plain = str(selected.get("plainLyrics") or "").strip()
                if selected.get("instrumental") and not synced and not plain:
                    instrumental = True
                    continue
            elif provider == "simpmusic":
                results = _search_simpmusic(metadata, simpmusic_api_url)
                selected = next(
                    (item for item in results if video_id and str(item.get("providerId")) == video_id),
                    None,
                ) or choose_candidate(results, metadata)
                if not selected or not selected.get("providerId"):
                    continue
                synced, plain = _fetch_simpmusic(str(selected["providerId"]), simpmusic_api_url)
            elif provider == "netease":
                selected = choose_candidate(_search_netease(metadata, netease_api_url), metadata)
                if not selected or selected.get("providerId") in {None, ""}:
                    continue
                synced, plain = _fetch_netease(selected["providerId"], netease_api_url)
            else:
                continue
            if synced or plain:
                return _write_lyrics(path, synced, plain)
        except LyricsRateLimitError as exc:
            rate_limited = True
            errors.append(str(exc))
        except LyricsLookupError as exc:
            errors.append(str(exc))
    if errors:
        if rate_limited:
            raise LyricsRateLimitError("; ".join(errors))
        raise LyricsLookupError("; ".join(errors))
    return ("instrumental", None) if instrumental else ("not_found", None)


def playlist_audio_files(
    ordered_ids: list[str], manifest_path: Path, managed_dir: Path
) -> list[Path]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
    files = files if isinstance(files, dict) else {}
    managed_root = managed_dir.resolve()
    selected: list[Path] = []
    seen: set[Path] = set()
    for identifier in ordered_ids:
        values = files.get(identifier, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            candidate = Path(value).resolve()
            if (
                candidate.is_file()
                and candidate.is_relative_to(managed_root)
                and candidate.suffix.lower() in AUDIO_SUFFIXES
                and candidate not in seen
            ):
                selected.append(candidate)
                seen.add(candidate)
    return selected


def summarize_playlist_lyrics(
    ordered_ids: list[str], manifest_path: Path, managed_dir: Path, state_path: Path
) -> dict[str, int]:
    """Return lyric health counts without performing any network lookups."""
    audio_files = playlist_audio_files(ordered_ids, manifest_path, managed_dir)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    tracks = state.get("tracks", {}) if isinstance(state, dict) else {}
    tracks = tracks if isinstance(tracks, dict) else {}

    summary = {
        "total": len(audio_files),
        "lyrics": 0,
        "errors": 0,
        "instrumental": 0,
        "not_found": 0,
        "pending": 0,
        "problems": 0,
    }
    for audio in audio_files:
        relative = str(audio.relative_to(managed_dir.resolve()))
        status = tracks.get(relative, {})
        status = str(status.get("status") or "") if isinstance(status, dict) else ""
        if existing_lyrics(audio):
            summary["lyrics"] += 1
        elif status == "error":
            summary["errors"] += 1
        elif status == "instrumental":
            summary["instrumental"] += 1
        elif status == "not_found":
            summary["not_found"] += 1
        else:
            summary["pending"] += 1
    summary["problems"] = summary["errors"] + summary["instrumental"] + summary["not_found"]
    return summary


def sync_playlist_lyrics(
    ordered_ids: list[str],
    manifest_path: Path,
    managed_dir: Path,
    state_path: Path,
    *,
    api_url: str = DEFAULT_API_URL,
    provider_order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER,
    simpmusic_api_url: str = DEFAULT_SIMPMUSIC_API_URL,
    netease_api_url: str = DEFAULT_NETEASE_API_URL,
    request_delay: float = 0.35,
    missing_retry_seconds: int = 7 * 24 * 60 * 60,
    error_retry_seconds: int = 6 * 60 * 60,
    now: float | None = None,
) -> dict:
    current_time = float(time.time() if now is None else now)
    provider_key = ",".join(provider_order)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    tracks = state.setdefault("tracks", {})
    if not isinstance(tracks, dict):
        tracks = {}
        state["tracks"] = tracks

    summary = {
        "audio_files": 0,
        "existing": 0,
        "fetched_synced": 0,
        "fetched_plain": 0,
        "not_found": 0,
        "instrumental": 0,
        "errors": 0,
        "deferred": 0,
        "rate_limited": False,
    }
    audio_files = playlist_audio_files(ordered_ids, manifest_path, managed_dir)
    summary["audio_files"] = len(audio_files)
    for path in audio_files:
        relative = str(path.relative_to(managed_dir.resolve()))
        current = existing_lyrics(path)
        if current:
            summary["existing"] += 1
            tracks[relative] = {
                "status": "existing",
                "lyrics": current.name,
                "attempted_at": current_time,
                "providers": provider_key,
            }
            continue

        previous = tracks.get(relative)
        if isinstance(previous, dict):
            previous_status = str(previous.get("status") or "")
            try:
                previous_attempt = float(previous.get("attempted_at") or 0)
            except (TypeError, ValueError):
                previous_attempt = 0
            retry_after = error_retry_seconds if previous_status == "error" else missing_retry_seconds
            if (
                previous.get("providers") == provider_key
                and previous_status in {"not_found", "instrumental", "error"}
                and current_time - previous_attempt < retry_after
            ):
                summary["deferred"] += 1
                continue

        try:
            status, lyric_path = fetch_lyrics(
                path,
                api_url=api_url,
                provider_order=provider_order,
                simpmusic_api_url=simpmusic_api_url,
                netease_api_url=netease_api_url,
            )
        except LyricsRateLimitError:
            summary["rate_limited"] = True
            break
        except Exception as exc:
            summary["errors"] += 1
            tracks[relative] = {
                "status": "error",
                "attempted_at": current_time,
                "message": str(exc)[:300],
                "providers": provider_key,
            }
        else:
            summary[status] = int(summary.get(status, 0)) + 1
            tracks[relative] = {
                "status": status,
                "lyrics": lyric_path.name if lyric_path else None,
                "attempted_at": current_time,
                "providers": provider_key,
            }
        _atomic_json(state_path, state)
        if request_delay > 0:
            time.sleep(request_delay)

    active = {str(path.relative_to(managed_dir.resolve())) for path in audio_files}
    for stale in set(tracks) - active:
        tracks.pop(stale, None)
    state["updated_at"] = current_time
    _atomic_json(state_path, state)
    if summary["fetched_synced"] or summary["fetched_plain"]:
        os.utime(managed_dir, None)
    return summary
