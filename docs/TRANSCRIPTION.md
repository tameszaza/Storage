# Tamestorage transcription

Installed 2026-10-16. Open the existing **Admin → Transcribe a recording** link,
or https://server.tailb7f57c.ts.net/admin/transcription while connected to Tailscale.
Sign in as Admin. Select an audio/video file, optionally enter a title and spelling
hints, and upload. English is the default; automatic language detection is optional.
Local AI comparison is on by default; estimated speaker labels are optional.
The limit is 5 GB and 24 hours per recording. Leave the page after upload completes;
jobs continue independently. Refresh the page to retrieve the full plain-text result.

## Files and retention

- Pending server audio: `/srv/tamestorage/uploads/temp/Transcription/<job-id>/`.
  This exact inbox and Goodnotes-cleanup are excluded from the existing 24-hour
  blanket temp expiry. The existing six-hour backup/cleanup worker removes only
  eligible completed recordings at least24h after completion and last filesystem
  change (normally24–30h). Pending/retryable inputs and inbox stay protected.
- Finished primary data: `/srv/tamestorage/uploads/Admin/Transcripts/<title> - <job-id>/`.
  `Transcript.txt` contains the complete selected speech, timestamps and optional
  speaker labels. `Review.txt` contains competing readings. No automatic expiry.
- Private queue, bearer key and detailed processing evidence:
  `/srv/tamestorage/.tamestorage_state/transcription/` (NAS, not the system disk).
  The Admin download endpoint also supports `download/details` for the compressed
  processing report. Reports include all recognition passes and should be treated
  as private recording data.
- Cyborg models, isolated Python packages, caches and job checkpoints:
  `/run/media/tameszaza/New Volume/Cyborg Transcription/`.
  Models/packages remain installed for future requests. Source audio and scratch
  are removed only after the server confirms all three result checksums and the
  completed job state. Completed server audio follows the existing24h cleanup
  worker, with matching checksum and published-results checks. Failed work retains
  its recording/checkpoints for Retry.
- Cancel stops the current job through its next heartbeat. Retry uses saved
  checkpoints where available. Remove entry deletes that job's temporary input
  and private history, but preserves finished plaintext in Admin/Transcripts.
  Cyborg prunes cancelled/removed/completed scratch at the next worker invocation;
  no recurring cleanup task was added. Manage saved plaintext through the existing
  Admin file browser. Downloading does not delete results.

## Recognition and comparison

Five passes use three separately trained model weights: Whisper large-v3 on the
original and dynamically normalized audio, Whisper large-v2 on the original, and
Parakeet TDT 0.6B v3 on original and normalized audio. Whisper uses CUDA FP16;
Parakeet uses FP32 ONNX on the CPU with 12 logical threads. Parakeet is used only
for its 25 supported European languages; other detected languages use the Whisper
passes. Models unload between stages. Decoding checkpoints every 30 seconds;
Whisper receives two seconds of context on each side, with word-midpoint ownership.
Both variants cover the full decoded input; no VAD removes quiet speech. Input
coverage is verified in the processing report. Processing every interval does not
establish that every spoken word was recognized correctly.

Disagreements are aligned into spans. Multiple variants of one model share one
model-family vote. If enabled, the existing Qwen3.5 9B Q4_K_M model chooses only
indices into supplied recognized alternatives under a strict JSON schema. Stable
words are mechanically retained; the LLM cannot introduce free-form wording or a
summary. Invalid/unavailable AI output falls back to the model vote and is recorded
in the job/report. Nontrivial disagreements remain `[uncertain: ...]` even after
AI selection. `[unclear]` records an unresolved empty span. Review.txt retains
alternatives. Models can agree on an error; this is not a guarantee of accuracy.

Optional speakers use FP32 Pyannote segmentation with TitaNet Large, cross-checked
against CAM++ (six CPU threads each). Generic Speaker numbers represent estimated
voice clusters, not identities; unclear and disputed assignments are marked.
Short, distant and overlapping voices remain difficult.

## On-demand operation and access

The existing Tailscale HTTPS endpoint and Flask login are reused. Upload, history
and downloads require logged-in Admin; mutations also require session CSRF tokens.
Audio streams directly to NAS, avoiding a full multipart copy in the web /tmp.
The worker API has a separate narrow bearer credential, never exposed to browsers.
There is no new public listener, public sharing route or cloud transcription API.

`tamestorage-transcription.path` watches the NAS queue wake marker. When work exists,
`tamestorage-transcription.service` invokes the maintained server dispatcher:
`/home/tameszaza/.local/lib/tamestorage-transcription/dispatch.py`.
Its dedicated SSH key in `~/.config/tamestorage-transcription/` is host-key pinned.
On Cyborg that key is restricted by source address, `restrict`, and a forced
`~/.local/bin/cyborg-transcription-run` command. It cannot obtain a general shell.
The dispatcher exits when the queue is empty; when Cyborg is offline it retries
only while jobs remain pending. Leases expire after 120 seconds and heartbeat
every eight seconds; interruption retains checkpoints for recovery.

