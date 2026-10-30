# Lecture Chat

Open the speech-bubbles ribbon icon or command **Lecture Chat: Open lecture chat**.
Choose a note/PDF, optionally restrict PDF pages (e.g. `4, 8-12`), and ask.
Turn on the text-selection icon to follow the current note/PDF. Selected PDF text
keeps its page location; with no selection the visible PDF page is used. The footer
shows this context beside the model picker and Send. Only pressing Send calls Codex.
The **Ask about selection** command also enables this mode.
Answers render Markdown/LaTeX and clickable Obsidian source/page links.
The compact file picker chooses a source; adjacent icons use the current file or
selection/page context toggle. PDF options expand to page ranges and annotations;
blank page ranges follow the current page; explicit ranges override it (e.g. `20-`). The arrow sends;
it becomes a Stop button while answering. The top-right New chat icon clears chat.
Button descriptions and the OpenAI disclosure are available on hover.

The only dependency is the existing authenticated Codex CLI (`codex login`).
Codex uses its native tool loop to find/read any relevant visible vault note/text
file, search notes and request more PDF pages itself. Initial selected context is
a starting point, not a restriction. Ask “from this page onward” and Codex can
read later pages on demand. This uses the existing Codex engine; no copy of the
VS Code extension, SDK package or replacement model implementation is installed.
No npm libraries, model downloads, local vector database/indexer, watchers, or
startup AI process on the Mac. One ephemeral CLI invocation per question.
The sidebar reads note text or the existing PDF viewer’s text in memory when asked;
Indexed library PDF text is read from the private server first, avoiding temporary
PDF viewers on the Mac. If the index/server is unavailable, direct core viewer
reading remains the fallback. Tames PDF loads server URL-pointer PDFs when visual
viewing is needed. Original PDFs are never rewritten.
The chosen PDF’s current saved annotations are read from its matching `.sets` folder;
automatic `.previous.md` recovery copies are skipped.

Text extraction is the default. When columns, diagrams, scanned pages or a disputed
reading need visual verification, Codex can request an original page image through
the standard MCP image response. Library PDF pages are rendered on the server with
published Poppler, so inspecting another PDF does not load the whole file into the
Mac PDF viewer. If the server fails, local rendering is used only for an already
loaded library PDF; local PDFs keep native PDF.js rendering. Images have original
colors and no annotation overlays or custom layout reconstruction.
Images are limited to four pages per question, 1.5 million pixels / 1,500 pixels per
side and a 1 MB encoded response per page. Transient canvases are cleared after each
render; active renders are canceled when the request closes. No images are saved or
generated while idle. Image requests also use model allowance. The Mac runs no OCR.
Other binary document formats are reported as unsupported; this
reader handles text files and PDFs, not arbitrary host files or hidden config. Source context is capped at 60,000 characters by default, adjustable
to 120,000; truncation is shown. Use narrower page ranges for large lectures.
The last 12 messages are supplied as conversational context. Saved history is capped
at 20 messages / 80,000 characters. New chat deletes it; disable Remember chat to
keep only in-memory history. No source text cache is stored. The small model picker reads the existing Codex
model cache once when the sidebar opens; it makes no background request. Default
uses the Codex default model; the selected model is saved in plugin settings.
The optional Reasoning setting defaults to the model's normal effort. Higher effort
can take longer and use more allowance; it does not change the selected model.

The general tutor identity and the student's factual study preferences are supplied
through native Codex developer instructions, separate from the read-only tool rules.
Context remains readable source text, recent Student/Tutor messages keep their roles
and source labels, and the latest question is last. Exact lecture facts and terms,
source citations and useful explanations are preferred. Interpretation/general
knowledge must be distinguished from source statements. Personal annotations are
labeled secondary material with their own note links; earlier tutor replies and
annotations may be mistaken. The original lecture is primary evidence. This improves
source handling but cannot guarantee that every model answer is correct.

