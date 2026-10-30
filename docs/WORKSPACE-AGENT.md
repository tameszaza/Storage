# Workspace agent

The existing Assistant now separates provider choices into Workspace (Cyborg or
Google) and Chat archive (Cyborg chats, Google chats, Excerpts). Ordinary Workspace
defaults to Cyborg; archive entry links keep their existing default. There is no
automatic cloud fallback. Existing styling, composer, images, Options and archive
behavior are retained. Refresh an older tab before sending: stale clients receive
409 before a Workspace job is submitted, preventing untracked background actions.

## Execution

POST `/chat` authenticates the account, validates Origin/CSRF/client capability,
bounds input and stores a job in the existing NAS volume. It does not run a model
or scan the file inventory in the foreground. Selected file context is bounded;
recent Workspace browser messages are untrusted context, not permission. Existing read-only
navigation commands remain deterministic. Slash file writes use the same durable
execution boundary as agent-selected tools.

The shared harness advances a checkpointed state machine:

1. Inspect attached images one at a time, when present.
2. Ask the model for one next tool or a final answer/clarification.
3. Fulfill required account-scoped lookups. Task/event IDs come from actual readers.
4. Supply only that tool's typed argument schema. Validate types, fields, actions,
   account scope, observed IDs/paths, target ambiguity, date grounding and URL provenance.
5. Persist the exact proposed arguments before execution.
6. Persist a started-operation record before a mutation; execute one tool, read back
   the outcome, then commit its result and action receipt.
7. Continue through every requested outcome. Compare the original request against
   actual results before finishing, and perform missing work found by that review.

Both providers use this harness. Cyborg is a new additive `workspace` action on the
existing native `/v1/assist`; it uses the installed `gemma4:12b-it-qat`, temperature 0, no thinking,
8,192-token context and the existing shared model lock. It only returns JSON and cannot
execute server tools. Google uses the existing configured Workspace model with
JSON Schema output, temperature 0, SDK automatic function calling disabled and one
transport attempt per harness call. The harness owns retries and provider cooldowns. Temporary Google failures also
stay pending and retry after backoff, including after a successful intermediate
write; they no longer stop after three transient failures. Permanent API
credential/model/access errors report the configuration problem.
No model download, SDK dependency, daemon, Mac runtime or boot autostart was added.
Before each bridge inference, while holding the shared model slot, `model_runtime.py`
reads Ollama residents and unloads other models with `keep_alive=0` before loading
the selected model. Grammar requests use the same switch path with their existing
grammar model. An active bridge inference is never interrupted to free VRAM.
Cold model switches take longer than resident inference.

## Durable state and boundaries

Archive-mode and older unclassified browser turns are not automatically copied into
Workspace model context. Explicitly quote relevant information when crossing modes.

Completed turns retain a compact, account-checked reference/focus receipt, including
stable IDs and paths, rather than file bodies. Argument generation receives the
same recent conversation, current focus, outcome plan and references as planning.
Follow-ups such as “it” and “the first one” resolve from these receipts and refresh
current records before editing. Older receipts without this structure still use
verified action titles for a fresh lookup.

State: `/srv/tamestorage/.tamestorage_state/assistant/` (0700).

- `jobs.sqlite3` (0600): WAL/FULL jobs, state transitions, leases, provider pauses
  and per-operation receipts. Job identity is account + browser request ID.
- `images/<job-id>/`: temporary originals needed for queued vision/diary processing.
  Originals are removed after completion/failure/cancellation; explicit diary saves
  still use the existing permanent image store and idempotent LiveSync writer.
- `pending`, `queue.lock`, `worker.lock`: durable dispatch signal and race/worker fencing.
- `worker-key` (0600): private server-local process-endpoint credential, independent
  of the archive database/collector. Never print or copy it into logs/docs.

An HTTP retry or provider switch with the same request ID returns the existing job
or receipt. A failed/canceled request with no mutation operation may be explicitly
regenerated. Completed writes are not replayed. A crash after a non-idempotent mutation starts
but before its receipt commits stops in `review`; the harness cannot assert that
no side effect occurred and does not blindly retry. Explicitly idempotent file/note/diary
writers resume the same persisted operation identity and verify its saved result. This is not a distributed
exactly-once transaction across the filesystem, calendar and LiveSync.

The UI polls only while visible, persists job IDs through reload and displays
the actual tool/task being worked on, completed outcome count and verified progress.
Offline/busy states name the provider and requested task; the interface does not
call a Cyborg request “queued”. No additional progress panel was added. Stop cancels before the next tool; an already running action
finishes its verification. Clear chat requests cancellation of its queued Workspace
jobs and preserves completed changes. Account/CSRF checks also cover cancellation.
Missing expired context receipts are ignored; another account's receipts are rejected.

