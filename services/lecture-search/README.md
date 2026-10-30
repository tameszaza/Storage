# Server lecture semantic search

Private endpoint: `https://server.tailb7f57c.ts.net:9443/semantic-search`.
This shares the existing Tailscale-only Quartz/Caddy route. No Funnel/public port.
Tailnet users who can read the private vault site can also search its indexed text.

## Resource design

User priority: Mac RAM > server RAM ≈ Mac CPU ≈ server CPU > Mac storage > server storage.
No embedding model, index, OCR/extraction runtime or cache is installed on the Mac.
Lecture Chat sends explicit requests and receives bounded snippets/page text. It
reads server PDF text before falling back to the native Mac PDF viewer; a visual
page is rendered by server Poppler on demand, avoiding full-PDF loading on the Mac. Nothing runs while typing.

Published Model2Vec `minishlab/potion-base-8M`, English, 256-dimensional embeddings,
with its native base Python package. Model revision
`bf8b056651a2c21b8d2565580b8569da283cab23` is pinned in code. SQLite + sqlite-vec exact cosine search and
FTS5 keyword search are combined by standard reciprocal-rank fusion. No custom
embedding model or PDF geometry reconstruction. This small static model favors
CPU/RAM over transformer retrieval quality; test results guide further changes.
Mathematical/spatial relationships still need source/page-image verification.

Embedding model, venv, vectors, source page text and locks are on the external NAS:
`/srv/tamestorage/.tamestorage_state/lecture-search` (0700).
Small maintained code: `/home/tameszaza/.local/lib/lecture-search`.
`requirements.lock` pins the installed runtime. No global Python package changes.

- systemd `lecture-search.socket` is the only idle listener: no Python/model worker
  remains resident. Loopback8095 only, Accept=yes, max2 connections.
- `lecture-search@.service` handles one HTTP connection then exits, CPU75%, RAM256M,
  max90s. The Caddy proxy requests Connection:close. Search/read responses are not cached.
- Model lock permits only one embedding process across search and indexing. Search
  returns503/busy during indexing or another search; direct file reading is the fallback.
- Index service is a one-shot, CPU75%, RAM256M, Nice15/idle I/O. SQLite page cache2MB,
  temp sorting on disk, mmap disabled; vectors are never loaded into a Python array
  for corpus search. Small source batches, one extraction at a time. BLAS thread1,
  tokenizer parallelism/multiprocessing disabled. File cache is included in the cap.
- No scheduled full rebuild, additional polling loop, query prefetch or idle model.

## Sources and update events

PDFs: external NAS `uploads/Goodnotes`, indexed under matching `PDF Library/` paths.
Markdown: original note bodies from the existing private Quartz export manifest,
which uses the upstream LiveSync client. Source notes are not rewritten. Annotation
JSON is reduced to its recorded text/note fields for retrieval and labeled personal
annotation; it is secondary evidence, never an original PDF statement.

Existing Goodnotes inotify/fanotify watcher touches `pdf-changes` after settled PDF
events. `lecture-search-index.path` also watches Quartz's atomic `export.json` updates.
The one-shot checks inode/size/mtime for PDFs and original-text SHA256 for Markdown;
unchanged sources skip extraction/embedding. It repeats if inputs changed during work.
Deleted sources remove their derived DB rows. Original PDFs, notes, annotations,
provenance and sync DB remain untouched. NAS loss preserves the old index; requests
fail rather than silently writing an index on the internal mountpoint.

Poppler `pdftotext -layout` provides native page text. Missing-text pages are queued
for server-only Tesseract5 English OCR with the published Poppler renderer. One page
at a time, longest side2400px, one CPU thread, no original PDF rewrite/OCR layer.
No diagram/handwriting embedding model is installed. OCR can misread handwriting,
words and equations; extraction is labeled in search hits and page context.
The tutor should inspect the original page image for exact OCR wording. Page numbers are PDF file pages.
Exact contiguous source snippets have small overlaps; no rewritten lecture wording.
Temporary extraction files are on NAS and removed on success/error/stop (unit
ExecStopPost also clears only `extract-*.txt`). No retained setup backups.

## OCR build versus normal operation

`lecture-search-ocr.path` activates its one-shot when the index queues missing-text
pages. No polling. Worker exits when the queue finishes. Completed OCR text and
page embedding flags persist in SQLite: interruption resumes pending pages without
repeating completed OCR. Unchanged PDFs are not OCRed during queries or note edits.
Changed PDFs generate fresh page state; no-text/failed OCR is also remembered.
This guarantees cache reuse for an unchanged fingerprint, not perfect recognition.

