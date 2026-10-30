# Cyborg AI Grammar

Optional local English grammar checking. Off by default; no automatic startup.

## Controls

In Mac Obsidian: Settings → Cyborg Grammar → On / Off / Status, or the command palette entries with those names. The tiny companion only opens SSH when clicked; it never starts the service at Obsidian startup. On Cyborg: app menu/Desktop → Cyborg AI Grammar (toggle; right-click actions On/Off).

From a terminal:

    ssh 100.66.32.22 ~/.local/bin/cyborg-grammar on
    ssh 100.66.32.22 ~/.local/bin/cyborg-grammar off
    ssh 100.66.32.22 ~/.local/bin/cyborg-grammar status

On Cyborg, omit SSH. Connect/mount the dedicated HDD first. Service startup checks both the exact mount point and UUID A87A-15B6; it must never create a substitute model folder on the internal disk. Off stops the bridge/model process groups, freeing their CPU/RAM/VRAM. Enabling starts the runtime; the model loads on the first check and unloads after3minutes without requests. A cold HDD check can be slower.

## Storage and implementation

All substantive installation and runtime data lives under:

    /run/media/tameszaza/New Volume/Cyborg Grammar

- runtime/: official Ollama0.40.4 binary/libraries, CUDA13 backend, no standard system installer.
- models/: qwen3:8b Q4_K_M weights and manifests, SHA256 validated by Ollama.
- bridge/: MIT Grammar-LLM-Bridge source pinned ba659e7cdbeb8d6dec66944464da250613f3e7f3, published prompts, structured schema, text/markup mapping and span validation.
- mode.py: thin adapter; no new grammar engine/prompt. Uses native Ollama API with thinking off and published JSON schema, serial model calls; builds contexts before the upstream UTF16 conversion so emoji spans/context remain correct. Formula ranges independently filtered.
- packages/, requirements.lock: only required FastAPI/Uvicorn/httpx/Pydantic/dotenv dependencies. No auth database/SDK/test runner stack loaded. Imports/bytecode, Ollama home data (via the small ~/.ollama directory link), CUDA cache and temporary files point to the HDD. The exFAT disk cannot hold symlinks; official library aliases were materialized as regular files, CUDA12/Vulkan removed after CUDA13 detection.
- home/, cache/, tmp/: current runtime state. Do not mistake this live application directory for disposable setup/temp. Remove only completed setup downloads and diagnostics.

Only a small ~/.ollama directory link, CLI, .desktop entries, environment configuration and two user systemd unit files are on Cyborg's internal disk. Existing system Python/NVIDIA drivers reused. No Docker/desktop model app/CUDA toolkit or large Mac installation.

## Existing LanguageTool and availability

Mac LanguageTool Integration keeps its existing source/plugin/UI/settings, except custom endpoint:

    https://server.tailb7f57c.ts.net:9443/grammar

The main server's existing Caddy routes /grammar/* to an on-demand localhost8096 systemd socket. Each short-lived standard-library Python worker gets normal LanguageTool first and optionally asks Cyborg100.66.32.22:9019 for extra suggestions. Any unavailable/busy/invalid/timed-out AI response preserves normal LanguageTool. Existing matches win overlaps. Model raw API stays localhost11434 on Cyborg. Tailnet-only HTTPS; no public port/Funnel.

The main server has no resident grammar gateway Python process/model while idle: one worker per accepted request, max8,64MiB/no swap/50%CPU/95seconds. No active probes, polling, queue worker or recurring cleanup. Mac companion has commands/settings only and clears the existing10-entry manual-check cache before manual checks so changing AI availability is respected. Auto-check retains its existing changed-text path and3second delay. No automatic corrections or file rewrites.

## Practical limits

AI is configured for English and inputs up to6000logical characters per request, using published paragraph/recursive chunks. Longer/non-English/busy/failed checks retain LanguageTool; select a smaller passage for AI. Suggestions are advisory; exact accuracy is model-dependent. Tests exercise tense/context beyond existing rule checks, correct text, formula protection and emoji/markup offsets. Existing LanguageTool spelling/rules remain available even with AI on.

## Maintenance

    systemctl --user status cyborg-grammar cyborg-grammar-model
    journalctl --user -u cyborg-grammar -u cyborg-grammar-model -n30 --no-pager

