# How the mesh works

Observed source/configuration snapshot: 2026-10-30. Availability checks are observations, not an uptime guarantee.

## Transport, storage and trust

Tailscale connects the Mac, iPad, main Ubuntu server, Cyborg and room controller. Tailnet access and application authentication are separate layers. Existing Serve/Caddy routes expose the website, Goodnotes WebDAV, CouchDB, calendar, music and search/grammar endpoints. [Caddy configuration](../deploy/Caddyfile) and individual compose files show backend routing. The website does not receive an unrestricted Docker socket.

Primary documents, queues, SQLite caches and service state reside on the main server's external NAS under `/srv/tamestorage`. Runtime paths are not source. The existing scheduled backup and disk-recovery units are in [deploy/systemd](../deploy/systemd). A device reconnect can change Linux's device number: OCR cache adoption verifies the configured filesystem UUID, inode, length and modification time before preserving derived data. The existing recovery timer's stale-mounted-ext4 shutdown case is not claimed fully solved by this source publication.

Finder's root maintenance share reuses Samba over the existing LAN/tailnet route. Share authentication maps to the ordinary server user, never a root override. Root directory visibility is broader than the regular Storage share; file permissions still determine which files can be read/written. No Mac polling process or login task is needed.

## Goodnotes → Obsidian → searchable knowledge

1. Goodnotes Auto Backup uploads exports through WebDAV. This is one-way backup, not two-way notebook synchronization. A PDF uploaded on the server does not magically import onto iPad.
2. The [Goodnotes watcher](../services/goodnotes-watch) waits for stable uploads, records origin/presence and consumes validated full-library backup inventories. Manual/server-origin files remain protected. Confirmed absence and complete replacement evidence drive cleanup; filenames such as `(1)` alone are insufficient evidence for deletion.
3. [Goodnotes links](../services/goodnotes-links) publishes small managed PDF pointers through LiveSync. It removes only matching generated pointers after deletion, preserving edited/unmanaged notes. Personal Obsidian references remain normal links, with shortest-link preference in the client. Native filename moves are not promised to retarget unrelated personal links automatically.
4. [Lecture search](../services/lecture-search) reuses existing PDF/Goodnotes text. Missing text is queued for OCR once and cached until the source changes. Pasted-image OCR uses the shared confidence filter; photographs with unreliable text can be skipped. Searchable PDF derivatives receive invisible text without replacing originals or changing rendered pages.
5. Lexical/PDF exact results remain distinct from semantic results. The Obsidian plugin appends server results after native matches; filenames participate too. Search remains usable locally if the server is unavailable. OCR alt-text content stays hidden in normal rendering; plugin code handles Markdown escaping and editor presentation.
6. [Quartz](../services/quartz-site) exports notes and applies the existing publication policy. A build is completed before the served site switches atomically. Failed/offline builds retain the last published site.

Successful Goodnotes backup ZIPs are consumed and removed after sync. The upload folder itself is protected from the general temporary-file cleanup worker. Do not add a second cleaner that conflicts with provenance or unfinished queue checkpoints.

## Job placement and resource priority

The Mac is a thin client. CPU helper jobs prefer ready, accepting Cyborg on its external disk. The [helper client](../services/lecture-search/helper_client.py) uses short cached health checks for background routing; it does not start remote services just to poll them. Off/busy/offline causes essential PDF/image processing and Quartz builds to run locally on the main server through existing implementations. Shared nonblocking admission locks prevent several expensive fallback jobs running together. Completed checkpoints and pending vector-sync debt survive interruptions.

AI inference stays on Cyborg. A Windows gaming session or Server tasks Off must not trigger an attempt to revive Linux or move its LLM onto the main server. Optional AI grammar readiness is checked cheaply; native LanguageTool results remain available when Cyborg is offline. Separate manual services and automatic startup settings are distinct. Current native Cyborg inference settings were not revalidated while it was unavailable.

