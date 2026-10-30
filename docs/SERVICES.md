# Services and uptime (2026-10-18)

## Use

- Admin center `/admin`: Services has search, state filters, Start, Stop, Restart and Details. Stop/Restart ask for confirmation.
- Status: https://server.tailb7f57c.ts.net:3001/status/services
- Kuma settings: https://server.tailb7f57c.ts.net:3001/dashboard
- Native Kuma username `admin`; password is the user's chosen credential. Private operator record: `/home/tameszaza/.config/uptime-kuma/admin.json` (0600). Do not print it or copy credentials into documentation.
- Both Kuma endpoints are Tailscale Serve only. Status is viewable without a separate login inside the tailnet; Kuma configuration requires login. No external notification channel was configured.

## Monitoring

Uptime Kuma 2.5.6 slim rootless, pinned by digest, is a separate container managed by `/srv/docker/uptime-kuma/compose.yaml`. It has no Docker socket, browser, separate database server or NAS dependency. Binds only 127.0.0.1:3001 with host networking, user1000, all capabilities dropped, no-new-privileges, 384MiB RAM limit, 0.5 CPU limit, 128 PID limit, Node heap192MiB. SQLite app data is internal at `/srv/docker/uptime-kuma/data` (0700), allowing it to survive a primary NAS outage. Retention30 days, update checks off. Sample warm RAM about122MiB; this is a sample, not a guarantee.

43 service monitors plus five groups cover ten long-running Docker containers, seven application endpoints, fifteen server listeners/watchers/timers, eight Cyborg services/storage checks and three phone aircon/bridge/SSH endpoints. HTTP checks do not invoke inference or aircon actions. Socket-activated search/grammar and on-demand transcription are checked through existing systemd socket/path readiness; no dummy inference or permanent worker is launched. The completed LiveSync initializer was removed, including its Compose service/script and unused image, after verifying persistent database configuration.

Most checks are60s; background readiness is120s. Timeouts8s and one retry avoid treating a single slow response as an outage. Cyborg state uses one restricted read-only SSH command cached30s by the existing host-control process, not a remote daemon. The forced SSH key cannot run arbitrary commands or forward connections. Cyborg Speaker is available on demand: loaded normal inactive/active states count available, failed/unloaded states fail. It does not require Speaker Mode to remain On. Other manually disabled services can be paused using Kuma's native controls.

Cyborg: SSH22, existing AI health9019, grammar/runtime units, Speaker Mode control, Sunshine, GPU safeguard and external disk UUID A87A-15B6. No models started by checks. Phone tameslist: Aircon controller login HTTP5000 keyword, MacroDroid TCP8080 and Termux SSH8022. Do not probe phone `/health`: it is not a supported health route. No relay actions or phone configuration changes were made.

Maintained provision.js reuses Kuma's native Socket.IO API; wait for its `info` readiness before sending login (connection alone can race server handler registration). It reads a JSON plan from stdin, never a password argument. monitors.json holds desired inventory, monitor-map.json the provisioned IDs. Native Kuma settings remain the editing UI; rerun provisioning deliberately after reviewing live configuration.

## Restricted controls

Admin routes/services.py reuses existing Admin session and CSRF protection. An unprivileged web container never receives Docker's socket. It calls existing `/run/tamestorage-control/control.sock`; root-owned `/usr/local/libexec/tamestorage-host-control` delegates only managed Start/Stop/Restart/status/detail operations to `/usr/local/libexec/tamestorage_containers.py`. The allowlist includes all current application containers and a reserved disposable verification container. Add future application containers explicitly to this maintained allowlist and the monitoring inventory.

Read-only sanitized Docker compatibility API is on 127.0.0.1:9780 inside this same broker process. It exposes only selected GET/HEAD state endpoints; mutation methods return405. Container credentials, command lines, health logs and host-side mount paths are omitted. Bounded async action workers return an operation ID; the UI checks completion and preserves errors. No exec/remove/create/pull endpoint. List refresh15s exists only while Admin page is visible; operations poll only until done/error/timeout.

Successful Stop persists an explicit preference in `/var/lib/tamestorage-control/stopped.json`, root0600. Start/Restart clears it. Root NAS recovery `/usr/local/sbin/tamestorage-primary-recover` respects these preferences after reconnection, and NAS-backed starts require the known primary UUID and uploads directory. Do not manually erase stop preferences during ordinary recovery. Web self-restart acknowledges before stopping and reconnects; stopping Tamestorage itself disconnects Admin and requires SSH to start again: `docker start tamestorage` (check NAS first).

The root service drop-in `/etc/systemd/system/tamestorage-host-control.service.d/10-shared-runtime.conf` sets RuntimeDirectoryPreserve=yes because disk-analysis.sock shares that directory and the web bind mount must remain valid across broker restart. Preserve the existing exit-node controls. Maintained sources are in app deploy/; apply root files with authenticated sudo and restart only the broker when necessary.

## Verification and maintenance