Both are manual user services; never enable boot/login startup without the user's request. cyborg-grammar.service requires the model service; PartOf means restarting the bridge also restarts the model. Finish a model download before restarting these units; interrupted Ollama transfers can have truncated resume metadata. No upgrade/download is performed when toggling mode. Do not run the normal Ollama install script: it installs internally and enables a system service. For changes, stop mode, maintain the external runtime and test fallback/mount absence/UTF16/markup/math before restoring user's selected mode. Preserve unrelated speaker/fan/desktop/audio services and primary documents.

## Verified deployment, 2026-10-15

Model SHA256 validation succeeded,100% GPU offload (~5320MiB). Correct capacitor sentence returned no errors; tense see→saw detected, native LT subject/verb check retained without duplicate. Fixed published array-prompt/object-schema mismatch by adapting only schema/envelope; prompts unchanged. Emoji/markup UTF16 spans and AI formula protection verified in real pipeline and Mac plugin. Warm sample2.5–3.9s; cold initial load~54s. Mac On-from-Off/Off controls and normal fallback passed. Final AI mode OFF/automatic startup disabled, no model process. Normal fallback sample0.41s. Full handoff in AGENTS.md/server manual.

Manual Check Text refreshes existing visible results after switching modes; autocheck runs when text changes. Model suggestions remain advisory and can still be wrong.


## AI grammar correction-loop repair (2026-10-15)

Reproduced AI Picky was→were then native LT were→was. Gateway now uses existing published conservative/default mode for AI only; native LT retains requested Picky/settings. gateway.py imports validation.py: after native overlap filtering, validate best AI replacements together in an in-memory markup/UTF16-preserving copy using one extra localhost8010 request only if edits exist. Reject whole candidate batch on new native warning or incomplete/failed/malformed verification; preserve original baseline. Rejecting whole batch avoids interdependent partial corrections. Bounds24edits/24000UTF16input/2000replacement. No extra model inference, persistent note history/cache, background timer/service or Mac changes.
Verified live correct was0, wrong were→was, recheck corrected0; correct list/capacitor0; useful AI see→saw and native agreement retained. Emoji/markup/math offset and AI-disabled path pass. Isolated injected regressions and incomplete/malformed safety pass. Native rules miss some AI false positives (stores→store, one→a one); validation does not guarantee AI accuracy. Conservative mode removed these in real fixtures. User mode already ON, preserved; autostart remains disabled. Existing workers naturally reload source next request; no restart. Use Mac manual Check Text to clear old displayed/cached results. No note/source document edits; staging removed.


## Qwen3.5 9B grammar replacement (2026-10-15)

- User selected Qwen3.5 9B. Official qwen3.5:9b-q4_K_M downloaded via existing local Ollama0.40.4 to Cyborg external HDD models path; completed official SHA256 verification before service restart. Tag digest56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90, total6,550,825,550bytes. Weights02d45dc1cf451ba2475ac33b301c2dd8f985abe4c182ce04a1f2f5bf0260278d; official bundle also includes projector. No runtime/driver/package upgrade or Mac model installation.
- Cyborg mode.py imports existing app.config.LLM_MODEL for both inference and health instead of duplicating hardcoded model names. ~/.config/cyborg-grammar.env now LLM_MODEL=qwen3.5:9b-q4_K_M. Existing published prompt/schema, high-confidence AI/default mode, native LT Picky/settings, server validation, UTF16/formula safeguards, context6144/1024output/serial/no-thinking/3min unload unchanged. No new service/polling/cache/index.
- Same full TVM sample: old8B falsely suggested Main idea is→are; new9B returned0matches. Factual I was, hypothetical If I were, head-noun agreement, correct capacitor and lecture fragments clean in tested requests; real see→saw and native She go→goes retained. Emoji/markup/formula test preserved AI see span at JS offset15. Warm sample0.72–3.3s, first cold21.65s. GPU fully offloaded per API size_vram==size5,581,454,704bytes; NVIDIA6350MiB total used, bridge~44MiB RAM/model host cgroup~871MiB sample. Measurements are samples, not guarantees.
- IMPORTANT accuracy limit: a separate correct two-sentence recheck (Yesterday I saw a car. She goes to university every day.) produced an unnecessary person-changing She→I/goes→go rewrite. Model is more useful in the TVM test but has not been proven generally better/Grammarly-equivalent or free of false positives. Do not silently rewrite note facts. Experimental think=true exhausted2048tokens in59.27s with empty answer; kept no-thinking for interactive use. Second experimental request cancelled by authorized service OFF before restoring ON; no experimental configuration retained.
- Actual controls OFF stopped model/bridge and main endpoint returned native LT in0.31s; ON restored new model/health and live hybrid request passed (suggested optional The main idea, no erroneous are). Mode was ON at arrival and remains ON, autostart disabled. Official DELETE removed superseded qwen3:8b and unreferenced weights after new-model verification. Only new model listed;0partial/pending downloads, no backup/test staging retained. Final full external application allocated8.0GiB. Main-server code and Mac plugin code unchanged. Refresh stale visible suggestions via LanguageTool Integration: Check Text. Latest section supersedes historical8B descriptions above.


