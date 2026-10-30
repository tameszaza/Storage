# Chat archive and Assistant

The server owns the archive at `/srv/tamestorage/uploads/Admin/ChatGPT Archive`.
Open `/admin/chat-archive`, or Admin center → Chats. Ask opens the existing Assistant
at `/chat?archive=1`. Archive questions default to Cyborg; Google and Excerpts are
per-question choices in the same compact selector. The normal `/chat` workspace,
file/calendar/task tools and image composer retain their defaults and design.

## Data and ingestion

`data/progress.sqlite3` is authoritative: text messages, all available branches,
conversation IDs/timestamps, saved link queue, discovery cursors and shared cooldown.
`data/search.sqlite3` is a separately rebuildable FTS5 index, vectors, provenance,
monthly counts, answer receipts/cache and durable pending work. Its WAL uses FULL
synchronous commits. Index commits precede source-dirty acknowledgement; changed
source JSON keeps its newer marker. Dirty documents are excluded from answers.
`data/exports/<scope>/` holds readable per-conversation TXT, `all-chats.txt`,
`messages.jsonl`, `catalog.jsonl`, coverage report and counts. Combined exports use a
short-lived SQLite snapshot so formatting does not hold a live reader lock. Snapshot
files are removed on completion. These are current accessible message trees, not
an atomic account snapshot or a version-history service. The full export is ongoing.

Cyborg Firefox still reads ChatGPT, preserving request pacing, Retry-After and all
checkpoints. Its existing loopback socket now relays authenticated RPCs over HTTPS to
`/admin/chat-archive/collector`. No ChatGPT cookie or bearer token leaves the browser.
The relay connection key is private on both hosts; never print it. Server key:
`/srv/tamestorage/.tamestorage_state/chat-archive/collector-key`,0600. Normal archive
pages, search, messages and answer jobs require Admin; machine endpoints require the
key. Responses are private/no-store and bypass the service-worker fallback.

The exporter launcher and temporary-addon procedure are unchanged. Relay code,
private connection key and tiny remote.json remain on Cyborg external service disk.
Its old primary database/export copy was removed after logical checksum, integrity
and output verification. Documents/ChatGPT Text Export.html opens the server archive.
Do not restore a local primary DB or put SQLite on an SMB network mount.

## Retrieval and answers

Keyword ranking, date/role filters and source IDs are deterministic. All text is kept
in the authoritative export; indexing and displayed/AI excerpts mask recognizable
credential patterns. This is not a guarantee of complete personal-data anonymization.
Exact mode uses no AI provider. Default answers use the existing native Cyborg model
and grammar context (no new model download; GPU inference uses the existing service). AI returns structured
claims; every displayed claim requires a recognized source ID and literal 8–350
character quote. Links address exact message IDs/pages, including empty messages in
page numbering. User statements, assistant guesses and alternate branches stay
separate. Quotation/ID validation establishes provenance, not logical entailment;
model conclusions still need review. Corpus-wide semantic recall waits for queued
embeddings. Native tokenization and the reused embedding model can limit multilingual
or paraphrased recall; model-answer tests are not whole-corpus recall benchmarks.
No inferred facts/preferences are silently saved to Obsidian, and no fine-tuning runs.

Google is opt-in per question and receives bounded relevant redacted excerpts, never
the full ZIP/database. Archive Google model is pinned to `gemini-3.6-flash` by default
(optional Flask CHAT_ARCHIVE_MODEL override). Published free-tier eligibility does
not verify a project's billing status/quota. No billing or API key was changed.
3.8 Flash is stable/free-tier eligible per current Google docs but returned503 high
demand in all initial generation probes, including a minimal prompt. 3.6 passed the
available synthetic grounding cases; one probe also returned503. Provider errors
retain source excerpts, without a paid-model fallback.
Official research checked2026-10-27Singapore:
- https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://ai.google.dev/gemini-api/terms
Free services may use submissions for improvement/human review. User chose local AI
as archive default, with an explicit Google/Excerpts choice. No private archive was
sent to Google for evaluation; provider tests used synthetic evidence only.

## Main features take priority