All plugin code/settings/saved chat are in this folder. Existing Codex installation,
login/token refresh and Codex’s own runtime state remain outside it in their usual
locations. `--ephemeral` prevents additional session rollout files. No authentication
credentials are copied into the plugin. Source text, recent chat and on-demand retrieved vault content go to OpenAI;
normal Codex account limits apply. CLI user config/rules/project instructions are
ignored for these requests; no unrelated configured MCP servers or hooks start.
A tiny native HTTP MCP reader lives in the existing Obsidian process only during
a request, bound to loopback with an ephemeral random token. Codex connects through
a per-invocation config override; global config is untouched. No helper daemon or
extra process. The reader closes on completion/error/Stop/unload. Read-only tools
are find_files, read_file, read_page_image, search_notes and optional semantic_search.
Hidden files, path traversal and symlink
reads outside the vault are rejected. No write/edit tool. PDF text comes from the
server index or existing viewer; transient tool-created PDF tabs are cleaned up. Source character
limits apply per response, never across the whole question. Initial context is at most
12,000 characters and defaults to the current PDF page (Follow enabled) or page 1;
explicit page ranges remain supported. Codex retrieves more material on demand.
PDF/text responses remain capped at 24,000/20,000 characters; the Source response
limit can lower these bounds. Searches and page images remain available after
earlier reads. No source cache or extra idle work. Max30 tool calls, max12 PDF pages per
read and max100 Markdown files scanned per note-search call. There is no content
search on the Mac outside an explicit tool request. Server indexing follows source events.
Shell tool is disabled, sandbox is read-only, and approvals are disabled.

## Server semantic search

Search server defaults to `https://server.tailb7f57c.ts.net:9443/semantic-search`,
private to Tailscale alongside the existing vault website. Blank disables it.
The Mac only sends explicit search/read requests and receives bounded text; it
stores no embedding model, index, PDF text cache or extra dependency. Server calls
close their connection and drop temporary result strings after use; Stop cancels
pending calls. No search-as-you-type, polling, idle network work or prefetching.

The server uses the published Model2Vec POTION 8M English embedding model and
SQLite/sqlite-vec cosine search plus FTS5 keyword ranking. Files, page numbers and
exact source excerpts stay attached to hits. Notes/annotations are labeled separate
from original PDF evidence. The per-response text limit applies to semantic results too; at most
8 hits/call. The model is retrieval support, not the answer-generating tutor.
Diagram/table relationships still require inspecting the original page image.

Embedding runtime/model, index and OCR text cache live on the external NAS. Standard
server Tesseract binaries/language packs use the server system disk (~22 MB). A systemd socket starts one
short-lived Python worker for a request; it exits afterward. A model lock allows
one embedding process at a time. The existing Goodnotes and Quartz change events
trigger incremental indexing; no new polling watcher or periodic rebuild. Unchanged
sources skip extraction and embedding. Deleted sources lose their index entries;
stale PDF fingerprints are rejected until refreshed. Indexing never changes PDFs,
notes, annotations or the central sync database. Missing-text pages are queued for
server-only Tesseract OCR once per unchanged PDF. English OCR uses one page at a
time; cached page text is reused and progress resumes after interruption. No visual
index is installed. Original PDFs are not rewritten or given an OCR layer. Machine
OCR is labeled in search results/page context and may misread words, handwriting
or equations; inspect the original image before claiming exact OCR wording.

Search may report busy during indexing; the tutor can use direct reading then.
Server request/index services each have a 256 MB cgroup cap; one model at a time.
The separate OCR build worker has a 256 MB cap and exits when its queue finishes.
Its small parent holds no embedding model while recognizing pages; a short-lived
embedding child updates page vectors and exits. Initial builds cost CPU; ordinary
searches and idle use do not reprocess the library.
See server `~/.local/lib/lecture-search/README.md` and `SERVER-RUNBOOK.md` for paths,
manual refresh, resource measurements and restoration instructions.

This Mac uses its local Codex CLI; iPad uses the private on-demand server bridge.
On Linux Flatpak the host CLI may not be executable inside the sandbox. A separate
authorized host bridge would be needed; no extra permission/service is installed here.
The plugin never saves answers into lecture notes automatically. Copy text manually.