The small OCR parent runs Poppler/Tesseract sequentially with no resident embedding
model; a short-lived embedding child acquires the shared model lock, updates one
page, then exits. Queries can use the native index while OCR backfill runs. Book
backfill yields between pages if lecture/non-book jobs arrive. OCR is CPU100% (one
core maximum), RAM256M, Nice15/idle I/O; no daemon remains afterward. Failed workers
have up to3 automatic retries in10min,60s apart, with progress kept. Individual OCR
page errors are recorded rather than repeatedly attempted on unrelated events.
Temporary images are NAS-only and removed on completion/error/stop.

A dedicated page-method index keeps status/OCR count queries from scanning all
stored text. Request workers disallow swap; initial building may use swap under
its memory cap. There is no Mac OCR/index/cache or extra dependency.

## Requests

- `POST /search`: `{query, folder?, limit?}`. Max1500 query chars, 1–8 hits; file,
  page, exact excerpt, source type and index time. Folder is a vault-relative prefix.
- `POST /read`: `{path, pages:[1,2], maxChars?}`. Up to12 indexed PDF pages and24k
  source chars. `{path, metadataOnly:true}` returns totalPages without text.
- `POST /image`: `{path,page}` returns original page metadata and standard MCP JPEG image blocks. Published Poppler, max1500px side/~1.5MP, quality82/max750kB JPEG. Source resolved under Goodnotes and fingerprint checked. NAS transient files removed; no model load. Mac library image requests prefer this route; local fallback requires an already loaded library PDF.
- `GET /status`: counts, no-text count, last-update timestamp and model.

Failed extraction fingerprints are recorded rather than repeatedly retried on unrelated
events. Changing the file retries it; for an explicit retry, touch NAS `retry-errors`
then start the index service. `/status` lists failed files.

Changed/deleted PDF fingerprints are rejected until refreshed, both in search hits
and page reads. Mac tool filters search results to visible files in its current
vault and adds native Obsidian citations. Recent device-only edits may precede the
server/Quartz export; direct Mac reads remain the source for such note edits.
Returned text is untrusted source data, not executable instructions.

## Maintenance

```sh
systemctl status lecture-search.socket lecture-search-index.path lecture-search-index lecture-search-ocr
journalctl -u lecture-search-index -n 20 --no-pager
sudo systemctl start lecture-search-index.service  # incremental reconciliation
sudo systemctl stop lecture-search-index.service   # stop initial/active indexing
sudo systemctl disable --now lecture-search.socket lecture-search-index.path lecture-search-ocr.path
sudo systemctl stop lecture-search-ocr  # checkpoints retained
```

`search.py status` uses the NAS venv. SearchServer blank in Lecture Chat disables
server integration without changing the existing direct read tools. For restoration,
create NAS venv, pip install --no-cache-dir -r requirements.lock, then `search.py prepare`
with HF_HOME on NAS; this is the only model-download operation. Runtime loads the local
model offline. Changing the model requires a deliberate rebuild of this derived DB.
Never delete original notes/PDFs or initialise LiveSync to rebuild retrieval state.

To remove this feature, disable units, remove the Caddy /semantic-search handler
and the optional marker-touch lines in goodnotes-watch, then remove only this code
folder/NAS data folder. Preserve the independent Goodnotes and Quartz services.

## Verified, 2026-10-13 Singapore time

- Native corpus:457real documents after diagnostic cleanup, over125,000passages;
  exact counts grow as OCR finishes. All native extraction succeeded.
- Semantic queries found CC0003 political systems page13 and AB1201 TVM/compounding
  material from paraphrased queries. Actual native Codex Luna called semantic_search,
  server PDF read, then read_page_image. Correct Limited/Illiberal column distinction
  and source links; this does not prove every model paraphrase is exact.
- Global query across125kpassages measured1.57s wall,1.19s user CPU,0.35s system CPU,
  maxRSS128604KiB (~126MiB),0swaps. Measurements depend on cache/other build work.
  Request workers exited after HTTP calls; no request service remained running.
- Mac server-text read returned requested page without calling local PDF loader or
  adding PDF tabs. No model/index/runtime package was installed on Mac.