The existing transcription dispatcher wakes archive work; no additional daemon,
recurring timer, model, Mac index or boot autostart. Its archive lane runs only after
existing audio/helper queues and lecture-index/OCR units are idle, with no Quartz build-required marker. The Quartz watcher stays running while idle and is not a busy signal. The durable
`chat-archive/pending` marker survives worker/web/Cyborg restarts; queue.lock prevents
an enqueue racing with marker clearing. Worker steps run in short-lived children
with nice19, idle I/O,384MiB address-space limit and CPU time bound. Large queues can
therefore take longer while primary services are busy. Web requests never perform a
corpus scan; monthly activity totals update on indexing. Archive chat POSTs skip the
ordinary NAS file-inventory invalidation. Preserve the existing web2GiB/512MiB
working-memory protection/no-extra-swap and Samba64MiB/no-extra-swap settings.

Embeddings reuse the CPU helper/model in batches32; archive vector mirror and epoch
live separately from lecture vectors at Cyborg `/work/archive-vectors.sqlite`.
Atomic document replacement, epochs, source digests and vector hashes fence stale
results. Lost archive mirrors requeue, without resetting the lecture mirror.
Native archive inference yields immediately if its shared model slot is busy. Server
tasks Off, manual stops and offline states retain pending jobs. The page polls answer
receipts only while visible; reload recovers queued messages. Existing native AI and
CPU-helper startup/admission preferences remain authoritative.

## Maintenance and verification

Maintained code: lib/chat_archive{,_export,_jobs}.py, routes/chat_archive.py,
archive templates/assets, existing chat/app/route registration, exporter server.py,
Cyborg assistant.py/archive_contract.py and helper worker.py. Existing dispatcher:
~/.local/lib/tamestorage-transcription/dispatch.py; maintained deploy/cyborg-helper copy.

Tests: tests/test_chat_archive.py; tests/evaluate_chat_archive.py (opt-in external
provider calls, synthetic only); tests/browser_chat_archive.py. Fixture HTML/assets
must be isolated. Exporter tests remain on Cyborg. Verified initial migration2334
conversations/2338links by full logical checksum of all original tables and SQLite
quick_check. Server subsequently stored2350 conversations/38304messages and all2350
per-conversation files; initial full account discovery remains unfinished.

The large-data test exposed export/index lock contention; separating the derived WAL
index fixed it. Tests prove an index transaction cannot block a primary save and a
concurrent source edit keeps its dirty marker.51focused archive/diary/image tests,
exporter22Python tests plus JS resume/429/bridge regressions, queue-priority scenarios,
real independent sqlite-vec namespaces and four desktop/mobile light/dark browser
flows passed at completion. Native archive tests cover latest decision, contradiction,
unrelated question, injection, Thai, paraphrase and unverified completion, with literal
citation checks. Runtime uses the existing grammar model; all6 final synthetic cases
passed. No claim of perfect factuality, cross-language recall or completed account
export. Retain primary data/runtime and queued work; remove only verified task staging,
fixture profiles/screenshots, retired local data and temporary snapshots after checks.

Final verification: existing image composer passed desktop/mobile picker, paste, drop, ordered sends, removal, retries, preview conversion and layout with zero page errors. Archive browser passed all four viewport/theme flows, citation export, queue receipt/reload and search. Real authenticated process endpoint→nice19/idle-I/O/384MiB child→native Cyborg→validated citations→durable ready receipt passed using synthetic data only. All51focused backend tests and five queue-priority cases passed. Actual primary PDF64KiB range probes returned206in0.146/0.186s; SMB master74020 and memory protections unchanged. Live archive retrieval checked three actual exact-message/page links without exposing their contents or triggering a NAS inventory rescan.

Quartz is a long-running watcher, so priority gating uses its build-required marker rather than treating the entire watcher uptime as busy. Queue processing was observed draining (86872→86456embedding passages) after release. Initial bulk embedding work remains queued and yields to primary work. Saves/checkpoints enqueue all derived indexing; no foreground FTS rebuild. Task staging, browser fixtures/screenshots, completed SQLite temporary snapshots and old Cyborg primary copy removed; runtime DB/WAL/queues/current source/tests preserved. Remaining full-history collection uses Firefox Start/Resume and the preserved server checkpoint; a stored running report alone is not proof of current browser activity.