Maintenance: unbundled CommonJS main.js, no build/install step. Codex exec JSONL
`item.completed`/`agent_message` protocol documented at
https://learn.chatgpt.com/docs/non-interactive-mode .

## Native Obsidian Search

The existing core Search pane also receives supplemental semantic results using
the same private search server. Local search runs immediately and remains the
primary source. Plain text queries of at least three characters send one bounded
request after a 700 ms typing pause; up to 8 server passages can add files absent from
the local results. Results reuse Obsidian's native renderer, sorting, counting,
collapse behavior and virtual scrolling. No separate UI, CSS, command or index.
Semantic-only excerpts are labeled `Semantic`; PDF results include the page and
open that page on click or keyboard activation. A local match for the same file
takes precedence. Visible-vault files only; Obsidian's excluded files are respected.

Advanced native search syntax (operators, quoted exact phrases, regex, Boolean
expressions) stays local to preserve its precise meaning. Semantic results are
nearest passages, not guaranteed literal matches. Original normal search behavior
is described at https://help.obsidian.md/plugins/search .

Changing/clearing the query, hiding/closing Search or unloading Lecture Chat cancels
the pending timer/request and removes semantic-only rows. Slow/unavailable/busy
server: native results remain; a 20 second wall timeout bounds the extra request.
No retry loop, polling, prefetch, persistent result cache, model or local indexing.
Chat's Stop button has a separate request lifetime. Blank Search server setting
disables this integration too. Initial server OCR building can increase latency.

Implementation is the roughly 9 KB `native-search.js` inside this plugin folder, reusing
Lecture Chat's bounded/cancellable HTTPS transport. It guards temporary hooks into
Obsidian's private core Search methods and restores them on unload. An Obsidian
update may require adjusting these hooks; the fallback is normal local Search.

### PDF text inside images

The server also inventories substantial embedded images once per unchanged PDF.
Pages with both native text and images get cached supplemental OCR: their exact
native text is retained and recognized image text is labeled machine OCR. Tiny
images are skipped; handwriting and table layout may still be misread. A cached
exact multiword phrase is retrieved using SQLite FTS5 without loading the embedding
model; queries with no exact phrase use the semantic/hybrid search.

Verified test: search `Damduan Thanakrit` without quotes. A server-added
`Classes.pdf` result marked `Semantic · p. 1` opens the timetable. The normal local
results remain. Server building/busy/offline/timeout can affect arrival of extras.

### Handwriting from Goodnotes

Goodnotes' recognized handwriting already embedded in PDF text is indexed directly.
No second handwriting recognizer/model is installed. Existing cached image OCR remains
for missing image text. Exact phrase matches are supplemented by existing FTS5
all-word matches, so words separated across exported lines can still locate a page.
The PDF and its text are not rewritten to guess corrections.

Verified queries: `group project part 2` and `short journal note` find Classes.pdf p5;
`uneven cash flow` finds AB1201/Note.pdf p1; `Synthesis warnings` finds Introduction
to Verilog p49. Original pages were visually checked.


### Native search status
A 14px icon beside the existing Search result count shows a spinner during the debounce/request, a teal cloud-check when complete (including no additional matches), an hourglass for timeout, a crossed cloud for connection failures, or an alert for service/errors. Hover reveals the status and added-result count; there is no extra text row. Only the pending icon animates, respecting reduced-motion preferences. Added-result count reflects supplemental file rows. It follows the existing request only; no status polling/indexer probe/background job. Query change, clear/hide, disabled server setting, advanced native syntax and unload clear the status. Request generations prevent late replies from changing a newer query's status/results.

### Pasted-image OCR and search status (0.3.3)
Server Image Paste uploads are indexed with the existing server OCR worker when a synced/exported note references the image. Image text is cached once per unchanged file/policy and joined to the parent note's existing search index. No image recognition/index/model is stored on the Mac. Normal local Search stays immediate; supplemental image matches are labeled **Image OCR** and open the containing note. Snippets focus on the searched phrase and omit machine headers/image URLs from display only.

