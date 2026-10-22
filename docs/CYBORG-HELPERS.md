# Cyborg CPU helpers

Cyborg runs one private Docker helper for image OCR, missing-text PDF page OCR,
semantic embeddings and vector ranking, and Quartz 5 publishing. The web/database,
LiveSync, original files, authoritative search text and keyword search remain on the
main server. Existing native Cyborg AI, transcription, fan, GPU guard and speaker
controls are retained. No Mac model, index, Docker daemon or background helper was added.

## Controls

- **NTU VPN → Server tasks** is the shared admission switch. Off stops active helper
  work and the existing native AI/transcription processing. Pending work/checkpoints
  remain. On permits recovery through the existing dispatcher, normally within a minute.
- **Tamestorage → Admin center → Services → Cyborg**: Start, Stop, Restart, Details.
  Stop is persistent; the admission switch and pending-job recovery respect it. Start
  and Restart require Server tasks On. Unknown/offline states are distinct from Stopped.
- Existing Uptime Kuma adds Docker control, CPU helpers on demand and external service
  storage. Intentional Off/manual stop is standby availability, not a running-job claim.
- SSH: `sudo -n /usr/local/libexec/cyborg-services status`, `detail helper`,
  `start helper`, `stop helper`, `restart helper`. `deploy` rebuilds the maintained
  image manually. Native model services retain their original manual-start preferences.

## External storage

External UUID `A87A-15B6`, mounted at `/run/media/tameszaza/New Volume`.
`Cyborg Services/services.ext4` is a 48 GiB ext4 filesystem file mounted at
`/srv/cyborg-services`. The drive was not reformatted. Existing Docker/containerd data
was copied with hardlinks/xattrs, checksum-verified and inventoried before deleting
its internal copies. About 38 GiB was freed internally; existing project containers,
volumes, games and model data were preserved.

Docker data-root is `/srv/cyborg-services/docker`; containerd root is separately
`/srv/cyborg-services/containerd`. `cyborg-services-storage.service` and engine
ExecStartPre validate the outer UUID, Linux filesystem and loop backing file.
Missing/wrong external storage fails closed, without an internal fallback. Docker
boot autostart remains disabled; a permitted pending request can start it on demand.
Do not rerun the migration to update the application; the installer now refuses a
second migration and directs an existing installation to the deployment command.

Canonical deployment source: external `Cyborg Services/deploy/`.
Root controllers: `/usr/local/libexec/cyborg-services{,-storage}`. No generic Docker
socket or shell is exposed to the web. Main-server control uses its dedicated
restricted forced-command SSH key, pinned host key and source address.

Container: `cyborg-helper`, image `cyborg-helper:1`, private Tailscale address
`100.66.32.22:9020`, bearer-authenticated job endpoints, nonroot UID1000, read-only
root, dropped capabilities, no GPU/CUDA or new large language model. Limits 8 CPUs,
8 GiB RAM, no additional swap; these are ceilings, not reserved RAM. Logs use bounded
5 MB × 2 local Docker logging on the external filesystem. Models, work, cache and
scratch are external. Job scratch abandoned by Off is removed at the next startup;
PDF checkpoints and source cache are retained. Reusable source PDFs are bounded at
2 GiB and evicted only as derived cache, never from the original library.

## Search and pending work

The existing POTION 8M model and SQLite/FTS5/sqlite-vec implementation are reused.
Cyborg's external `work/vectors.sqlite` contains a derived vector-only mirror, not a
second authoritative note library. Initial seeding is one-time. Thereafter SQLite
triggers mark changed documents in `vector_pending` within the authoritative index;
only those document vectors are transferred. Atomic replacement, epoch checks and
result-vector hashes prevent stale row IDs from being mistaken for current evidence.
Interrupted transfers remain pending; a lost mirror is reseeded automatically.
Keyword ranking uses FTS5 native rank/LIMIT rather than joining and sorting the full
chunk table. Original text and citation resolution remain on the main server.

If Cyborg is Off, busy or offline, keyword/native Obsidian search remains available;
semantic lookup waits. No fallback model is loaded on the main server in this case.
Bulk indexing/OCR/publishing preserve prior outputs and defer. The existing
transcription path/dispatcher also wakes pending index/OCR/publishing jobs; no second
queue daemon, Redis instance, recurring Mac poll or extra timer was added. Worker
exit75 means deferred work, not failure. Quartz retries reuse an unchanged export
and check availability before compressing it. The published site switches atomically
only after successful output validation; theme, graph filter and patched Quartz 5
plugins are the existing ones, including their pinned Git metadata.

Private main-server credentials: `~/.config/cyborg-services/` (mode0700; credentials
mode0600). Do not print/copy tokens or private keys into the vault or documentation.
Main-server reader/client: `~/.local/lib/lecture-search/helper_client.py`; maintained
control and integration tests are in `/srv/docker/tamestorage/deploy/cyborg-helper`.
Publisher and transcription dispatcher retain their existing installed paths.

## Verified 2026-10-21