## Grammar gateway premature timeouts (2026-10-15)

- User reported frequent LanguageTool503. Live Mac endpoint remained private9443/grammar with auto language/Picky/3s delay. Native LT and both Cyborg units healthy; no Cyborg restart/OOM evidence. Main native logs at12:42 showed15-character auto-detected ca-ES checks taking29.864s and25.355s, while old gateway baseline read deadline was6s; corresponding gateway workers returned/ended after~6–7s. Native eventually returned200. Short health/English checks did not expose this cold-language timeout. Do not assume every503 is GPU failure or solve by restarting unrelated services.
- Main server gateway.py now allows45s for baseline check. Per-request monotonic85s budget reduces/skips optional AI to leave6s verifier reserve and headroom under unchanged95s systemd lifetime; AI/verification errors preserve original successful baseline. Native timeout is distinguished as504, other baseline unavailability503. Error-only diagnostics log class/elapsed, never note/request/credential/exception contents. No retry/polling/daemon/dependency, Mac/Cyborg changes or user delay/language preference changes. Existing socket workers load updated script on next request; atomic source replace, no restart.
- Isolated actual7s HTTP baseline passed (old6s limit would time out); simulated30s baseline preserved200 for busy/timeout/disconnect/invalid AI, exhausted AI budget skipped model, verifier timeout retained baseline, genuine native timeout504. Live private endpoint short ca-ES auto200~0.89s, English AI200~2.65s with see→saw, AI-disabled native200~0.42s. These are samples;45s still bounds genuine prolonged native failures. Source syntax verified, staging/fixtures/pending deployment removed; no backup retained. Updated server runbook/README and Mac/Cyborg AGENTS.


## LanguageTool native heap exhaustion behind504 (2026-10-15)

- User reported504 after the gateway deadline fix. New error-only gateway log identified TimeoutError at45.05s; actual native LT Docker logs at~12:56–12:57 show java.lang.OutOfMemoryError: Java heap space in HTTP dispatcher/checker/monitor threads, a13-character health check taking59999ms and another request77362ms. Container eventually restarted. This is a deeper native-server failure; increasing gateway timeout alone was insufficient. Cyborg AI was not the failure source. Preserve distinction between gateway premature timeout and native OOM.
- Existing /srv/docker/languagetool/compose.yaml had no custom env/limits: image default -Xms256m -Xmx512m,10check threads on2-core3.7GiB host. Updated same Compose service/image (no pull/version change): Java_Xms128m,Java_Xmx1024m, JAVA_TOOL_OPTIONS=-XX:+ExitOnOutOfMemoryError; maxCheckThreads2/maxTextCheckerThreads2; cacheSize0,pipelineCachingfalse,pipelinePrewarmingfalse explicit. Docker mem_limit1536m/memswap_limit1536m bounds totalcontainer and permits no additional container swap. Maxheap is a ceiling, not idle reservation. Existing unless-stopped restart and healthcheck retained; JVM fails promptly on future heap OOM for Docker recovery. No watchdog/timer/new daemon/dependency/Mac/Cyborg plugin change.
- Compose validation passed and recreated only native languagetool with --no-deps --pull never. Image unchanged sha256:ef8fa12cbd485166c9ceeb7139d76d56d07707a624da6bb1fc1fbb5411750527. Config writable by tameszaza, no sudo workaround. Native logs confirm applied thread/cache settings and JavaExitOnOOM. Other containers/Cyborg/manual startup preserved; gateway45s baseline/85s total budgets retained.
- Live simultaneous English+AI and cold auto-Catalan passed200 (14.62s/20.76s including startup/queue); warm native2000.42s and Englishauto+AI2001.04s. Healthy/restartcount0 after recreation, JavaRSS532864KiB + fastText138740KiB (~656MiB total); host~2.2GiB available during test. Samples, not long-duration/worst-case proof. No injected real OOM or recurring monitoring. No retained backups/staging. Server manual/AGENTS/README and Mac/Cyborg handoff updated.