## Tools and correctness

Existing scoped calendar, task, document, media and diary integrations are reused.
Task reads now include stable IDs and canceled state; open tasks exclude canceled
items. A targeted local planner lookup avoids relying on a truncated task list.
For single-action operations, schemas omit irrelevant fields and bind the exact
observed ID. Ambiguous targets produce server-generated questions. Dates are
resolved against the original request clock; rescheduling preserves event duration.
Only verified receipts supply mutation-completion text. Live-state answers require
relevant tool evidence. Ordinary list responses are rendered from actual records.

File creation/copy/move/rename use scoped paths, no-follow directory descriptors,
exclusive copies and Linux `renameat2(RENAME_NOREPLACE)`. Concurrent destination
files are not overwritten. Metadata/version move helpers remain in use. Managed
archive data and its containing root cannot be moved by this interface. File copies
are limited to one regular file under 128 MiB; larger/directory transfers belong in
the file manager. Verified file/image saves invalidate the ordinary file index;
read-only questions and queue submissions do not trigger inventory rescans.

Schedule images retain two independent extraction passes and the existing date
verification/review workflow. Reading images never directly creates calendar events.
Model reasoning and image interpretation can still be wrong; typed JSON and real
quotations/records do not prove semantic correctness. Missing/ambiguous evidence is
handled conservatively rather than reported as successful execution.

## Scheduling and limits

The existing transcription dispatcher reads the Workspace pending marker. Audio
and helper queues, lecture indexing/OCR and an actual Quartz build retain priority;
Workspace advances before optional archive work. Native service/admission/manual
stop settings are unchanged. Offline/busy Cyborg work stays queued. No queued job
automatically enables native AI or changes Server tasks controls.

Each worker is short-lived, nice 19 / idle I/O, with a 512 MiB data-allocation limit,
45/50 seconds CPU limit, no core dumps and 285 seconds wall timeout. The existing web
2 GiB/no-swap cgroup remains the aggregate memory ceiling. RLIMIT_DATA is intentional:
an address-space cap prevented the existing Node/V8 diary runtime from starting
because of its uncommitted code-range reservation. Node retains its existing 96 MiB
old-space cap and 75-second publisher timeout.

Useful work has no 12-tool/28-inference cutoff. Compound requests get a durable
outcome plan; verified effects are credited even when executed out of order.
Equivalent readers can fulfill discovery outcomes. The final independent review
can append accidentally omitted work. Identical unproductive lookups, duplicate
write attempts and repeated identical invalid output are bounded to prevent loops;
different successful steps and repairs keep progressing. Existing per-worker limits
and priority scheduling still apply. Missing file prerequisites and canonical
account-relative paths are resolved without an extra model argument round trip.
Goal-level file organization may create folders and move files; plan-only/negated
requests remain read-only. Requested explanations can accompany verified receipts.

Storage/admission bounds: 12 active jobs/account, 64 global active jobs, 512 MiB queued image originals, and the
existing 8 images / 20 MiB each / 40 MiB batch upload limits. Queued writes older than 24 hours
expire instead of unexpectedly acting on stale intent. Terminal receipts are kept
for up to 7 days / 256 jobs, with bounded pruning during submission; no cleanup timer.

## Validation

- `tests/test_workspace_goals.py`: complete compound requests, more than 40 model
  interactions, outcome review/recovery, out-of-order effects, equivalent readers,
  context/ordinal follow-ups, automatic prerequisites and relative paths.
- `tests/evaluate_workspace_goals.py PROVIDER`: opt-in real-provider task, file,
  follow-up and vision scenarios using synthetic data only.
- `tests/test_workspace_agent.py`: real tool contracts, target/account isolation,
  stale/invented IDs, typed arguments, negation, grounded dates, deduplication,
  cancellation, offline recovery, crash boundaries, postconditions, no-overwrite
  files, symlink rejection and Node compatibility under actual worker limits.
- Existing assistant tool/diary/image suites remain in use, updated for queued HTTP
  delivery. Calendar review still verifies before any event write.
- `tests/evaluate_workspace_agent.py PROVIDER --cases ...`: opt-in real-provider
  tests against synthetic accounts/files only. Never use personal data for evaluation.
- `tests/browser_workspace_agent.py FIXTURE_DIRECTORY`: real shell/CSS/fonts with
  mocked transport; provider selection, queued reload recovery, draft preservation,
  cancellation/clear and desktop/mobile geometry. Existing image browser suite also
  remains applicable. This is not a physical Safari/iPad test.

