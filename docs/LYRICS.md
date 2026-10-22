# Automatic missing song lyrics

Installed and verified 2026-10-17 Singapore. Recheck live queue and controls before
claiming individual tracks are finished.

## Behavior

The existing playlist worker tries normal lyric providers first. After a successful
playlist sync, `lib/lyric_transcription.py:enqueue_playlist` sends only remaining
missing-lyrics tracks into the existing durable transcription queue. It does not
scan the library continuously or install another scheduler/model/server. Existing
sidecar and embedded lyrics take precedence. Provider instrumental notices are not usable lyric text. When public lookup
has no useful result, the AI verifies the audio before flagging likely instrumental. Any Thai character (U+0E00–U+0E7F) in format/stream text metadata
excludes that track. Playlist/album names alone are not an exclusion rule: this
supersedes the user's original Thai playlist exception.

The enqueue operation snapshots audio to protected NAS storage, fingerprints the
source and records an atomic persistent deduplication key. Repeated playlist syncs
cannot repeatedly enqueue the same source version, including unsuccessful results.
Interrupted enqueues resume safely. The original song is never a disposable input.

Cyborg reuses installed Whisper large-v3 (original and normalized audio) and
large-v2 (original) with existing word timestamps and recognition reconciliation.
Lyrics disable Parakeet, speaker models and LLM rewriting. `lyrics_output.py`
groups recognized words into standard centisecond LRC lines, preserving repeats.
No new model, dependency, alignment pass or resident process is added.

**Recognition is not proof of singing.** Initial live instrumental `Einaudi:
Experience` produced Whisper hallucinations. Its generated LRC was removed after
matching its exact artifact checksum; the music was untouched. The renderer now
classifies a result as likely instrumental if more than half of recognized words
have existing cross-pass `uncertain` flags or no intelligible words remain. This uses already computed reconciliation data, without another
model pass. It rejects that actual instrumental result while accepting the three
first vocal results. This is conservative screening, not guaranteed instrumental
classification or human-verified lyrics. Classification is a completed result, not a processing failure. Existing source-version
queue records prevent repeated inference. Genuine processing errors retain Retry.
Never manufacture lyrics for silence.

The host dispatcher publishes a verified `.lrc` beside the source, exclusively and
atomically, after rechecking source fingerprint/SHA. Changed/removed songs skip
publication. Lyrics that appeared during processing are retained. Likely-instrumental results
publish no lyric sidecar; their flag remains in the existing queue history. Playlist
health reads that flag only for the matching source fingerprint and excludes it
from problem counts. Source folders
get their mtime touched as with existing provider downloads. Navidrome picks up
sidecar lyrics without requiring an application restart. Automatically generated
LRC has a provenance/review warning. Wording and timing can be imperfect.

## Queue, availability and resources

- Existing queue: `/srv/tamestorage/.tamestorage_state/transcription/queue.sqlite`.
- NAS snapshots: `/srv/tamestorage/uploads/temp/Transcription/<job-id>/Recording.*`.
- Reports/checkpoints: queue `jobs/<job-id>/`; generated lyrics do not create extra
  copies in `uploads/Admin/Transcripts`. Admin downloads use protected artifacts.
- Existing temp cleaner excludes the Transcription tree; existing inbox watcher
  ignores managed UUID subdirectories. Offline queued audio survives daily cleanup.
- Existing `tamestorage-transcription.path` starts the existing dispatcher service.
  It retries only while pending work exists, including while Cyborg is unavailable.
  When empty, dispatcher and inference exit. Shared **Server tasks Off** interrupts
  processing and preserves checkpoints; On permits resume. Respect the live state.
- Existing pinned restricted SSH key, authenticated worker API, 120-second leases,
  8-second heartbeat, external-disk UUID checks, GPU free-memory gate and thermal
  pause remain in force. No new Mac code, storage, process or polling is needed.