Citation cleanup completed in same turn: Lecture Chat0.3.5 actual PDFpage25 + annotation-note clicks verified; two diagnostic tabs removed; current6saved messages retained, debug9229closed, normal Obsidian reopened, /private/tmp/lecture-citation-fix removed. No note/annotation edits or generated AI test replies.


## Honor Picky Mode with Qwen3.5 (2026-10-15)

- User reported missed contain→contains in SC1005 TUT7 prose. Exact passage reached both native LT and AI withHTTP200; native and forced-default Qwen returned0matches. Note has no lt-ignore/frontmatter disable; global user setting pickyMode=true. This was missed detection, not service failure/UI transport. No note content changed.
- Prior8B false-positive workaround in main gateway always forced ai_params.level=default. With new9B direct upstream picky catches contain→contains. Gateway now uses dict(params), forwarding user's selected default/Picky level to AI as native LT already does. Uses existing published prompt's mode block; no custom sentence rule, role rewrite, model/temperature/thinking/context/token change or extra inference stage. Default mode remains selectable via ordinary plugin Picky toggle. Higher recall may bring more optional/style suggestions; cannot guarantee error-free output.
- Live gateway tests: original passage flaggedcontain→contains; same Obsidian-style annotation/wikilink request languageauto yielded exact original offset95 and correct replacement. Correct factual Iwas staysclean, previous TVM gets optional The main idea but no incorrectare, correct two-sentence Shegoes remainsclean because existing native validator filters model's erroneousgoes→go. Other head-noun/plural agreement clean. Native LT remains limited; AI validation stillbounds/fallback45s/85s and resource/manual preferences unchanged. No files/downloads/backups/staging added; README/server manual/Mac+Cyborg AGENTS updated. Existing displayed results refresh on manual Check Text or a normal edit/autocheck.


## Punctuation checks and larger AI passages (2026-10-15)

- User requested stricter punctuation without damaging Obsidian syntax. Cyborg TYPOGRAPHY_CHECK=true (GRAMMAR_ONLY remains false); Mac LanguageTool settings preserve Picky=true and enable PUNCTUATION. Existing published prompts/model/schema reused; no new grammar role, custom sentence rule, extra model pass or daemon. Native/AI punctuation coverage remains incomplete: possessive apostrophe and double punctuation caught, but tested introductory/conditional commas and well-known hyphenation were sometimes missed. Do not promise exhaustive punctuation detection.
- Main gateway validation.py protect_markup reuses Obsidian data.annotation text/markup boundaries for native matches, AI candidates and native verification. Rejects protected markup/wiki targets+aliases, syntax-marker changes, new line-start lists/rules, invalid UTF16 spans and changes spanning formatting markers. Prose commas, apostrophes, quotes, ellipses, hyphens and parentheses remain allowed. No new Markdown parser or Mac computation. Fixture boundaries passed; live annotated request retained Johns→John's, isnt→isn't and double-period suggestions with no YAML/heading/list/wiki/code/math/URL edits.
- User extended AI input cap from6000 to24000 logical characters (characters, not letters). Cyborg mode.py enforces new cap; existing recursive chunker now3000 characters, overlap60, threshold3500, serial model_slot, same6144 context/1024 output/no-thinking/75s request bound. More context per chunk and fewer calls than old1000 chunks. Main native-validation raw UTF16 limit96000 permits markup overhead; max24 AI edits, transport262144bytes and existing gateway85s budget remain. Longer notes can take longer or fall back to native LT on time/busy/validation failure; cap is not a guarantee of completion or correctness.
- Live10753-character whole-note request returnedHTTP200/hybrid in14.51s and AI correction Yesterday I see→Yesterday I saw at offset10731, beyond old cap. AI mode retainedON/autostart disabled; no model/runtime download or Mac storage/model/index added. Server socket workers load source on next request; only Cyborg grammar service restarted for config.


## Cyborg note organization and writing previews (2026-10-16)