Cyborg verifies external UUID A87A-15B6 before work. The server verifies primary NAS
UUID 4dcef454-5a20-484b-b00e-fe9a136f82b9. Missing mounts must fail, never create
replacement data directories on internal storage. The main web container retains
read-only root, uid1000, existing bind mounts and loopback-only port5000.

No transcription model or worker remains idle. Each LLM phase starts a temporary
loopback Ollama process on127.0.0.1:11436, reusing the existing external Cyborg
Grammar binary/weights; it exits with the job phase. Manual grammar services and
their autostart preference are unchanged. Simultaneous desktop/grammar GPU use
can delay a job: Whisper/LLM wait for at least5500MiB free. Only the transcription
job's child process groups pause at83°C and resume at77°C; unrelated processes are
never paused. This bounds this worker's GPU contribution, not heat generated by
other applications. CPU uses up to12logical threads with nice5; no CPU temperature
cap was added. No persistent GPU power setting was changed.

## Maintenance

App source: `/srv/docker/tamestorage/{routes/transcription.py,lib/transcription_store.py,
templates/transcription.html,static/js/transcription.js}` plus the Admin link,
route registration and file-inventory invalidation in the existing app.
Queue tests: `tests/test_transcription.py` (nine tests). Reconciliation source/tests
and pinned model/package manifests are retained with Cyborg runtime. `requirements.lock`,
`package-report.json` and `models/manifest.json` identify the installed artifacts.

Use `systemctl status tamestorage-transcription.path tamestorage-transcription.service`
and `journalctl -u tamestorage-transcription.service` on the server. Only inspect
individual Cyborg job `<phase>.log` files when needed; they can contain private
speech. Never print bearer keys, SSH private keys, passwords or full reports into
general logs. The dispatcher intentionally suppresses SSH output.

To pause new dispatch, authenticate sudo and stop the .path then .service. Existing
SSH model children can take a heartbeat interval to notice a cancelled job; use
the portal Cancel action first. Re-enable the path with `systemctl enable --now
tamestorage-transcription.path` after maintenance. No recurring poller or cleanup
timer is needed. The maintained `install-host.sh` installs these units and narrowly
patches the temp cleaner; it requires explicit administrator authentication and
checks the pre-edit cleaner hash. It contains no credentials.

Only recreate the existing web Compose service after checking both NAS mounts:
`docker compose -f /srv/docker/tamestorage/compose.yaml up -d --no-deps --no-build web`.
Do not recreate workers or other services merely to update this portal. Follow
the main server runbook for building future app images and disk recovery.

Model references: https://github.com/SYSTRAN/faster-whisper,
https://k2-fsa.github.io/sherpa/onnx/index.html,
https://github.com/k2-fsa/sherpa-onnx,
https://docs.ollama.com/api/structured-outputs .


## Transcription minimal UI and automatic inbox (2026-10-16)

Lasting user preference: minimal visible copy, reuse the site's existing design, always render and visually inspect UI changes. Rebuilt /admin/transcription using existing tokens, compact drop area, collapsed Options, short status rows/actions and Inbox/Results links. Default English, AI cross-check On, speaker labels On (user-selected screenshot). Actual deployed Chrome desktop dark/light, options and390px mobile renders:24 visible main-area words with one real job, no horizontal overflow or console errors; visually inspected desktop/mobile. Nine queue/API tests and four inbox recovery tests passed.

Direct finished media uploads to /srv/tamestorage/uploads/temp/Transcription queue automatically with en/llm=true/speakers=true, filename title and no vocabulary hints. Existing root Goodnotes inotify watcher reuses its idle event loop through /usr/local/lib/goodnotes-watch/transcription_events.py; audio close-write/move/startup/overflow events touch private audio-wake without PDF work. New enabled tamestorage-transcription-inbox.path starts a bounded oneshot service (128MB/50%CPU/idle IO), no extra idle Python watcher. Maintained host inbox.py/tests/unit templates in ~/.local/lib/tamestorage-transcription. Only direct media files; ignores hidden/partial/symlinks and managed UUID subfolders. Stable checks before/after atomic move (same filesystem, no audio duplication), fingerprint/SHA validation and persistent recovery intent before move; growing writers and interruption recover. Minimal private inbox-pending.json and per-job inbox.json are intentional recovery data. Existing protected temp cleanup exclusions and result retention unchanged. Actual .wav.part→rename→root filesystem event→queue passed, partial ignored, SHA/defaults verified, zero duplicates after repeated signal; fixture removed, ordinary dispatcher path restored. No GPU inference added during active gaming.