Test without quotes: `clock with a frequency` finds the pasted quiz in the SC1005 Sequential Circuits annotation note. Printed text works in this tested image; tiny diagram labels, handwriting and math can be misread. Unsynced local fallback PNGs become server-searchable after Server Image Paste's manual image cleanup uploads them. Unreferenced server images are not scanned.

Timeout is no longer described as offline: hover the compact status icon for the specific outcome. HTTP errors retain their status. The server's existing FTS5 keyword shortcut now also handles single words before loading the semantic model; `recursive` returned in about0.6seconds in the verified live request. Semantic fallback can still take longer. No retries or status polling were added.

### Math rendering (0.3.4)
Responses are requested in Obsidian's `$...$` inline / `$$...$$` display math format. Existing and new assistant replies using TeX `\(...\)` / `\[...\]` are converted at render time before the existing Obsidian Markdown/MathJax renderer. Code spans/fences, existing display math and stored chat text remain unchanged. No separate math library, server call, watcher, timer or disk cache; conversion runs only when chat messages render and skips messages without those delimiters.

### Clickable citations (0.3.5)
The chat message area delegates internal-link clicks to Obsidian's openLinkText API using the reply's source path. PDF page, note, heading and block targets retain their normal link syntax. Cmd/Ctrl-click and middle-click use native pane selection; external links keep normal handling. Two view-lifetime listeners, no per-link watcher, timer, dependency or history rewriting.

## iPad support (0.3.7)

Desktop keeps its native read-only Codex process. iPad runs the same vault tools, source budgets, image checks, role, citations and chat history; the private Tamestorage bridge runs the native Codex process on demand. No iPad model/index or idle process. A question polls the bridge once per second while active and handles tool calls through Obsidian's native HTTPS API. Keep Obsidian in the foreground; backgrounding/disconnection stops the server job after 35 seconds without a heartbeat. Stop cancels the exact isolated job. One bridge question at a time; five-minute maximum.

The dedicated bridge key and model choices are published from Mac in plugin data, alongside existing settings/history. Codex account credentials remain in the server's private CODEX_HOME and never enter the vault/iPad. Native search helper is bundled in main.js so standard customization sync transfers it. Network failures retain native search results and never edit notes. Mobile opens chat in a normal tab. No automatic request is made to start Codex on loading the plugin.

Server implementation: /srv/docker/tamestorage/routes/obsidian_mobile.py and lib/obsidian_mobile_runner.py. Runtime/auth/assets are NAS-only in /srv/tamestorage/.tamestorage_state/obsidian-mobile; no extra always-running service.

## Search ordering, filenames and PDF occurrences (0.3.9)

Native Obsidian matches come first, followed by literal server text/filename matches, then semantic matches. The existing sort order applies within each group, using the native renderer/virtual scrolling. Late native matches take precedence and restore native click behavior. Existing native file paths are excluded from the bounded supplementary request so they do not consume extra result slots.

Literal PDF hits are labeled **PDF text**, with indexed occurrence counts and page-specific expandable snippets. Overlapping index chunks are deduplicated. Counts describe indexed extracted/OCR text, not a visual verification of each mark. Up to48 snippets per PDF and a bounded overall response are rendered; the full count is retained when sampling. A deliberately bounded very broad count is marked ≥. Native result counts include the additional matches. Each snippet opens its own PDF page, including keyboard navigation.

Filename semantics reuse one title-only vector per supported note/PDF in the existing server index and vector mirror, including files without body text. Body edits keep that vector; new/renamed files are updated by existing indexing. **Filename** identifies a literal name hit; **Semantic filename** identifies a related name and opens the file without reading its body on this device. A filename is navigation, not evidence that its contents support a claim. Native Search explicitly requests semantic candidates even when server keywords also match; chat/file tools keep the existing fast literal path. Offline/busy Cyborg leaves native and available keyword results intact.

No extra idle timer, observer, local model/index or persistent search-result cache. Temporary sort/count/render hooks are guarded and restored on cancellation/unload/native takeover. Regression: tests/native-search.test.cjs; server test_search_results.py and test_vectors.py.