- Image-only two-line capacitor fixture had no native text, gained labeled OCR text
  through upload-change events, and became semantically searchable. Original PDFs
  were not rewritten by OCR. Test file/pointer/derived rows/provenance removed.
- Unchanged reconciliation reported updated0/removed0/failed0; no embedding load.
  Initial OCR backfill has ~3300pages and continues independently. It is a build,
  not a permanent idle process. Status/journal show progress/errors.
- Old BGE/FastEmbed/ONNX runtime, unused dependencies and initial derived DB were
  removed when selecting lighter POTION. Download/install staging/caches removed;
  only required current model/runtime/derived index retained.

Tesseract system binaries/English language data occupy ~22MB on the server system disk; the embedding runtime/model, index and OCR text cache are on NAS. No Mac dependencies were installed.

## Mixed PDF page coverage and exact phrase fast path

Poppler pdfimages -list inventories substantial raster images once per unchanged
PDF/policy (raster_scans). Images with both sides at least100px and area at least
100000px queue native text pages for supplemental OCR; tiny images are skipped.
Already completed blank-page OCR remains cached. Mixed pages use published
Tesseract sparse-text mode11 and a3600px longest side, preserving exact native
text and appending a labeled machine-OCR section. Existing blank pages retain
2400px/mode3. Methods native+ocr/native+ocr-empty/native+ocr-failed are reported
in page context; status includes imageInventoryFailedFiles. Failed inventory
fingerprints are remembered, renewed when source changes. Inventory is not run
on searches or repeatedly on unchanged note updates.

`search.py ocr-file 'PDF Library/NTU/Y1SEM1/Classes.pdf'` inventories/processes one
indexed PDF and reuses completed OCR; transient NAS images are removed. An
unchanged Classes recheck queued0; page1 now contains DAMDUAN THANAKRIT. Its
4mixed pages took ~50seconds/~140MiB peak under256MiB/one-core caps.

Exact multiword source phrases use existing SQLite FTS5 before model loading or
the model lock. If none are found, existing POTION cosine/FTS hybrid runs. Results
share source/fingerprint validation. This avoids model work for exact names and
phrases even during backfill. Semantic ranking is not promised for exact-phrase
requests. No extra vector projection/index retained; SQLITE_TMPDIR points to NAS.

## Reuse Goodnotes handwriting text

Prefer the recognized text already present in Goodnotes' exported PDF. No additional
handwriting model/runtime is installed. Inspect source pages and real searches
before proposing a second recognition pipeline. Keep existing cached image OCR
for confirmed missing image text.

FTS5 exact phrase results now include supplemental all-query-word matches in the
same passage. Up to4exact hits precede conjunction hits when both exist; total8,
file/page dedup and source fingerprint validation unchanged. This improves
scattered-line/misrecognized phrase retrieval without changing source facts.
POTION/hybrid fallback remains when keyword search returns no source.

Visually checked/live-searched: Classes.pdf p5 (group project part2, short journal
note), AB1201/Note.pdf p1 (uneven cash flow), SC1005 Introduction to Verilog p49
(Synthesis warnings). Those queries completed in0.5–0.8s; Classes name test2.2s.
The temporary PP-OCRv5/RapidOCR experiment was fully removed after the user rejected
redundancy: no handwriting venv/models/scripts/jobs/tables remain.


## Pasted-image OCR and keyword search (2026-10-13)