## Shared Cyborg controls on Mac and Cyborg (2026-10-16)

User requested independent GPU safeguard and server-task switches inside the existing /Applications/NTU VPN.app, plus Server tasks inside Cyborg GPU Guard. Implemented native NSSwitch rows in existing Mac window, confirmed state on open/reopen and manual refresh, bounded key-auth SSH25s/shared busy lock, no idle polling or credentials/new privilege. Mac source/build/README stay inside bundle. Existing VPN/SAML session, fan/night controls and manual startup preserved. Guard uses existing exact protected sudo controller; toggle does not change its boot setting. Live after user reboot guard was already On with autostart enabled; preserved this live setting (supersedes earlier installation-time disabled observation).

Cyborg shared ~/.local/bin/cyborg-server-tasks on|off|status|desktop-status uses ~/.local/lib/cyborg-server-tasks/admission.py. Persistent private0600 ~/.config/cyborg-server-tasks/state.json; atomic writes, malformed state fails closed, pre-install missing state preserves accepting behavior. No new daemon/socket/startup/sudoers permission. All POST processing on existing Cyborg FastAPI9019 (grammar /v2/check and LLM /v1/assist) checks admission before body/model work, returning503+Retry-After60 while Off; health stays available. Worker checks before each /claim and exits75 while Off, so main server keeps new jobs queued and existing event dispatcher retries only while pending. Already admitted requests/jobs finish normally. On permits new work but does not start manually disabled grammar/model services or retry failed recordings. Normal main-server native LanguageTool fallback remains available. Future Cyborg server-processing integrations must apply this shared check.

Native GTK Server tasks switch uses same preference and refreshes with existing visible-window polling. Mac switches are disabled until remote state is confirmed; refresh after changes elsewhere. Verified state/fail-closed/atomic persistence, no worker claim while Off, live adapter health and503 for both AI routes without loading models; actual admission Off/read/restore kept safeguard On. Swift build, prior SAML/fan/night parsing plus strict JSON switch parsing, deep signature verification passed. Native Mac and GTK layout previews rendered; website preview independently verified in actual Chrome. Final admission On; grammar/model units remain Off as found.

Reboot diagnosis: previous boot2026-10-16 21:58:01 logged NVIDIA Xid79 GPU fallen off the bus, Xid154 reboot required and PCIe transaction/AER recovery failure. Guard transitioned hot at21:57:59 and later reported lost telemetry. Logs establish device/PCIe loss, not a definitive heat/hardware/driver cause. GPU recovered after user reboot. Do not claim the clock guard guarantees85°C or eliminates GPU bus failures.

CS2 shader pass completed21:55:42 after normal Steam shutdown/relaunch with session-only FOSSILIZE_DISABLE_RATE_LIMITER=1; roughly2minutes vs prior2%/30minutes. Verified11 active workers and saved unShaderBackgroundProcessingThreads12 in steam_dev.cfg. Closing just CS2 retains session env; restarting Steam/reboot removes limiter override. Shader caches retained deliberately on external SteamLibrary. No persistent global limiter override, priority helper or diagnostic socket left behind.


## Server tasks Off now interrupts active work (2026-10-16)

User correction: while gaming, Server tasks Off must stop active processing and release GPU memory as well as block new tasks. This supersedes all earlier descriptions allowing admitted jobs to finish. The observed worker started22:14:54, preference switchedOff22:15:00, and original admission-only implementation kept Whisper large-v2 running. Stopped only identified worker7647/infer8148 groups, preserving all recording/checkpoints. No game or desktop process was stopped.

Shared admission.py Off now persists false first, stops the two known user grammar/model units (cancelling active AI requests and unloading resident models), signals exact transcription worker/infer groups and job-only Ollama/runner processes identified by protected runtime path plus private11436 environment tag, waits for exit and escalates only those known tasks if needed. It reports an error if a process remains stuck; it does not falsely confirm release. No broad pkill/GPU reset/root grant. Concurrent control changes serialized by a private flock, no resident controller. On restores only grammar that Off had found running; explicit manual cyborg-grammar off clears its restoration marker. Existing unit autostart preferences remain untouched. On otherwise allows checkpoint resume; failed jobs still use Retry.

Worker checks Off before processing stages and every running/waiting loop (~1second), stops its model process groups in finally, and returns the current job to queued through new bearer+lease-protected POST /api/transcription/jobs/<id>/pause. Pause invalidates the old lease, preserves recording/artifacts/checkpoints/progress and uses Server tasks off or Waiting for GPU recovery. GPU query errors are now resumable pause conditions instead of terminal stage failure. Existing dispatcher retries only while pending; actual forced restricted server worker call while Off returned75 and did not claim/change the queued recording. Small SSH admission checks may still occur while queue pending, but no model or processing stage starts. UI queued/off shows Paused; GPU recovery shows Waiting for GPU. Mac/native GTK tooltips updated; GTK control timeout increased to22s for graceful shutdown. Mac build/parser checks and strict deep signature passed.