- User authorized implementing on-demand organization/writing with preview, comparing local models on preservation/speed, and clarified that responses under1minute are acceptable (supersedes earlier10–30s target). RTX4060Laptop8188MiB,64GBRAM (61GiB reported), i5-12450H verified. Keep Mac overhead minimal, models/runtime on Cyborg dedicated HDD, no new idle/recurring jobs or autostart.
- Official gemma4:12b-it-qat downloaded through existing Ollama to external models; official SHA256 verification completed before service restart. Both Qwen3.5 9B and Gemma4 12B QAT retained as selectable manual preview models after latency clarification. Qwen initial fast default, grammar remains Qwen. Existing one-model-loaded policy/3min unload/manual controls retained. No model downloaded to Mac, no second Ollama service.
- Sample HTTP wall times: factual rewrite after model unloadQwen7.09s/Gemma55.88s; correct prose warm2.01/4.08; final organization15.85/35.92; English→Thai warm2.86/8.58. Both preserved tested quantities/negation/protected equation/code/wiki link; correct prose unchanged; final organization every block once. External model loading included, OS file cache not flushed; no guarantee of physical cold-disk speed/subminute long notes or universal model superiority. Full small report in Mac plugin model-comparison.md; no actual note excerpts retained in report.
- New external Cyborg assistant.py installed beside mode.py. Existing FastAPI app registers /v1/assist, reuses HTTP client/Ollama/model_slot. No new listener/daemon/dependency. Allowed installed models only, actions organize/rewrite/translate/explain; input200KB, organization24kcharacters/100blocks, writing12kcharacters, inference3072output tokens/no-thinking/temp0/context8192. Bounded15s slot wait/60s total; model lock serializes with grammar without marking grammar check_slot busy for the whole manual operation. Long manual work can delay optional AI grammar; native LT fallback preserved. Grammar context aligned8192 to share Qwen runner with manual tools instead of reloading on context switches; other grammar chunking, Picky, punctuation and validator unchanged.
- Main server existing socket gateway adds /grammar/v1/assist -> Cyborg9019/v1/assist (80s forwarding), same request workers/64MiB/95s lifecycle/Caddy route. No new system unit/port/resident server Python. Native /v2/check untouched apart from previous context setting on Cyborg. Simultaneous manual/grammar requests verified after integration.
- Mac existing cyborg-grammar plugin now named Cyborg AI1.1.0 (ID unchanged), adds assistant.js/styles.css plus settings and comparison report. Palette/right-click editor commands Organize this note, Improve selected writing, Translate selected text, Explain selected grammar. Small two-column Original/Proposed modal, model selector, optional instruction/target language, explicit Preview/Apply. No OpenAI/Codex involvement, cloud upload, cached note corpus, new index, startup request or automatic edit. Optional candidate discovery reuses existing server semantic search plus existing filenames/resolved links only on organization request. Related targets include actual Markdown notes and PDF pointers, exclude current/hidden/recovery files.
- Organization parses current editor snapshot through existing Obsidian metadata worker computeMetadataAsync(ArrayBuffer), avoiding stale saved positions or a new parser. Preserves frontmatter separately. Dynamic JSON schema requires every original block ID as a placement property; validates group assignments, then mechanically assembles exact source blocks. Empty new titles valid to retain existing headings. Optional tags/links/summary toggles startOFF; related links restricted to existing candidate files and rechecked before Apply. No model-generated original prose in organization.
- Writing/translation mask YAML/math/code/wiki/Markdown links/URLs with reversible placeholders, enforce unchanged token order/count then restore exact originals. Prose semantic accuracy still requires user preview. Apply rejects any intervening source edit and uses normal editor replaceRange/Undo. Explain has no Apply. Stop/Close/unload aborts Mac requests and releases preview data; remote computation can finish within deadline, no immediate GPU-cancellation guarantee. Existing LanguageTool cache wrapper/manual modes preserved; no new paste/typing interception. Upstream LT plugin untouched for these additions.
- Actual Obsidian tests passed selection-only application, stale-preview refusal, mechanical organization/frontmatter/exact-block preservation, missing block rejection, native context-menu construction, metadata worker parsing, and live HTTPS writing/organization previews (3.6s/5.4s native generation samples) without source changes. Server fixtures tested protected roundtrip/mutation rejection, required block schema and invalid group/link refusal. Prototype organization schema/strict-heading failures corrected and both final models passed.
- All source/README/handoff files retained as current maintained implementation. Temporary fixture notes/views/helper exports/CDP/staging/benchmark outputs removed after testing; normal app reopened/debug closed. Flush Markdown view save before deleting scratch files: an outstanding debounced save initially recreated one scratch note, which was detected and removed. No primary note/PDF/annotation/chat edits, troubleshooting backup or recurring cleanup.