- Extended maintained lecture-search/search.py using existing Quartz note-export/PDF-change path triggers, OCR oneshot, Tesseract/ImageMagick, Model2Vec and SQLite. No new packages, model, watcher, polling, recurring cleanup or resident process. Unit limits unchanged. Source images/PDFs/notes are never modified/deleted by the indexer.
- Only canonical tailnet image URLs referenced by current Markdown/annotation note text are recognized: server.tailb7f57c.ts.net or100.124.30.94 /obsidian-images/<64hex>.<raster extension>. Source bytes remain /srv/tamestorage/uploads/Admin/Obsidian/Attachments; no remote arbitrary URL fetching. Local fallback PNGs need the existing manual Server Image Paste cleanup/upload first. Unreferenced uploads are not OCR-scanned.
- SQLite image_ocr stores per-name file fingerprint, OCR policy, text and status; image_refs maps parent note paths, image_notes tracks note/reference signatures. Unchanged note bodies skip reparsing; unchanged image fingerprints reuse text, including empty/error outcomes. Cached unlinked image text is retained for reuse upon relinking. Derived parent-note OCR rows are pruned/rebuilt when references change; originals are preserved.
- English Tesseract OEM1/PSM11 on a bounded first raster frame, max3600px side; existing ImageMagick is bounded to1thread/32MiB memory/64MiB map/256MiB disk. NAS temporary directories are removed automatically. Existing256MiB/one-core OCR oneshot handles jobs sequentially; parent holds no model while recognizing, bounded embedding child exits. Cache lives with existing external-NAS index. No Mac OCR/model/index/cache.
- Parent note OCR is labeled machine recognition, stored as existing pages page=0/method=image-ocr and embedded into existing chunks/FTS. Page0 is an internal note supplement, not PDF page0. Search validates referenced file fingerprints and returns the normal parent-note path/sourceType with textExtraction=image-ocr. Mac Lecture Chat0.3.3 native Search supplements local results, labels Image OCR, focuses snippets on query text and opens the parent note. No fake annotation/block data added to source notes.
- Verified18referenced PNGs:17ready/1no-text,8notes with OCR text,0queued jobs. Initial OCR37.5sCPU, then worker exits. Unchanged reconciliation updated0/removed0/failed0 and queued no further image OCR; subsequent user note changes updated native chunks without repeating recognition. Live search clock with a frequency finds the actual pasted5kHz quiz in SC1005 Sequential Circuits annotation; original image inspected. Tiny Clk_B clock diagram was misrecognized: OCR is imperfect, especially diagrams/handwriting/math. Do not invent corrections or promise exact recognition.
- Existing FTS5 exact/conjunction shortcut now accepts single-word queries too, reusing phrase results for one term. No model load/lock when keyword hits exist; POTION fallback remains for no keyword hit. Direct tests with encoder forbidden passed recursive/Clk_B; live recursive HTTP200~0.57s, replacing prior25s+ timeout. The prior Mac offline message was a client timeout, not proof of an unreachable server. Mac failure labels distinguish timeout/hourglass, connection/cloud-off, and HTTPservice/genericalert; HTTP errors retain status. No retry/polling.
- Final index/OCR units inactive/success, queue0. Historical BrokenPipe request failures are not claimed repaired/erased. Maintained source atomically replaced; staging/downloads/probes removed after verification. Original caches/primary data remain required runtime.


## Searchable text inside PDFs (2026-10-20 Singapore)

Obsidian's existing managed PDF raw links now serve a cached, searchable derivative
when ready. The PDF's own Find and text selection can read the added invisible OCR
layer. Shared single-file downloads also receive this derivative. Original NAS PDFs,
Goodnotes WebDAV files, managed links, cleanup provenance and annotation records are
unchanged. Direct SMB/WebDAV access and ordinary file-browser downloads still read
the original file. An already-open PDF is a snapshot: close/reopen it after processing.

Code `searchable_pdf.py` extends the existing index/OCR one-shots and durable queue.
No new daemon, timer, polling, model or Mac plugin/dependency/storage is installed.
All substantial runtime and cached PDFs remain on NAS in
`/srv/tamestorage/.tamestorage_state/lecture-search/searchable-pdfs/<path-sha256>/`.
`source.json` identifies the exact input fingerprint/policy. `manifest.json` records
ready/no-additional-text/error, added page count and page errors. A ready directory
also contains `document.pdf`. Per-page text-only PDFs/checkpoints exist only during
an unfinished build and are removed after final publication. These are active derived
runtime data, not troubleshooting backups. Deleted-source caches are pruned by the
existing incremental index pass; updated-source caches are replaced in place.

Existing cached text had no word coordinates, so one initial geometry pass is needed
for the prior OCR pages. Academic/other non-book PDFs run before Books. Checkpoints
resume interrupted work; unchanged completed PDFs require no recognition. On future
new/changed PDFs, Tesseract emits text/PDF/hOCR together in the same recognition pass.
Already-native-only PDFs and Goodnotes handwriting remain usable without new OCR.

Mixed pages are recognized unmodified, then OCR words overlapping existing native
word rectangles are removed from hOCR. This keeps native/Goodnotes recognition rather
than replacing it or adding a duplicate copy. Native extraction is preserved in the
search index. Do not reinstate the experimental pre-recognition white masking: it
caused the timetable name to disappear from OCR even though it remained visible.
The published OCRmyPDF17.13.0 hOCR/fpdf2 renderer creates invisible text for mixed
pages; Tesseract's native text-only PDF is used for blank pages. pikepdf10.17.0/QPDF
adds the layer with native page/crop/rotation transformations, without rasterizing
existing source content, converting to PDF/A, deskewing or changing page positions.
Protected/signed PDFs fall back to originals. English OCR can still misrecognize text.