- NAS audio snapshot is removed only after finished publication/skip, with checksum
  protection. Failed/interrupted jobs retain inputs/checkpoints for review/recovery.
  Preserve these purposeful records; they are not completed-work backup leftovers.

## Maintained files and deployment

Main server source: `/srv/docker/tamestorage/lib/{lyrics,lyric_transcription,
transcription_store}.py`, `routes/transcription.py`, `playlist_worker.py` and tests.
Host dispatcher: `~/.local/lib/tamestorage-transcription/dispatch.py` imports that
source. Existing systemd service has only two additional writable scopes: managed
music and the protected transcription inbox. Its 128MB limit and strict protection
remain. Web music stays read-only. Playlist worker gains narrow writable inbox and
read-only normal transcript output mounts needed by the existing Store constructor.

Cyborg runtime: `/run/media/tameszaza/New Volume/Cyborg Transcription/`:
`worker.py`, `infer.py`, `lyrics_output.py`, `test_lyrics_output.py`. All models and
job scratch remain on external UUID A87A-15B6, not its internal SSD.

Web and playlist-worker use the same deployed image
`a149635f930f` (latest, verified 2026-10-17 afternoon).
Other services were not recreated. Future app changes use the normal build/deploy
procedure after checking both NAS mounts. Do not restore old images independently
of host code and the required playlist-worker mounts.

## Verification and initial inventory

- 20 server queue/API/lyrics tests, 1 actual-dispatcher offline/deduplication test,
  3 Cyborg LRC tests, and 3 existing shared-Off/worker tests passed (27 total).
- Real future-sync hook replay inside the live playlist-worker queued zero duplicates
  (9 already queued, 608 already with lyrics in the tested pop playlist).
- Real 154-second Despacito job completed: original SHA unchanged; 32 ordered timed
  lines; NAS input and Cyborg scratch removed. Actual authenticated Navidrome
  `getLyricsBySongId` returned `synced=true`, 32 lines without a restart or rescan.
- Initial 827 managed audio copies: 25 lacked sidecars; six had Thai metadata and
  were excluded; 19 were queued. Of those, Experience's erroneous output was removed
  and the job marked for attention. Do not call all queued jobs fixed. Query live
  state for current completed/pending/rejected counts.
- Despacito, Girls Like You (Matt Johnson), and I Like You the Most (SHAD cover)
  were the first three saved vocal outputs passing the recognition gate. Closer
  (Postmodern Jukebox / Kenton Chen) subsequently completed as well.

Never log credentials, bearer keys, full private reports or lyrics. Titles/artists
may be listed when the user requests a song report. Do not delete source music or
existing lyrics to make counts look complete.


## Cybrog AI lyric attribution (2026-10-17)

User requires attribution at the top of automatically transcribed lyrics. External
Cyborg `lyrics_output.py` now starts LRC metadata with
`[by:Cybrog AI; automatic transcription, review wording and timing]` and adds the
first visible timed line `[00:00.00]Transcribed by Cybrog AI`. Existing song words
and timestamps are retained. Six already published generated sidecars were migrated
only after matching the original queue artifact SHA, with atomic replacement;
provider/user lyrics were untouched. Historical queue artifacts/checksums remain
immutable, so migrated sidecar bytes now intentionally differ from original
artifacts. Future artifacts include attribution directly. Actual Navidrome API
confirmed `synced=true` and first line start0/value `Transcribed by Cybrog AI`.
Three LRC tests pass. No new model pass, service, polling, backup or staging.


## Provider validation and source matching (2026-10-17 update)

Existing and newly fetched lrc/elrc/txt/srt content must contain actual body text.
Credit-only, timestamp-only, metadata-only and instrumental notices do not count.
Short genuine/repeated/non-Latin lyrics remain valid. Rejected synced results can
fall back to useful plain lyrics or another provider. Known normalized artists
require similarity at least0.60: a same-title song by another artist is insufficient.
Persisted lyric state records provider, matched track details and rejected-result
reasons for future diagnosis. Existing status updates preserve that provenance.