10 focused unittest security/API cases passed (tests/test_services.py): Admin/CSRF, narrow actions, details redaction, unavailable broker, duplicate operations, failed actions, manual stop preferences and NAS guard, completed setup and read-only monitoring adapter. Desktop and390px dark layouts rendered in installed Cyborg Chrome;16 assertions per layout passed for filters, details, cancel/confirm, Start/Stop/Restart, retained warning file list, removed descriptions and fitting viewport. Actual Admin HTML/details/CSRF and real Docker Stop/Start/Restart verified against a disposable container. Kuma independently recorded UP → DOWN → UP for it; test monitor/container then removed. Real Kuma status page rendered All Systems Operational with Cyborg/Aircon groups. This establishes current readiness, not future availability or backup/note integrity.

Only web was recreated with image c0946f0c7746; existing production workers, ASR queue, data and phone were preserved. Brief disk pressure during image startup produced real transient warnings; no false history was erased. Task staging, browser profile/fixture, temporary test objects and build aliases are removed after verification. Keep maintained code/tests, real Kuma history/data, restricted SSH key and live app configuration. No backup or recurring cleanup added.


## Server settings

Admin → Settings opens `/admin/settings`. See [SETTINGS.md](SETTINGS.md) for the typed service parameters, Save/Apply behavior, current-image pins, pending state and credential handling. It reuses this restricted broker; no Docker socket or additional monitoring process is exposed to the web. Existing manual-stop preferences and shared socket directory remain authoritative.


## Cyborg external CPU helpers (2026-10-21)

Admin Services now includes Cyborg helpers, a host filter and host details. Restricted
remote Start/Stop/Restart honors the same Server tasks switch and persistent manual
stops. Local inventory is never blocked by an offline Cyborg. Existing Kuma adds
three checks (46 total); native AI/audio/phone monitoring stays intact. See
[CYBORG-HELPERS.md](CYBORG-HELPERS.md) for storage, queues, search, limits and recovery.

The 21 October web deployment retained immutable9e8c6d5c616a and added three read-only source binds for
_services.html/services.js/services.css in compose.yaml; preserve these alongside
the existing mobile bindings. No other application container was recreated.

## Assistant diary logging (2026-10-22)

See [ASSISTANT-DIARY.md](ASSISTANT-DIARY.md). Web reuses the existing encrypted LiveSync runtime and private Docker network, with on-demand diary appends and shared image storage; no new worker. Preserve its readonly runtime/profile and source binds.


## Digital circuit simulator (2026-10-22)

Real Digital runs in a separate Cyborg Docker container, controlled by the shared Server tasks switch and existing Admin Services. Kuma monitor53 adds one on-demand check (47service monitors total). Full browser, storage, save-before-Off and recovery instructions: [DIGITAL.md](DIGITAL.md).


## NAS and Obsidian PDF readiness (2026-10-26, Singapore)

User reported first access after roughly3hours idle taking up to5minutes, with immediate later accesses. Their readiness priority is NAS browsing and Obsidian's server PDF provider. Preserve existing manual AI/service controls.

Observed before tuning: main web continuously running with unchanged Gunicorn master PID2694246/container uptime; no application sleep found. Web cgroup used682,790,912bytes swap (~651MiB), memory.low0 and unlimited swap. Host has3.7GiB RAM, internal rotational root/swap.img and ~1.8GiB swap used. Initial diagnostics showed51–64%IOwait and page-ins; Docker CLI was slow when cold. These observations support cold paging as a contributor, but the reported3-hour/5-minute incident was not reproduced under a controlled idle period. Existing persistent UDisks policies remain ata-pm-standby0/APM254 for the stable internal and primaryNAS drive IDs; independent backup-drive policy unchanged.

Applied only web resource fields in /srv/docker/tamestorage/compose.yaml:
- mem_limit:2g (ceiling, not preallocated RAM)
- memswap_limit:2g (memory+swap total equals memory limit)
- mem_reservation:512m (best-effort working-memory protection)

Native Compose validation passed. Docker Engine update applied the policy live; actual cgroup memory.max2147483648, memory.low536870912 and memory.swap.max0 verified. Web container/image/master and environment/command retained; mounted source/destination/RW sets match Compose (normalize array ordering for comparisons). An isolated no-network/read-only container from the same existing image also received these limits correctly at creation, then was removed. Gunicorn workers were gracefully refreshed after two checks found no active incoming connections; eventual worker exit/boot logs and healthy responses confirmed completion. No forced termination/container recreation or unrelated-service restart. Old web swap fell to26,202,112bytes; residual existing swap entries are reclaimed gradually, so limit0 must not be described as current usage0.

Samba: administrator-authenticated systemctl set-property smbd.service MemoryLow=64M MemorySwapMax=0. Persistent files /etc/systemd/system.control/smbd.service.d/50-MemoryLow.conf and50-MemorySwapMax.conf verified. Effective memory.low67108864/memory.swap.max0; service active, same master PID74020 and ActiveEnterTimestamp2026-08-19. No Samba restart/client disconnect. Original ~17MiB swap can remain until accessed; further swapping prohibited. Existing smbd.service.d/tamestorage.conf preserved. Password handled only through non-echo authentication and not recorded.