The OCRmyPDF renderer is a short-lived subprocess, releasing its font/parser imports
before embedding or document assembly. Existing worker256MiB/one-core/idle-I/O limits
remain. No model is loaded for layer-only backfill. Dependencies and their exact
versions are in the existing NAS venv/requirements.lock; no system Ghostscript or Mac
codec/OCR runtime was added. Request handling imports only the small stdlib cache
reader `lib/pdf_text_cache.py`, not OCRmyPDF, pikepdf, Pillow, SQLite or an encoder.

The web lookup follows existing share authorization, verifies source/path/fingerprint,
policy/status and current file existence, then uses normal Flask send_file including
ranges, PDF MIME and the original download filename. Missing/stale/failed caches
immediately fall back to the source; a request never starts or waits for OCR. Managed
Goodnotes raw links retain private/no-store. Re-exporting from Goodnotes invalidates
the old cache immediately, before any new OCR finishes. No source rewrite means no
self-triggered sync loop or erroneous server-only ownership/import warning.

`search.py status` / private `/semantic-search/status` now includes `searchablePDFs`
ready/pending/failed/unchanged counts. Existing journal logs report page and document
completion; `ocrQueuedFiles` includes the geometry backfill. To retry a failed derived
PDF, remove only its SHA-named derived cache directory and touch `pdf-changes`;
never remove source PDFs, index.sqlite, model files or cleanup-state.json. Existing
`search.py ocr-file 'PDF Library/<relative>.pdf'` also queues a missing layer cache.
The normal path units process future uploads and exit when no work remains.

Maintained tests: lecture-search/test_searchable_pdf.py and app/tests/test_pdf_text_cache.py.
Source references: https://tesseract-ocr.github.io/tessdoc/FAQ.html ,
https://pikepdf.readthedocs.io/en/latest/topics/overlays.html ,
https://github.com/ocrmypdf/OCRmyPDF/tree/v17.13.0/src/ocrmypdf/hocrtransform .

Verification: six full regression tests plus the added mixed-crop regression passed
(seven maintained cases), covering four rotations, nonzero crop origin, pixel-identical
rendering, native/Goodnotes text preservation, links/bookmarks, original source hashes,
resumable cache reuse/invalidation and pruning. Five web/cache route cases passed:
existing auth gate,206ranges,PDF MIME/original filename/no-store, corrupt/missing/stale
and symlink/outside-source fallback. The mixed-page regression also passed under a
real user systemd scope with256MiB/one-core caps. Sample peak process RSS117404KiB.
Actual Classes.pdf has searchable DAMDUAN THANAKRIT on page1, retains original native
handwriting on page5, and both checked pages are pixel-identical to the original.
Original source fingerprint unchanged. Live full HTTP body matches the derived PDF;
existing Mac pointer reached it through private HTTPS with206/application/pdf.

Deployed web image9e8c6d5c616a, extending the current8a118ace6563 base so the calendar
bridge update is retained. Only web recreated. Compose configuration hash and image
environment/command/entrypoint/user/workdir/healthcheck remained identical; all other
container IDs unchanged. An initial broad raw-JSON fingerprint comparison was stricter
than the semantic Compose/image checks (array ordering); it was not used as proof of
an unintended setting change. Worker resource settings remain unchanged; sampled
live OCR cgroup peak153755648bytes (~147MiB), no new resident service/model.
The existing path units queued the283-document/9200-page geometry backfill; counts
can change. This is not a claim that the full library backfill has finished.

Final observed state:32derived PDFs ready,4needed no extra text,247queued,
0derived-file failures. Counts are a snapshot while the one-time backfill runs.
OCR cgroup later reached its256MiB cap with~135MiB anonymous/~98MiB file cache;
normal memory reclaim occurred, oom/oom_kill0 and NRestarts0. Do not report the
initial147MiB sample as a maximum for the whole backfill. Existing index/pasted-image
coverage and errors were not reset. Web healthy, no change on Mac or Cyborg.
Task staging, synthetic fixtures, failed candidate image and temporary tag removed;
required layer checkpoints/cache/runtime and maintained tests/docs remain.