## LanguageTool latency and effective swap repair (2026-10-17 Singapore)

- User requested fixing repeated504 and asked why Cyborg AI did not respond. Re-read new shared Server tasks/GPU/thermal handoffs. Live admission was accepting=true and grammar+model units active, Qwen9B loaded; retained live user state without changing On/Off, guard, fan, clocks, transcription queue or autostart. Health alone is insufficient: actual hybrid and assistant calls verified below.
- New native LT logs showed a3816-character auto-English request taking68.179s while gateway timed out45.03s; other similar checks3.358s/6.118s. No current OOM or container restart. Pre-fix short hybrid2.82s, auto hybrid71.89s. Main LT cgroup actually had406,286,336swap bytes and memory.swap.max=max despite Docker inspect/Compose equal1536m RAM+swap settings. Earlier claims of enforced no-swap were incorrect for that observed old cgroup.
- Updated only /srv/docker/languagetool/compose.yaml: mem_reservation768m (memory.low805306368; protects existing hot pages under contention, not preallocated RAM), retained mem_limit/memswap_limit1536m and Java128m/1024m/ExitOnOOM/2checkthreads; enabled native sentence cache256/TTL300s, pipelineCachingtrue/maxPipelinePoolSize2/expiry300s, prewarmingfalse. Uses existing upstream caches/pipelines rather than custom retries/watchdogs. Native cache is bounded, in-memory only; no disk corpus/model on Mac. Official config reference https://github.com/languagetool-org/languagetool/blob/master/languagetool-server/src/main/java/org/languagetool/server/HTTPServerConfig.java and resource semantics https://docs.docker.com/engine/containers/resource_constraints/ .
- docker update --memory-reservation768m --memory-swap1536m first applied effective swap.max0. Validated Compose then recreated ONLY languagetool with --no-deps --pull never, same image sha256:ef8fa12cbd485166c9ceeb7139d76d56d07707a624da6bb1fc1fbb5411750527; no package/model/image update. New cgroup readback memory.low805306368, memory.max1610612736, swap.max0, swap.current0, no max/OOM events. Sample memory.current766898176 (~731MiB), healthy/restartcount0. Future checks must inspect effective kernel values as well as Compose/inspect. No host global swap/sysctl change.
- Tests: native initial cold English14.74s, edited English0.08s (2errors→1 after correction, remaining error retained), ~4k auto0.27s, all200/incompletefalse. Actual Mac private9443/grammar auto+AI1.80s detected LLM_RULE contain→contains; whole-note+AI2.21s/hybrid/nativeerrorsretained; manual /v1/assist rewrite1.21s yielded explain instead of explains. No note edits. These samples verify current service path, not an unlimited-load or long-duration latency guarantee. Small native+health concurrent requests also completed in inspected logs without queued backlog.
- Gateway45s/85s budgets, native/AI protected-markup validator, Mac plugin/Picky/autocheck3s, shared controls and other services unchanged. No extra resident process/timer/poller/dependency/download/staging/backup. Use LanguageTool Integration: Check Text in Obsidian to refresh existing hints; backend change needs no app restart.


## Cyborg manual Gemma preview timeout repair (2026-10-17)