Validation:10 queue/API tests passed including authenticated pause, wrong lease denial, stale worker rejection, source/progress preservation and resumed lease. Three worker behavior tests passed using a disposable real child: Off terminates it without deleting saved checkpoint; GPU loss raises resumable condition; main pauses rather than failing. Prior three state/AI admission checks passed after fixture updated for signal handler. Actual Off CLI killed a disposable sleep-only group (no model/GPU workload), and actual server forced worker invocation while Off returned75 without assignment. Live nvidia-smi shows CS2 only, no transcription or LLM compute process; grammar/model units Off. User explicitly said gaming: leave Server tasks OFF until user enables it. Existing recording9e983d6a30e14111a5e2fc75035360fa queued/Server tasks off, primary audio and v3 checkpoints retained. Do not resume this job while Off.

GPU diagnosis remains unresolved: both21:58:01 and22:10:12 previous boots logged Xid79 fallen off PCIe bus plus reboot-required recovery. First boot guard entered hot2s earlier; second boot had no hot transition in inspected guard journal before device loss, so do not attribute every failure to a cap change. User reports failure after gaming and much sooner without fan boost, increasing suspicion of heat/cooling or power stability but not proving root cause. Current fan boost alreadyOn6956RPM, keptOn. Sample GPU65C/17.07W while CPU package95C and ACPI97C (simultaneous chassis heat; no CPU setting changed). Guard user setting now84C trigger, bootOn, preserved. No driver/BIOS/PCIe/voltage/reset/reboot/CPU limit change or stress test. NVIDIA reference https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html describes79 as GPU inaccessible over PCI Express. Guard cannot guarantee prevention of these failures.


## Automatic missing song lyrics (2026-10-17)

Existing playlist sync now feeds missing lyrics to the same durable queue after provider lookup. Thai text in any metadata excludes tracks. Cyborg reuses Whisper word timestamps to produce sidecar LRC, with cross-pass disagreement rejection. Offline/Server tasks Off preserve queued work; no new idle service or Mac footprint. Existing lyrics and source songs are preserved. Full implementation, constraints, initial validation and maintenance: `/srv/docker/tamestorage/docs/LYRICS.md` on the main server.


## Public-first lyric quality / instrumental results (2026-10-17)

Credit-only/empty/provider-instrumental notices are rejected as lyrics. Public
providers are tried before AI. Artist identity is checked to avoid same-title
wrong-recording results. Existing recognition renders either attributed LRC or a
normal Likely instrumental result; genuine runtime failures remain errors. No new
model/pass/service. See main server /srv/docker/tamestorage/docs/LYRICS.md.

## Recording audio retention uses existing temp cleanup (2026-10-18)

User requested original uploaded transcription audio to follow the existing folder cleanup worker. Normal recording completion now retains the verified input in uploads/temp/Transcription/<job-id>/ instead of immediately unlinking it. Existing root /usr/local/sbin/tamestorage-temp-cleanup calls root-owned /usr/local/libexec/tamestorage-transcription-cleanup.py after ordinary temp expiry. Same existing six-hour backup/cleanup invocation, no new timer, daemon, polling or model. Eligible audio is at least24h after completion and its filesystem ctime; it is removed on the next existing cleanup run (normally24–30h after completion). The persistent inbox itself remains excluded from blanket expiry.

Only completed recording jobs with both saved plaintext results present are eligible. Queued, running, uploading, failed and cancelled jobs stay available for processing/Retry; music/lyrics job inputs remain owned by the existing lyrics workflow. Removes only the expected Recording.<extension> matching its stored size/SHA256, verifies unchanged inode/timestamps across hashing, uses directory-relative O_NOFOLLOW/nonblocking descriptors to avoid symlink/FIFO traversal/hangs. Modified/unrecognized source or unavailable/malformed storage/queue is preserved. Unrelated files and published Transcript.txt/Review.txt remain. Hashing occurs once when an old file is eligible, within the existing idle-I/O cleanup worker. Manual Remove entry keeps its prior explicit source/history cleanup and preserves published outputs. Previously deleted audio cannot be restored by this change.

Maintained source deploy/tamestorage-transcription-cleanup.py and deploy/tamestorage-temp-cleanup; normal completion in lib/transcription_store.py. No UI change.18 focused queue/API and retention tests passed, including age/success gating, pending/failed/cancelled, changed audio, missing results, symlinks/FIFOs, unrelated files, lyrics exclusion and unavailable storage. Candidate built from existing web image with only changed application files. Root installation validates the previous script hash and syntax before atomic root-owned replacement; no global NAS cleanup or transcription inference was run for testing.