Public providers remain first. Only when no useful public result is available does
the existing AI queue process the source. No separate classification model/pass: it
reuses existing recognition and disagreement data. infer.py writes lyrics_analysis
in the existing gzip diagnostics. Worker, validated completion and host dispatcher
accept likely-instrumental as a normal completed result. Existing UI displays
Likely instrumental, and real stage failures still show Needs attention.

The previous nine recognition-rejection failures are being completed from saved
checkpoints without rerunning ASR. Experience was reclassified from saved verified
analysis with its diagnostic alternatives preserved and report checksums updated.
Beanie completed Likely instrumental with original audio unchanged and no fake
sidecar. Dorin / The One that got Away was repaired by AI after wrong-artist public
matching was rejected; actual Navidrome API returned35 synced lines and first line
Transcribed by Cybrog AI. Original Dorin audio SHA unchanged.

One-time inventory removed43 verified unusable sidecars. Repair queued42 new
fallback tracks plus the Dorin correction and resumed9 cached rejected jobs. All44
bulk jobs briefly held to prioritize the example were resumed. Remaining processing
continues in the same durable queue. Do not report queued jobs as already finished.
33 provider/queue/API tests and4 Cyborg output tests passed; actual UI renderer/CSS
was rendered and visually inspected with success/instrumental/error fixtures.

Classification queue entries are primary feature state, not disposable backups.
Removing one removes its source-version classification/deduplication history; a
later sync may check it again. Changed audio automatically ceases to match an old
flag. Ordinary provider retry/backoff stays in force. No new scanner, cron, daemon,
model download, Mac plugin or idle model was added.


### Queue-priority correction from this repair

Six of the nine old rejected jobs reused saved recognition. The temporary hold
used Cancel/Retry for44 bulk jobs; existing Cyborg startup cleanup deletes local
scratch for cancelled jobs. Consequently the remaining three old cached jobs
(Positive Mood, Akaishi, My Heart Is For You) could require one fresh ASR run after
resume, though their primary NAS snapshots were preserved. Earlier statements
that all nine would reuse caches were an intention, not the final observed result.
All44 were resumed and no job remains held. Future repairs should enqueue/verify
the exemplar before bulk enqueue, or use an actual dispatcher pause; do not use
Cancel as a temporary priority mechanism when cached work must be retained. This
does not change the normal source-version deduplication or completed instrumental
flag: classified unchanged tracks are not automatically inferred again.

## Music sync: review uncertain Cybrog lyrics (2026-10-17)

Music sync now has Review lyrics on each playlist. The on-demand native dialog
selects a completed AI song, shows uncertain timed lines first, names the original
uncertain words, supports replay of each line plus the normal audio player, and
allows editing/confirmation. Show all lyrics also exposes the other lines. Editing
confirms a line; uncheck Confirmed to leave it for later. Clearing words keeps the
timestamp as a silent gap and the line remains editable on subsequent visits.
Save changes persists both corrected LRC and confirmed line indices. Attribution,
metadata, timestamps, source music and original ASR artifacts remain intact.
Instrumental estimates have no invented lyrics to edit. Existing/public lyrics
keep their original priority and are not included as AI-generated reviews.

Implementation: lib/lyric_review.py, routes/music.py, templates/music_sync.html,
static/js/lyric-review.js and static/css/lyric-review.css. Reuses the existing
transcription SQLite job/options, verified gzip details (selected_word_units),
source fingerprint and SHA, and existing playlist_worker.py loop. No new models,
ASR pass, process, scheduler or Mac installation/data. Existing web music mount
remains read-only. Saves are durably queued in lyrics_review_pending, with a
lyrics-review-wake marker checked by the already-running music worker. Only that
worker atomically replaces the matching AI .lrc; fsync and exact pending-content
recognition cover a replace-before-DB-commit retry. Marker is cleared on a later
empty transaction so a pre-commit crash cannot lose pending work. While no review
is pending the extra cost is an existing-loop marker stat; details/audio are loaded
only when opened/played. Close pauses/unloads audio, clears rows/state and aborts
requests. Save-status polling exists only while an editor is actively saving,
bounded to about one minute; a busy playlist sync may defer publication, with an
explicit queued message. Review/save works without Cyborg being online or enabled.
Shared Server tasks admission control and the active ASR queue are unchanged.

