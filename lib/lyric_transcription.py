"""Missing-lyrics fallback using the existing durable transcription queue."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from lib.lyrics import AUDIO_SUFFIXES, LyricsLookupError, existing_lyrics, playlist_audio_files, probe_audio, lyrics_quality
from lib.transcription_store import Store, QueueError, digest

SAVING = 'Saving synced lyrics'
INSTRUMENTAL_RESULT = '[Cybrog AI: likely instrumental; no reliable singing recognized.]'


def signature(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def queue_for(upload='/srv/tamestorage/uploads'):
    upload = Path(upload)
    return Store(upload.parent/'.tamestorage_state/transcription',
                 upload/'temp/Transcription', upload/'Admin/Transcripts')


def source_path(options, music_root='/srv/music'):
    root = (Path(music_root)/'Managed Playlists').resolve(strict=True)
    relative = options.get('lyrics_source', '')
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise QueueError('Unsafe lyrics source.')
    source = root/relative
    # Reject a symlink at any component, including links that still point inside root.
    current = root
    for part in Path(relative).parts:
        if part in {'.', '..'}: raise QueueError('Unsafe lyrics source.')
        current = current/part
        if current.is_symlink(): raise QueueError('Unsafe lyrics source.')
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved.suffix.lower() not in AUDIO_SUFFIXES:
        raise QueueError('Unsafe lyrics source.')
    return resolved


def enqueue_playlist(item, ordered_ids, manifest, managed_dir, lyric_state, *,
                     music_root='/srv/music', queue=None):
    counts = dict(queued=0, already_queued=0, existing=0, instrumental=0, excluded=0, errors=0)
    queue = queue or queue_for()
    root = (Path(music_root)/'Managed Playlists').resolve(strict=True)
    managed_dir = Path(managed_dir).resolve(strict=True)
    if not managed_dir.is_relative_to(root): raise QueueError('Unsafe playlist directory.')
    try: tracks = json.loads(Path(lyric_state).read_text()).get('tracks', {})
    except (OSError, ValueError): tracks = {}
    if not isinstance(tracks, dict): tracks = {}
    # One lock covers imports/backfill and makes deduplication crash-safe without a new DB.
    with (queue.root/'lyrics-enqueue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with queue.connect() as db:
            known = {}
            for row in db.execute('SELECT id,options,state,stage FROM jobs'):
                options = json.loads(row['options'])
                if options.get('kind') == 'lyrics': known[options['lyrics_key']] = dict(row)
        for source in playlist_audio_files(ordered_ids, Path(manifest), managed_dir):
            row = None
            try:
                if existing_lyrics(source): counts['existing'] += 1; continue
                relative = str(source.relative_to(root))
                sig = signature(source)
                key = hashlib.sha256(json.dumps([relative, sig]).encode()).hexdigest()
                previous = known.get(key)
                if previous and previous['state'] != 'uploading':
                    counts['instrumental' if previous['stage']=='Likely instrumental' else 'already_queued'] += 1; continue
                metadata = probe_audio(source)
                if metadata.thai_text: counts['excluded'] += 1; continue
                if metadata.embedded_lyrics: counts['existing'] += 1; continue
                if previous:
                    row = queue.get(previous['id'])
                else:
                    options = dict(kind='lyrics', lyrics_key=key, lyrics_source=relative,
                                   lyrics_signature=sig, lyrics_title=metadata.title,
                                   lyrics_artist=metadata.artist, lyrics_album=metadata.album)
                    row = queue.create(source.name, sig[2], 'Lyrics: '+metadata.title,
                                       {'language':'auto', 'llm':False, 'speakers':False}, internal_options=options)
                    known[key] = row
                # Snapshot to the already-protected NAS inbox; never lend the worker a music file to delete.
                target = queue.input_path(row); temporary = target.with_name(target.name+'.part')
                h = hashlib.sha256()
                try:
                    with source.open('rb') as inp, temporary.open('wb') as out:
                        for block in iter(lambda:inp.read(1024**2), b''): out.write(block); h.update(block)
                        out.flush(); os.fsync(out.fileno())
                    if signature(source) != sig: raise QueueError('The song changed during queueing.')
                    os.replace(temporary, target)
                    queue.uploaded(row['id'], h.hexdigest()); counts['queued'] += 1
                finally: temporary.unlink(missing_ok=True)
            except (OSError, ValueError, QueueError, LyricsLookupError, subprocess.SubprocessError):
                counts['errors'] += 1
                if row:
                    try:
                        queue.action(row['id'], 'cancel'); queue.action(row['id'], 'remove')
                    except (OSError, QueueError): pass
    return counts


def complete_lyrics(queue, row, artifacts, db):
    for name, value in artifacts.items():
        if digest(queue.work_dir(row['id'])/(name+'.ready')) != value['sha256']:
            raise QueueError('Result verification failed.')
    text = (queue.work_dir(row['id'])/'transcript.ready').read_text(encoding='utf-8')
    instrumental = text.strip() == INSTRUMENTAL_RESULT
    if not instrumental and (lyrics_quality(text)!='usable' or not re.search(r'^\[\d+:\d{2}\.\d{2}\]\S', text, re.M)):
        raise QueueError('No timestamped singing was recognized.')
    options=json.loads(row['options']);options['lyrics_disposition']='likely_instrumental' if instrumental else 'transcribed'
    db.execute("UPDATE jobs SET state='completed',stage=?,progress=100,options=?,updated=? WHERE id=?",
               (SAVING,json.dumps(options), time.time(), row['id']))
    queue._wake(db)
    return queue.public(queue.get(row['id'], db))


def publish_ready(queue, music_root='/srv/music'):
    """Run in the existing host dispatcher; the web music mount stays read-only."""
    count = 0
    with queue.connect() as db:
        rows = list(db.execute("SELECT * FROM jobs WHERE state='completed' AND stage=?", (SAVING,)))
    for raw in rows:
        row = dict(raw); options = json.loads(row['options'])
        if options.get('kind') != 'lyrics': continue
        stage, message = 'Synced lyrics ready', 'Automatically transcribed lyrics; wording and timing may need review.'
        try:
            source = source_path(options, music_root)
            if signature(source) != options['lyrics_signature'] or digest(source) != row['sha256']:
                stage, message = 'Song changed; lyrics skipped', 'Original music was preserved.'
            elif existing_lyrics(source) or probe_audio(source).embedded_lyrics:
                stage, message = 'Existing lyrics kept', 'Lyrics appeared while the song was queued.'
            elif options.get('lyrics_disposition')=='likely_instrumental':
                stage,message='Likely instrumental','Cybrog AI found no reliable singing; flagged as likely instrumental, not a processing failure.'
            else:
                ready = queue.work_dir(row['id'])/'transcript.ready'
                expected = json.loads(row['artifacts'])['transcript']['sha256']
                if digest(ready) != expected: raise QueueError('Result verification failed.')
                target = source.with_suffix('.lrc')
                temporary = source.parent/('.lyrics-'+row['id']+'.part')
                try:
                    with ready.open('rb') as inp, temporary.open('wb') as out:
                        shutil.copyfileobj(inp, out, 1024**2); out.flush(); os.fsync(out.fileno())
                    # Link publishes atomically without overwriting a provider/user lyric file.
                    try: os.link(temporary, target)
                    except FileExistsError:
                        stage, message = 'Existing lyrics kept', 'A lyric file appeared during publication.'
                    else: count += 1; os.utime(source.parent, None)
                finally: temporary.unlink(missing_ok=True)
        except FileNotFoundError:
            stage, message = 'Song removed; lyrics skipped', 'Original music was not recreated.'
        except QueueError as error:
            stage, message = 'Lyrics need attention', error.message
        # I/O failures propagate so the existing dispatcher retries only pending publication.
        # Only managed inbox audio is disposable. Source songs and reports remain intact.
        try:
            temporary_audio = queue.input_path(row)
            if temporary_audio.is_file() and digest(temporary_audio) == row['sha256']:
                temporary_audio.unlink(); temporary_audio.parent.rmdir()
        except (OSError, QueueError): pass
        with queue.transaction() as db:
            db.execute('UPDATE jobs SET stage=?,message=?,updated=? WHERE id=?', (stage, message, time.time(), row['id']))
            queue._wake(db)
    return count