## Hidden pasted-image OCR in notes (2026-10-21)
Server Image Paste1.2.0 reuses /semantic-search/image-text on privateHTTPS443.
Cached text is saved as hidden tagged HTML comments in parent notes for ordinary
offline Obsidian search. No Mac OCR/model/index. Existing image_ocr cache and
ocr.lock are reused; one image recognized per request, no embedding import.
Comments are excluded from server prose chunks to avoid double indexing.
Native Search verified38matches in14notes. Source prose/image links/annotation
JSON preserved; bounded event queue and concurrent-edit-safe writes.
Client19checks and server8cache/lock/pathchecks passed. Three cached image errors
remain for offload repair; do not claim complete handwriting/math accuracy.
Added Tailscale443 /semantic-search proxy to existing Caddy8094 prefix, retaining
root/WebDAV and9443. No new listener or worker service.

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


## Pasted-image OCR confidence and controls (2026-10-24)

Server Image Paste1.2.5 and shared image_ocr_quality.py replace unconditional nonempty-text acceptance for pasted-image OCR only. Existing Tesseract emits txt+TSV in one run; character-weighted confidence60 and confident-character fraction0.55 gate the image, followed by line confidence55/fraction0.4. No English dictionary filter: code/equations/short labels remain eligible. Confidence is not proof of correctness; faint handwriting/technical text can be rejected. PDF OCR/native Goodnotes search is unchanged.

The same stdlib module is used by Cyborg helper and optional legacy local OCR; no model/package/service/polling introduced. Policy-v2 image cache records no-text for rejected results, so ordinary rereads do not recognize again. Old helper responses without the expected policy defer rather than accepting unfiltered text. Existing referenced images were refreshed once under the new policy using existing queues. Original image bytes remain unchanged.

Right-click a rendered server image for Skip image OCR or Retry OCR (keep low-confidence text). Existing native image actions remain through Obsidian Menu.forEvent when available; editor-menu and cursor commands are fallbacks. Skip persists in the shared per-image cache across notes and restarts, removes generated alt metadata and stale image-only search passages, never deletes the image. Retry is explicit acceptance of raw low-confidence OCR and persists pending-force while Cyborg is unavailable; it respects Server tasks and reuses the existing dispatcher/worker. Busy cache changes report busy without claiming success.

Generated alt suffixes are updated/removed while preserving manual labels, dimensions, titles, URLs and surrounding text; annotation notes use parsed JSON and recovery/code fences stay protected. No-text photos retain clean image embeds. Only a bounded session result cache (128entries/128000text characters) avoids repeat Mac requests; no disk OCR/index/model. One-time metadata refresh changed19notes; hashes of content stripped of generated OCR and image identities were unchanged. Actual food photo1044characters/25.5mean confidence rejected; clear code/equation76characters/85.1 retained, real lecture screenshot retained593reliable characters. Filtering can omit weak lecture lines; use explicit Retry to keep those.

31initial backend/quality/search/inventory regressions, final22focused cache/quality/protocol checks and10client metadata checks passed. Live native image-menu, Skip persistence, asynchronous forced Retry, rendered image and note integrity checks passed. Test notes/images/cache rows, staging, diagnostics and troubleshooting copies are removed after completion. Helper source-only image update preserved existing external mounts,8CPU/8GiB caps, bounded local logs, restart/manual pause policy and other containers; no GPU AI/audio changes. Maintained tests: deploy/cyborg-helper/test_image_text.py and test_image_ocr_quality.py.


## Essential fallback and reconnect identity (2026-10-30)
Indexing, PDF/image OCR and invisible PDF layers now continue on the main server when
Cyborg is Off/busy/unavailable. Existing sequential tools and POTION8M are reused;
no LLM or interactive semantic model fallback. One shared nonblocking local slot and
existing75/queue handling avoid concurrent heavy work. Index256MiB/75%CPU and
OCR256MiB/100%CPU retain their ceilings and now have MemorySwapMax=0. Vector mirror
updates stay durable until Cyborg returns. No extra daemon/timer/Mac storage/model.
Known-UUID USB device renumbering adopts identical inode/size/nanosecond-mtime sources
without invalidating page text, vectors or geometry; real changes still invalidate.
See /srv/docker/tamestorage/docs/CYBORG-HELPERS.md and maintained deployment tests.