Admin auth and CSRF reuse transcription protection; APIs/audio are private no-store,
audio supports HTTP ranges. HEAD is read-only. Body/text sizes are bounded; paths
and symlinks checked. Revisions prevent stale-window overwrites. Changed/removed
songs, externally modified/provider lyrics and stale publication hashes are
preserved with an error, rather than recreated/overwritten. Legacy completed jobs
without lyrics_disposition are recognized by Synced lyrics ready. Earlier byline
migration is accepted only with a verified original artifact and exact equality of
all non-attribution lines; arbitrary lyric edits still fail the ownership guard.

Maintained tests: tests/test_lyric_review.py (10 cases) plus existing provider,
publication and API tests: 43 passed. Existing 10 AI songs verified reviewable.
Actual JS/CSS and dialog rendered in installed headless Chrome on Cyborg (RAM
profile, external temporary fixture/cache). Desktop/narrow-screen dark UI inspected;
18 browser assertions cover filter persistence, edit-confirm, save/reload,
playback invocation, disabled saves, fitting viewport and visible Save footer.
Song picker dark contrast was fixed after visual inspection. No Mac browser or
model installed. No backup retained; remove task/browser/build staging after final
live save verification. Original queue diagnostics are active source data, not
cleanup candidates. Never cancel ASR jobs to temporarily hold them during this work.

Live verification: web and playlist-worker run image34c421e78e5a (latest). Authenticated
GET Music sync200, song choices available, Dorin15 uncertain lines, ranged audio206.
A POST of unchanged current lines was published by the existing playlist worker,
then returned Saved. LRC bytes and source audio SHA256 exactly unchanged; zero
human confirmations were introduced. Other services were not recreated; shared
Cyborg Server tasks and ASR jobs were not cancelled/reprioritized. Temporary build,
preview, RAM browser profile and Mac staging are removed after verification; no
backup or recurring cleanup was added.

### Safari lyric-review audio correction (2026-10-17)

The review player failed in Safari because Python's MIME guess for .opus was
`audio/opus` (RTP payload), while managed .opus files are Ogg containers. Explicitly
serve .opus/.ogg/.oga as audio/ogg in routes/music.py; other types retain their
normal detection, auth, no-store and conditional/range behavior. No transcoding,
new service, model, audio conversion/cache or browser installation added. Focused
API regression checks MIME plus206/range/auth/CSRF. Web only redeployed with image
df79bcd16ce2; playlist worker and ASR jobs left running.

Actual WebKit on this Mac reproduced failure: identical five-second Opus sample
with old audio/opus returned MediaError4/canPlayType empty. Corrected audio/ogg
returned canPlayType probably, duration4.96s, decoded and played past1.25s. Used a
temporary Swift/WebKit ephemeral data store and localhost fixture; no Safari UI
permissions/settings modified. Test process/window/server stopped and sample,
module cache and scripts removed after successful live checks. Source music and
lyrics unchanged. Prior range-only/fake-browser playback checks had not proven
Safari decoding; use an actual media engine for future format changes.

The user's simultaneous Dorin screenshot was client-cached old credit-only lyrics;
server getLyricsBySongId returned35 synced lines with Cybrog byline and legacy
getLyrics also returned the current text. User confirmed clearing client cache
resolved it. No additional lyric regeneration, music/tag rewrite, DB mutation,
Navidrome restart or manual scan was needed.

MIME reference: https://www.rfc-editor.org/rfc/rfc7845.html
Safari Ogg support: https://webkit.org/blog/16574/webkit-features-in-safari-18-4/