Real embeddings, image OCR and an invisible PDF layer passed; the source PDF hash
was unchanged. Three previously failed large image recognitions succeeded. Real
Quartz site:654 output files,9.76s in the isolated test. Active OCR was interrupted
by Server tasks Off in3.3s; the client reported Paused. On restored only permitted
helpers and left the previously stopped native grammar/model services stopped.
Admin authenticated/CSRF checks and real helper Stop/Start/Restart passed; desktop
and390px UI checks passed12 assertions each. Other main-server container IDs stayed
unchanged. Kuma inventory46 checks; no existing monitor/history/account removed.

The same semantic query was observed at35.35s with main-server vector scanning and
0.30s after moving ranking and using native FTS rank. These are sample observations,
not a controlled cold-cache benchmark or a latency guarantee. A broad folder-scoped
keyword query took8.64s cold; keyword searches can still depend on NAS I/O. The initial
1.2GB authoritative index was not copied wholesale to the Mac or Cyborg: the derived
vector mirror is about200MB. No new corpus-wide OCR or duplicate handwriting model.
OCR can still misread handwriting, code and equations. Existing two PDF raster
inventory failures are separate and were not cleared or claimed repaired here.

Status requests read a completed-work snapshot, with live small queue/image counts;
they do not traverse every derived PDF directory on each request. NAS availability
is still checked first. Snapshot details refresh at completed index/OCR runs and can
lag during a run. The private HTTPS9443 search route remains configured: a status
timeout was not proof that its port was unreachable. Final native client requests
reached it successfully; the443 alias also remains. No LanguageTool/chat credential,
history, role, endpoint preference or manual AI setting was changed.

Busy semantic requests do not trigger reindexing. Offline requests enqueue only a
wake; a missing/dirty mirror enqueues indexing. Two extra busy/offline regressions
passed. Final native Mac HTTPS9443 semantic lookup returned Cyborg results in3.85s
and cached status200 in0.50s. Warm helper sample105.2MiB/0.03%CPU excludes engine
processes and is not a worst-case measurement.

### Server tasks startup (2026-10-22)

Explicit On now starts the AI API/grammar and model runtime as well as permitted CPU helpers, even after a reboot or manual AI stop. Off stops server processing. Boot autostart remains disabled; models load on demand. A cold HDD model load may take over a minute.

## OCR event watcher recovery (2026-10-22)

The systemd start limiter counts successful event starts, not only failures. The previous three-start/600-second limit disabled OCR after ordinary updates. Installed `lecture-search-ocr.service.d/40-success-counter.conf`: a fixed privileged ExecStopPost resets only this service's start counter when SERVICE_RESULT=success (including existing SuccessExitStatus=75 deferred standby). Failure exits do not reset it; the original three-start/600-second circuit breaker and60-second failure restart delay remain. Existing PathExists marker, one-shot worker,256MB/one-core bounds, Cyborg admission switch and durable queues/caches are unchanged. No new resident process, timer or retry queue. A short systemctl invocation runs only after completion.

Maintained source/drop-in installer/native regression in ~/.local/lib/lecture-search and /srv/docker/tamestorage/deploy/cyborg-helper. Opt-in regression: `python3 ~/.local/lib/lecture-search/test_ocr_result.py`. Tests eight successful starts, eight accepted75 pauses and exactly three actual failure executions with a fourth blocked, using disposable user-manager path/service units. Wait for stop-post completion; use execution count rather than Result alone (systemd255 user manager retained exit-code after rejecting another start). Installer requires root, validates exact drop-in contents, reloads, clears the previous failed states and rearms only OCR path. Actual production backlog processing resumed; final queue verification is recorded below. Preserve the drop-in across unit reinstalls. Do not disable the failure limit or add a periodic watchdog merely to recover this issue.

Production verification: backlog drained to zero live ocr_jobs, pending marker absent, OCR service inactive/success and path active/waiting. Both cleanup and the new privileged success hook exited0. no process remains for idle OCR. No test unit/scratch/backup retained. Existing two book image-inventory failures are separate, unchanged.

## Book image inventory offload (2026-10-22)

The fixed90-second main-server `pdfimages -list` inventory timed out on the594-page Principia and1493-page l8r0NozU book. A bounded diagnostic confirmed Principia exceeded110seconds and the other book needed107.48seconds (with recoverable PDF syntax warnings). Native text extraction and source files remained readable.

`queue_images` now reuses the existing authenticated Cyborg `/jobs/pdf` endpoint and fingerprint-keyed PDF source cache. Poppler still supplies image geometry/inline/nested-form handling; no new PDF parser, recognition model or service. Each call inventories at most64pages with a120-second child deadline, saves a small atomic `.inventory.json` checkpoint beside the external cached PDF, then releases the helper job slot. Source changes invalidate checkpoints; completed scans return cached page numbers. Existing source-cache eviction removes the derived inventory sidecar. Main server streams source uploads, validates progress/page bounds, and publishes successful raster_scans only after full completion and an unchanged source fingerprint. Existing failure policy/thresholds and cached completed OCR are preserved.