[Goodnotes statistics](../services/goodnotes-stats) is lower priority. It reads vector geometry, never invokes OCR/LLMs, and is limited to a quarter CPU and 192 MiB/no extra swap. It checks OCR/index/fallback locks and the web-build marker before/during a page, cancels unfinished page analysis when essential work starts, and keeps completed cache entries. It also defers under memory/load pressure. Stroke counts from vector exports are estimates; distances describe PDF geometry, not measured hand travel.

## Website, assistants and clients

Flask/Gunicorn handles authenticated file operations, shares, previews and the Assistant. Assistant tool execution and durable jobs live in [lib/workspace_agent.py](../lib/workspace_agent.py) and related `workspace_*` modules. Multi-image messages preview attachments before sending. Diary logging stores images in the existing server image mechanism and writes timestamped Markdown to today's diary through the existing LiveSync path. User intention determines the requested analysis; no automatic replacement of original notes is implied.

[Lecture Chat](../clients/obsidian/lecture-chat) reads the current document/page as context, can inspect relevant other vault files, renders citations and math, and separates retrieval constraints from explanation. It is a client of a selected provider, not an autonomous source of lecture facts. [Tames PDF](../clients/obsidian/tames-pdf) implements transparent text boxes, pins/regions, region links/embeds and source-specific annotations. The published plugin files exclude saved conversations, keys and personal settings.

The native [NTU VPN app](../clients/macos/ntu-vpn) provides VPN authentication and existing remote Cyborg controls, including Server tasks and Bluetooth speaker mode. It also contains display/audio helper source. Speaker playback uses nearby Bluetooth, not Tailscale audio transport; toggling its receiver can interrupt local Cyborg audio. Native source unavailable on Cyborg is explicitly listed below.

## Calendar, media and room automation

Calendar uses a dedicated service plus its separate synchronization worker. Those two containers perform different roles. Music sync reconciles playlists and cached metadata. Public lyrics are preferred; unusable/near-empty lyrics can fall back to queued Cyborg transcription when available. Thai metadata remains excluded from that fallback. Instrumental classification and uncertainty review preserve the distinction between unavailable text and a confirmed instrumental; AI lyrics retain their Cyborg source label and timestamps. [Music routes](../routes/music.py) and [playlist worker](../playlist_worker.py) show the queue/review mechanics.

Videos and transcription use durable queues rather than keeping browser requests open. The main server dispatcher retains jobs while Cyborg is unavailable. Original transcription uploads use the existing inbox cleanup policy; unfinished jobs/checkpoints are not incidental temporary files.

Room aircon automation runs through the existing phone/Termux/HTTP/MacroDroid arrangement. Repository phone files explain the existing implementation; the currently running phone source was not refreshed in this update. Monitoring must not actuate aircon or other physical outputs. Native authentication/configuration remains separate from Docker control.

## Controls and observability

Admin Services exposes restricted Start/Stop/Restart/Details operations through the host Unix-socket broker. Only named containers are allowed; manual stop preferences and NAS readiness checks are preserved. [Settings](../routes/settings.py) uses the existing typed/validated host settings extension. [Activity](../routes/activity.py) polls cached, bounded read-only snapshots while the tab is visible; queue checkpoints are the source of task progress, not browser activity.

Uptime Kuma uses existing endpoint checks and a read-only Docker monitoring adapter. Monitoring availability does not prove GPU compute, successful OCR, data integrity or completed mobile synchronization. It does not load an LLM simply to prove a model endpoint is running. Goodnotes statistics has its own Docker monitor and remains visible in Admin Services.

## Native Cyborg source not refreshed

The Linux native AI model runtime/grammar process, GPU/fan/night-mode controls, Bluetooth speaker receiver and audio/transcription worker remain distinct from the Docker CPU-helper source published here. Cyborg was in Windows, and Linux/external-disk files were unavailable. Deployment/helper copies are included where present on the main server. A future Linux-online read-only source export can complete this boundary without changing gaming or service-start policy.