- User clarified failure is Writing/Organize preview, not grammar suggestions. Live Mac plugin model selection gemma4:12b-it-qat; admissionOn/bridge+model active. Captured real Gemma model log generation~16tokens/s progressing to441tokens, then /api/chat HTTP500 after59.999s with task cancelled. Existing assistant.py hard60s bound included cold model loading, explaining failing larger/cold previews despite healthy service and successful Qwen checks. No grammar endpoint or GPU failure inferred from that timeout.
- Manual-only bounds now Cyborg assistant240s total including slot wait, explicit per-call httpx timeout=remaining (otherwise shared client's70s cap would still apply); gateway/v1/assist250s; Mac HTTPS helper260s; existing on-demand systemd gateway worker RuntimeMaxSec270s. Native grammar45/85s and Cyborg grammar75s remain unchanged. Worker memory/CPU limits, socket activation/noidleprocess, same model lock, context/output/input limits, preservation validation, AI mode/admission/autostart/guard/fan/preferences unchanged. No automatic fallback/model switch/new dependencies/models/downloads/jobs; Gemma selection preserved. User's subminute preference is a latency target, not an artificial request-kill limit. Larger Gemma output can exceed a minute; Qwen remains faster selectable option.
- Updated only external Cyborg assistant.py, main gateway do_assist forwarding/comment and existing root worker lifetime, Mac cyborg-grammar/assistant.js request deadline plus one Gemma working-status message. Restarted existing Cyborg main service to load backend; PartOf also restarted model, left bothactive/admissionOn as found. Secure Mac sudo prompt fed SSH stdin to edit existing worker+daemonreload; no password stored. Cleanly quit/reopened Obsidian normally to load helper; no forced quit, debug port or note edit. Syntax checks passed.
- Real private HTTPS Gemma cold rewrite passed200/57.48s (model load9.95s,628tokens), preserving16paragraphs and exact equation/wiki link. A larger beyond60s fixture is checked separately below. No source note used/rewritten by diagnostic fixture. Stop/Close still aborts Mac request; an already-started remote computation can continue to its now240s bound, not immediate cancellation.

- Final beyond-cutoff verification: real Mac→private HTTPS→socket worker→Cyborg Gemma rewrite completed HTTP200 in108.30s,1162outputtokens. All30testparagraphs, exact equation and wiki link retained. This passes both old60s model and95s worker limits. Request workers exited afterward; native LT healthy/restartcount0. No fixture note/files/backup/staging or automatic request loop created. Existing selectedGemma kept; generation was~11–16tokens/s, so longer previews cannot be promised under1minute.


## Independent overlapping grammar suggestions and exact Ignore (2026-10-17)

User reported ignoring a sentence-wide LanguageTool warning also hides an inner
Cyborg AI correction. Found two independent causes: gateway combine() discarded
all overlapping AI spans before client ignore state was applied; plugin Be's We
handler removed all overlapping decorations, and ignored/dedup identities used
only from/to. These earlier blanket native-priority/range-only behaviors are now
superseded.

Main server gateway.py combines distinct actions despite overlap. Only identical
span plus a shared replacement is deduplicated, retaining the existing/native
suggestion. Existing markup protection, native verification, batch checks,
availability fallback, request budgets and no automatic text changes remain.
New maintained test_overlap.py beside gateway.py has six passing cases: broad
warning/inner AI, independent same-span actions, duplicate action, validation
inside a broad warning, new native regression rejection and markup protection.
Socket workers read the atomic source replacement on the next request; no restart.

Mac obsidian-languagetool-plugin/main.js Ignore now carries the selected match.
Identity includes rule/category/message/replacements in addition to mapped
positions. Ignore removes only that exact hint; independent nested/same-span
hints survive, including a full recheck. Actual text edits still invalidate
intersecting hints. Tooltip/navigation/keyboard selection prefer the narrowest
hint; same-span tooltip reuse also checks identity. Eight isolated real CodeMirror
state checks passed (including emoji position mapping and post-accept clearing),
without creating/editing a vault note. Maintained regression fixture in plugin
`tests/overlap-regression.js`; source syntax checked by system JavaScriptCore.
Upstream updates may overwrite local main.js adaptations. No new timer, network
request, model pass, cache, history, dependency or idle computation.

Separate screenshot investigation: exact two-sentence iPhone/Cyborg transcription
paragraph returned0matches from native LT and direct Qwen AI; real private gateway
returned200/hybrid0matches in1.09s. English en-US/Picky/PUNCTUATION and Cyborg On
confirmed. This is missed detection, not network outage or editor suppression.
Published prompt is conservative; no case-specific rule/prompt/model switch was
added. Do not claim no matches proves grammatical prose or parity with QuillBot.
Another live private gateway fixture returned200/hybrid in19.2s, native comma
warning and AI see→saw, demonstrating both paths remain active. Native/AI quality
and optional word-choice false positives remain advisory, not guaranteed correct.


## Offline Cyborg (2026-10-30)
The native main-server LanguageTool response is authoritative and remains available
while Cyborg is Off, gaming in Windows or unreachable. Optional AI first uses a bounded
non-inference health request (0.35s connect/0.6s read), preventing a blackholed AI endpoint
from consuming the long inference deadline. Failures retain native matches and HTTP200;
headers distinguish native/hybrid mode and AI availability. No service start, local LLM,
Java/model/rule setting change or background polling. Actual English error/control
requests returned native200 in2.52s/0.82s while Cyborg was unavailable (sample timings).