Production uses the existing immutable web image with read-only source binds.
Gunicorn preload requires recreating only web for Python changes. Preserve its
environment, image, mounts, security flags and memory/no-swap settings. Do not stop
primary transcription work just to reload its dispatcher: the optional archive
maintenance marker can let an idle dispatcher exit naturally before the next wake.

## Dynamic tool protocol (2026-10-28)

Workspace now discovers and composes actual connected tools rather than requiring
a bespoke workflow for every request. `describe_tools` lists role-scoped tools and
can return exact schemas. `request_capability` resolves paraphrased current intent
when initial keyword matching did not enable a necessary write. The resolver receives
the current request and conversational context, never retrieved text/OCR as authority.
A grant enables the specific tool/action; explicit no-change requests remain read-only.
Unavailable integrations are reported rather than invented.

`compose_operations` persists an ordered plan of up to32 operations per batch. Each
child still passes the usual scoped discovery, typed validation, receipt and readback
boundaries. `save_as` binds a real result; `{"$ref":"folder.path"}` passes its field
to later calls, and `{"$join":[{"$ref":"folder.path"},"report.md"]}` joins relative paths.
No expressions or arbitrary code are evaluated. Google receives at most four calls
per generated batch to fit its structured-output grammar; further batches and
ordinary calls continue the same goal without an overall four-action limit.
Schemas derive nested objects, arrays, nullable and Literal types from annotations;
server validation remains authoritative. Provider schema rejection falls back to
individual tools. Docs: https://ai.google.dev/gemini-api/docs/structured-output
and https://docs.ollama.com/capabilities/structured-outputs .

`workspace_protocol.register` attaches domain/actions/retry-safety and a verification
callback for future scoped integrations. The integration factory still supplies real
account access; registration does not install a service or grant unrestricted OS access.

Additional capabilities:

- `write_text_file`: create, append or replace scoped UTF-8 files.
- `edit_text_file`: replace unique exact text, or explicitly all matches.
- Admin `search_obsidian_notes`, `read_obsidian_note`, `write_obsidian_note`: discover
  metadata, read bounded line ranges, create/append/replace/edit visible Markdown notes
  through the existing encrypted LiveSync client. Discovery paginates metadata;
  it does not copy the whole vault. Other accounts do not receive these tools.
- `append_diary_entry`: independent calls with explicit 1-based `image_numbers`
  selections and optional target day. null attaches all to a diary entry; [] none.
  General note writes default to no image attachments. Separate/grouped outputs
  have independent duplicate protection. Request timestamps stay the original
  recorded time even when saved under a different requested diary date.

Existing text must first be read and its SHA256 revision supplied (the runner can
reuse an observed revision). File writes use no-follow parent descriptors, exclusive
create/atomic replacement, immediate hash checks and normal app version history.
External NAS writers do not participate in a distributed filesystem CAS; a final
external write race cannot be ruled out. LiveSync uses its existing revision CAS,
with hidden per-operation markers for lost-response retry protection. Conflicted
notes are not overwritten. Text files/results are limited to1MiB; arguments to16,000
characters. Managed archives, secrets and other accounts remain outside these writers.

A missing parent or stale revision is a repairable precondition. Plans preserve the
pending text while discovering/creating prerequisites, rereading, then resuming.
Completed operations survive restart/cancellation; only the unfinished operation
continues. Image descriptions remain numbered in persistent model context even
after other tools push them out of recent observations. Output goals track typed
image selections rather than requiring the model to repeat matching title prose.
This improves source association but cannot prove model interpretation or prose accuracy.

Validation adds `test_workspace_dynamic.py`, expanded `test_diary_writer.mjs`, and
opt-in `evaluate_workspace_dynamic.py PROVIDER --cases separate,group,report,chain`.
Live evaluations use synthetic files/images and mock vault publication; actual vault
compatibility is checked read-only. No personal note is used as a write fixture.
The frontend, existing defaults, primary-work priority and worker resource limits
are retained.

HTTPS ingress: Assistant Origin checks accept an HTTPS origin for the same
authority when Tailscale Serve forwards to local HTTP. Cross-host/port origins
and invalid/missing write CSRF tokens remain rejected; no forwarded-host headers
are blindly trusted.

Verified 2026-10-28:169 Python checks and18 Node checks, including Node under the
actual worker limits; desktop/mobile dark/light Workspace browser flows and image
attachment flows passed using the production shell/assets. Synthetic real Gemma
and Google runs covered separate/grouped photos, CSV-to-report and folder/file/task
chains. A final Gemma grouped-image run preserved Workout/Lunch vs Building A source
association. Live encrypted vault read was read-only; no personal note write fixture.
The normal dispatcher -> bounded worker -> Gemma -> ready receipt passed, then its
synthetic greeting receipt was removed. Main NAS service stayed active with the same
PID/memory protections; live PDF byte-range probes returned206/65,536bytes.
Reasoning and vision quality remain model-dependent; tests prove the checked
operations/source selections, not universal interpretation or arbitrary capabilities.

