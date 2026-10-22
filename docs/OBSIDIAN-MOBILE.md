# Obsidian on iPad

## Plugins and sync

Mac source vault: `/Users/tameslist/Obsidian/Notes`. Custom versions: Tames PDF1.2.1,
Lecture Chat0.3.7, Cyborg AI1.1.1, Graph Memory1.0.1. All nine enabled plugin
manifests support mobile; the other five already supported it. User notes, PDF
annotation data and chat history are preserved. Standard Customisation Sync carries
the small helpers inside main.js; Include extra plugin files is not required.

Publish MacOS files with LiveSync Customisation Sync; keep automatic scans and
periodic scans off. On iPad select the MacOS plugin code/data, theme, snippets and
shared settings, apply and restart Obsidian. Keep iPad's LiveSync connection/device
settings and workspace files device-specific. Never initialize/overwrite the remote
note database to transfer customizations. Local customization application and physical
iPad verification still require the iPad; successful Mac publication alone is not proof.

## On-demand chat bridge

Canonical code: `routes/obsidian_mobile.py`, `lib/obsidian_mobile_runner.py`.
Existing web container serves `/obsidian-mobile/{start,poll,result,cancel}`. No new
Docker service, daemon, scheduler, local model, index or idle polling. The iPad runs
the same visible-vault read-only tools and source/image budgets as Mac. A temporary
loopback MCP server proxies tool calls back to iPad's native HTTPS transport. Current
unsaved context/history is supplied by the client; subsequent file reads use the iPad
vault and existing private semantic/PDF endpoints. No duplicated server vault export.
The tutoring/citation instructions are reused verbatim from the existing plugin.

Private NAS runtime: `/srv/tamestorage/.tamestorage_state/obsidian-mobile` (0700).
This is outside the uploads/Temp cleanup tree. Contents:

- `codex` and `codex-code-mode-host`: matching official0.163.0 musl x86_64 binaries.
  Both are required; copying the CLI alone makes tool calls fail. Release archives
  verified against official GitHub SHA-256 digests, then removed. No Mac runtime copy.
- `home/`: isolated CODEX_HOME with existing ChatGPT auth, native model cache/runtime
  state. Auth is0600, never sent to iPad or stored in the vault. The official headless
  auth-cache copy flow was used. Later login: CODEX_HOME=<home> <codex> login --device-auth.
- `bridge.key`: dedicated0600 bearer key synced only as plugin connection settings.
  Rotating it requires updating Lecture Chat/Cyborg AI data through Mac and republishing.
- `role.txt`: existing role, study preferences and reader guidance, unchanged.
- `work/`: empty execution cwd; no vault documents or credentials.
- `jobs/`: private transient IPC. One conversation via cross-worker flock. Each request
  has a random UUID; prompt/tool buffers are removed on completion. Completed answer
  is removed when fetched. Abandoned completed metadata is pruned on a later request,
  with no recurring cleanup. No request text/auth is written to web logs.
- `assets/`: exact pinned Tames PDF runtime/worker (3.11.174). Missing mobile fallback
  engines download on first use, SHA-256 verify, then stay only in the plugin folder.
  Native overlays use Obsidian's viewer and do not load/download the fallback engine.

Codex execution uses ephemeral mode, ignored user config/rules, read-only sandbox,
no shell tool, required authenticated MCP, existing readOnly annotations and automatic
read-only approvals. Five visible-vault tool names only; there is no generic server
file/shell endpoint. Limits: one job,300s,30tools, current source budget,4page images.
Client polling1s occurs only while answering. Stop cancels its UUID; disconnected iPad
heartbeat expires after35s. Runner terminates the isolated process group and exits.
Native requestUrl cannot abort an in-flight HTTP operation; stale results are discarded,
and the explicit cancel endpoint/heartbeat bounds the actual Codex process.

## Cyborg controls

Existing grammar socket gateway also serves authenticated POST `/v1/control`, exposed
through existing private9443 `/grammar/v1/control`. Only `on`, `off`, `status` allowed.
Fixed SSH command,22s timeout, dedicated ed25519 key and pinned Cyborg host identity:
`~/.local/lib/cyborg-grammar-gateway/mobile-control-key`, `mobile-known-hosts` on server.
Cyborg restrict/forced command: `~/.local/lib/obsidian-mobile-control.py`; it invokes
only the existing cyborg-grammar helper. No shell, forwarding or automatic startup.
Mac keeps native SSH. AI off/unavailable retains normal server LanguageTool.
Writing/organize previews reuse existing9019 assist service and manual Apply safeguards.

## Verification

All nine mobile module evaluations reject Node imports. Mobile HTTP, path traversal,
read-only tools, model choices, organization preservation, serialized graph writes and
unchanged-data no-write checks passed. Eleven touch/engine lifecycle checks passed:
region/textbox draw, move and resize commit final pointer-up coordinates without delay;
buttons=0 during touch movement does not prematurely commit. Graph physics unchanged.
Two full mobile assistant/chat onload checks passed. Five server request-isolation tests
passed; unauthorized/private controls401, authorized status200; restricted SSH rejects
unrelated commands. Real native Codex fixture requested and read a mobile file and
returned exact text with citation (13.7s). Cancellation0.61s, no remaining Codex/host
process. CLI-only RSS peak in that fixture239316KiB, excludes host/runner and shared
page accounting; not a total memory measurement. No idle Codex process after a question.
581 primary Mac files hashed unchanged after plugin installation. Physical iPad remains
user-side verification. Maintain tests/test_obsidian_mobile.py for request contracts.

Deployment reuses the unchanged live9e8c6d5c616a web image. Three small read-only
canonical source mounts are declared only for web in compose.yaml: routes/__init__.py,
routes/obsidian_mobile.py, lib/obsidian_mobile_runner.py. Preserve these on future
deployments; changes require recreating web. Existing image, environment, command,
resource limits, ports and nine other container IDs were verified unchanged.
The iPad tool reader sends a ten-second heartbeat only during a slow file/PDF tool;
heartbeat polls never consume a completed answer. Client timers are cleared afterward.
Real authenticated HTTPS simulated-mobile round trip also passed (13.7s), with
read-only tools, exact text/citation and401for an unauthenticated request. Both
fallback engine downloads passed their pinned hashes without saving files on Mac.