Cyborg Off/busy/network/cache eviction defers through the existing dispatcher rather than caching an inventory error. Explicit `retry-errors` now also clears failed raster inventories for a user-requested retry. Existing unchanged inventories do not rescan; only the two known failed rows were explicitly invalidated for this repair. Future missing/changed PDF inventories use the same helper; legacy local Poppler path remains for installations with helper offload disabled, not as an automatic fallback while Cyborg is unavailable.

Maintained `pdf_inventory.py`, worker.py, helper_client.py, search.py, Dockerfile, test_pdf_inventory.py and test_inventory_queue.py in deploy/cyborg-helper; matching runtime/client and external deployment sources updated. Nine focused tests cover interrupted/resumed batches, completed cache, source changes, malformed output, nested-form images via real Poppler, response validation and Paused cache protection. Real helper image OCR/embedding and two-batch inventory/cache checks passed. No Mac PDF/model/index, new idle worker, periodic scan or extra queue. Original PDFs are not rewritten. The existing Server tasks admission/stop policy remains authoritative.

## Book inventory errors repaired (2026-10-22)

Both PDF Library/Books/Isaac-Newton-Principia-English-1846.pdf (594pages) and l8r0NozU.pdf (1493pages) failed the old main-server90sec Poppler image-inventory deadline. Bounded diagnostic first timed out at110sec; second took107.48sec with recoverable syntax warnings. Native text remained readable. Reused existing Cyborg PDF source cache/authenticated /jobs/pdf endpoint and Poppler;64-page/120sec batches with atomic external .inventory.json checkpoints, completed-cache reuse, source-change invalidation, progress validation, existing eviction integration. Main runtime helper_client/search and canonical server/Cyborg deploy sources updated. Off/busy/cache eviction defers through existing dispatcher; no false failed-PDF cache on Paused. Existing explicit retry-errors also invalidates failed raster inventories. No new service/queue/daemon/model/Mac data or idle polling; caps/admission unchanged.

Real book test found stderr/stdout interleaving corrupting batched rows. Worker.run gained an optional stderr stream (default unchanged); inventory uses stderr=DEVNULL separately from machine listing.9focused tests passed (resumption, changed/unchanged source, malformed output, real nested-form PDF, response validation, Paused cache protection). Real existing image OCR/embedding and two-batch endpoint/cache checks passed. Both full inventories now have matching source fingerprints and failed=0; original SHA256 hashes verified unchanged. API imageInventoryFailedFiles=[], ocrFailedPages=0 at verification. The two discovered-image OCR jobs are processing through the existing worker, not complete yet; original native text stays searchable. Preserve their checkpoints/queues.

Cyborg helper updated via maintained Dockerfile plus small source-only correction; bounded local5MBx2 logs,8CPU/8GB,readonly UID1000,external mounts retained, Server tasks On respected; independent native AI/audio/fan controls not touched. Root deploy's existing20sec cold Docker-create timeout failed after build; recreated only helper with same isolation/mounts/logging and120sec create timeout, then existing Start. Future cold deploys may need that longer creation timeout; do not infer a GPU disconnect or reset engines. Tiny synthetic helper source/cache removed after SHA check. Staging/fixtures removed after verification, no backup retained.

## Search ordering, filename semantics and PDF counts (2026-10-22)

Lecture Chat0.3.9 adds server results through the existing native Search renderer: native first, literal text/name second, semantic third; selected sorting retained inside tiers. Literal PDFs say PDF text. Count indexed occurrences across pages and deduplicate overlapping chunk positions; expandable samples open their own page, including keyboard navigation. Up to48 snippets/PDF and40000 response bytes; full count retained with sampled snippets, ≥ for deliberately capped5000matching-chunk scans. Counts refer to indexed extracted/OCR text, not visual verification. Occurrence expansion only for native Search filesOnly requests, protecting chat source budget.

Reuses existing chunks/vector mirror/model: one cached filename vector per supported document (page=-1), preserved on body changes;571 built. Separate small filename candidate pool prevents body passages crowding filenames out. Native paths excluded before bounded candidate limits. Dirty/updating documents excluded from semantic candidates instead of blocking whole corpus (up to100pending paths; bulk migration still defers), hashes/pending state checked before results. Available keyword matches survive busy/offline semantic lookup. No new idle process, timer, observer, Mac model/index or persistent result cache; existing admission/resource limits unchanged. Search hooks restored on cancel/unload/native takeover.

23server regression tests and native Search client tests passed. Real numOfNodes: Recursion Solutions12occurrences/pages9,11–14; Decomposition11/pages18–21. Live Mac verified native rows first, PDF text labels, counts,12expanded snippets and page14 keyboard target. Real algorithm efficiency returned Algorithm Complexity.pdf as semantic filename while book OCR continued. Live plugin0.3.9; private data.json unchanged. Mac customization main/manifest published via existing LiveSync; physical iPad application still user-side. Preserve OCR queues/checkpoints; no need to wait for separate book OCR backfill. Completed-task staging removed; temporary Obsidian remote debugging closed after verification.