PDF provider confirmed from real vault pointers: capability-protected /s/<token>/raw/... routes in routes/shares.py, through the same protected web service. lib/pdf_text_cache.searchable_pdf only checks cached derived-PDF manifest/fingerprint and falls back to original; it does not generate OCR on the read path. Two real Obsidian PDF URLs returned HTTP206 for first/last64KiB in0.10–0.21s on repeated Mac probes (first earlier sample1.142s). Original Cache-Control private/no-store and range support retained, no pointer/source PDF changes or capability URL logging. Server-side baseline: home0.147s, PDF first/last ranges0.052/0.036s. NAS Goodnotes top-level metadata enumeration12entries<0.001s; Mac SMB TCP connect0.011s. Mac had no mounted matching SMB share, so no authenticated Finder listing/transfer benchmark was claimed. A fresh isolated Chrome profile on Cyborg loaded home in0.956s and introduction0.029s with no failed requests; authenticated-only page probes redirected to login and are not private-page verification.

One-time follow-up: user transient timer tamestorage-readiness-20261025174719, due about2026-10-25T20:47:19UTC (04:47Singapore on26October). It records website/PDF range response timings, NAS metadata and effective web/Samba policy to its user journal, without URLs/tokens/document content. Read with:
  journalctl --user -u tamestorage-readiness-20261025174719.service --no-pager -o cat
It is nonrecurring, --collect, no persistent script or extra daemon. User Linger=no means it depends on the existing user session remaining active. This tests server responses/policies after3hours, not a controlled cold Mac/SMB session; results pending at installation. Do not claim the exact long-idle problem fully verified yet.

Tradeoff: prioritize resident working memory for file/PDF access; background workloads have less reclaimable RAM during pressure. Global swappiness/swap, network routes, primary data, scheduled NAS backup/retention and other service preferences unchanged. Updated current resource fields are visible to native Admin Settings; keep memswap_limit equal to mem_limit to retain no-swap policy if deliberately changing web memory later. Temporary Compose candidate, disposable container and isolated browser profile removed; no backup or recurring cleaner added.


## Server-owned ChatGPT archive and Assistant integration (2026-10-27 Singapore)

User authorized moving the running Cyborg exporter database into main-server Admin and integrating it with the existing Assistant, matching the old design, minimal UI, extensive different-prompt tests, and durable Cyborg queues. Main NAS/PDF/current features take priority. Canonical detailed manual: /srv/docker/tamestorage/docs/CHAT-ARCHIVE.md.

Primary data now /srv/tamestorage/uploads/Admin/ChatGPT Archive/data/progress.sqlite3, outputs data/exports/<scope>. Separate derived search.sqlite3 (WAL/FULL) isolates index work from primary export transactions. Cyborg loopback exporter now relays to authenticated server HTTPS; original ChatGPT token stays in browser. Full original-table logical checksum and SQLite integrity verified before cutover.2350chats/38304messages and per-chat files verified afterward. Full export still running, not complete. Old Cyborg primary copy/transfer scratch removed after verification; its Documents HTML bookmark opens the server.

Admin→Chats and /admin/chat-archive browse/filter/date/source messages. /chat?archive=1 defaults Cyborg, compact Google/Excerpts choice; ordinary Assistant workspace/tools/image UI retained. Structured generated claims require existing IDs/literal quotes; message links account for empty-message pagination; dirty source documents excluded. No automatic inferred memory, training, vault flooding or entire-archive cloud upload. Google3.6Flash is pinned for archive generation; official3.8 was503high-demand in live tests. Published free eligibility is not a billing/quota guarantee. Tests sent only synthetic evidence to Google.

Reuse existing transcription dispatcher and Cyborg CPU helper/POTION model; separate archive vector DB/epoch never resets lecture vectors. Durable pending marker, idempotent answer receipts/leases, atomic per-document vectors and hash checks. Work waits behind audio/helper queues and lecture/OCR/Quartz units; short-lived nice19/idle-I/O/384MiB children. Native archive reuses grammar model/context and yields when its model slot is busy. No new service/timer/startup/model/Mac runtime. Web/SMB protection and Server tasks/manual-stop preferences preserved. Archive requests skip NAS file inventory rescans; monthly chart is precomputed.

Read canonical manual before changes. Deployment uses same immutable web image with additional readonly source binds; existing main-server containers/settings retained. Gunicorn uses --preload: HUP refreshes workers but does not reliably load changed application modules; recreate web with existing image/config for code changes. A caught initial shared-DB lock issue was fixed by separating derived index and acknowledging dirty source only after durable index commit, conditioned on unchanged source JSON. Test fixture/scratch/one-time snapshots removed after verification, no recurring cleaner. No credentials recorded.