## Photo continuation recovery (2026-10-28)

A real three-photo job exposed an incomplete model outline: one goal for three
image groups, followed by75 changed-prose attempts at an already-saved image.
Distinct diary receipts with equal titles were also collapsed by display text.
Synthetic happy-path tests had not exercised this partial outline/repeated-prose case.

The runner now fills every independent image outcome even when the model omits
steps. For simple unambiguous separate-photo log requests, the server schedules
one bound output per image, drafts from only that image's reading, advances from
verified results and finishes from the receipts. Successful three-photo work uses
three vision plus three draft calls; no redundant outline/next-step/final-review
inferences. Richer compound requests keep the general planner. General image schemas
exclude satisfied selections, and repeated precondition failures are counted by
logical target/error rather than editable prose, bounding the stalled-target loop.

Regenerate can resume a failed Admin separate-photo request from completed diary
operation receipts. It validates original saved image identities and attachment
count, restores original request time, and skips saved/read images. Existing notes
are not appended again. This also handles legacy failures that discarded their
originals, when the browser reattaches the explicit original batch. Canceled/complete
requests do not resume, and ambiguous/non-diary effects remain outside this path.
For legacy failures without a trustworthy explicit batch size or retained originals,
no automatic partial resume is inferred. Ordinary same-ID reattachment supports
older open clients even if they omit archive_refresh.

New recoverable separate-image model failures retain their checkpoint and uploaded
sources for explicit retry, within the shared512MiB admission budget. Ordinary
submission/pruning releases retained failed sources older than24hours; there is
no new cleanup timer. Successful/canceled jobs still remove their temporary originals.

Receipt identity uses entry_id/operation_id rather than visible title and displays
image numbers. The client maps individual save receipts to the corresponding
original attachment, preserving order and partial recovery. Stored-image retries
fetch through the current origin. Manual reattachment retries preserve request ID
and regeneration intent. Existing layout/styles/providers and primary-work priority
remain unchanged.

Validation:176 relevant backend checks pass, including incomplete outlines, changed
prose loops, equal-title receipts, legacy partial retry without duplicate writes,
original date preservation, source retention and admission/pruning. Updated desktop
and mobile image browser tests cover partial image1/3 receipt mapping and same-ID
Regenerate; Workspace dark/light/desktop/mobile flows pass. Real synthetic three-
photo runs completed with Gemma in45.69s and Google in17.06s (load-dependent, not a
latency guarantee). The user's saved treadmill/building images were also checked
locally on Cyborg with mocked publication: independent entries and correct building
source association passed in44.68s. Those private images were not sent to Google.

Dispatcher latency: the existing idle dispatcher now checks its wake timestamp
once per second while pending, so a new request interrupts an old60s idle wait.
When primary work is clear, its sleep is also bounded by the earliest Workspace
retry/lease deadline from a read-only query. Primary helper/audio/indexing work
still takes priority and retains its existing backoff. There is no new daemon,
dependency, boot setting, busy spin or primary-service restart.
The native bridge's exact shared-slot busy503 response retries after5s; offline,
paused, timeout and cloud failures retain ordinary backoff. This prevents a brief
local contention from escalating into several minutes of idle delay.

Added wake-arrival, retry-deadline, worker-lease and primary-priority regressions,
plus strict native busy classification/retry tests:182 relevant Python checks pass.
The host dispatcher and maintained deploy/cyborg-helper mirror must stay identical.
Load dispatcher changes at its next natural idle exit; do not kill primary work.

Availability is separate for the background indexing helper and native assistant.
If primary audio/indexing is active or its queued helper is reachable, it still
goes first. An unreachable helper port gets its existing guarded ensure attempt,
throttled to once per60s during foreground work, while the independently reachable
assistant may proceed. This preserves the pending index request and Server tasks
On/Off/admission controls. No unavailable helper queue is deleted or treated as done.
A read-only TCP probe does not enable or submit background work.
185 relevant Python checks pass, including offline-helper fairness and active-primary
priority. Reloading the sleeping coordinator is allowed only after freezing it and
verifying no child/network operation is active; do not interrupt primary work.

Final continuation verification: web healthy with unchanged2GiB/no-swap limits; Samba PID74020/memory protections preserved. Live PDF206 probes returned65,536bytes in0.038/0.042s. Live dispatch began in4.16s using a synthetic Google probe; this is not a cross-model speed comparison with the earlier122.43s Gemma probe. Original partial request and notes remain. All synthetic probes, temporary validation container/browser fixtures and deployment snapshots removed; owned maintenance pauses cleared.
