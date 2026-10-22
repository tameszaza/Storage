# Server settings

Open **Admin → Settings** (`/admin/settings`). Search finds both common and advanced
options. **Advanced** reveals native parameter names. Edit, **Review changes**, then
**Save only** or **Save & apply**. Saved changes awaiting application have a separate
**Apply saved** action. Retention values apply on the next existing cleanup.

## Scope

159 currently available controls in 13 groups: server, storage/backup, music sync,
Navidrome, LanguageTool, Cyborg gateway, lecture search, Goodnotes/WebDAV, calendar,
LiveSync, transcription, uptime and video. Every managed Docker has memory, CPU,
PID, restart-policy and log-rotation controls. Other options include worker/thread
counts, upload/quota limits, native cache/request settings, service credentials,
request budgets, search/read limits, host job resource caps and existing schedules.
Kuma's monitor-specific settings remain in its native linked dashboard.

The page edits typed allowlisted parameters in existing Compose, INI, systemd and
operations files. It has no generic root command, raw file editor, network exposure,
image updater, database identity or mount-path control. Calendar/music credentials
are the app's sync-client credentials; rotate the corresponding native service
account separately before updating its client. Existing account quotas are separate
from the default quota for new accounts.

## Applying

Review lists affected Docker services. Applying recreates only those services with
`--no-build --pull never`, retaining each container's current immutable image ID.
`compose.settings-images.yaml` beside each affected Compose file is maintained
active image-pin configuration, not a troubleshooting backup. It is merged only
for settings applications; an intentional normal deployment can upgrade images.
Deliberately stopped services remain stopped and recovery preferences are retained.
The web service is applied last; an interruption while it restarts is expected.

Host search/gateway/index/OCR/transcription values are loaded on the next process
invocation. Applying reloads systemd but does not launch a model, indexer or queued
job or restart an active transcription. The Goodnotes watcher resource cap updates
in place; a memory cap below current usage is refused at application time. New
request budgets also update systemd's process deadline. Updated timers restart the
existing timer; already-due work may run according to systemd's normal semantics.

Apply can partially succeed across independent services. Pending changes remain
until the operation succeeds; Refresh shows what is still saved. Saved files can
also take effect on a later ordinary restart. There is no separate runtime rollback
service. Byte-level write errors restore the files from temporary process memory.

## Protection and runtime

Admin session plus existing CSRF protection; private no-store responses. Passwords
are never returned to the browser, previews or operation metadata. Changed Compose
files are private mode0600 and retain ownership. Password fields are blank until
changed, with explicit Clear. Native Compose validation preserves escaped dollar
signs. Stale-window revisions, type/range checks, heap/container memory checks and
request-budget checks reject invalid saves before writing. Configuration validation
is not a guarantee that arbitrary resource reductions fit a workload: choose caps
with headroom and review uptime/service status afterward.

Root handler `deploy/tamestorage_settings.py` runs inside the existing restricted
host-control process. No new daemon, scheduler, UI framework or Mac dependency.
Catalogue reads occur on open/Refresh; operation polling only during a bounded save.
The existing control adapter still handles Docker and exit-node actions. Preserve
its `RuntimeDirectoryPreserve=yes` drop-in and the shared disk-analysis socket.

Saved pending IDs and last20 key-only audit entries are in root0600
`/var/lib/tamestorage-control/settings.json`. No credentials or rollback copies there.
Numeric retention values are in root0600 `/etc/tamestorage-operations.conf` and read
by the existing backup/temp cleanup; Goodnotes inbox and retryable recording guards
remain in force. Host overrides use unit-specific `60-tamestorage-settings.conf`
drop-ins. No defaults were changed by installing this page.

Maintained tests: `tests/test_server_settings_backend.py` on the host (existing
PyYAML), and `tests/test_server_settings_api.py` plus `tests/test_services.py` in the
app environment. Real browser layout and a disposable-container save/apply cycle
are deployment verification steps; never test by changing production limits.

Deployment verification:29 focused tests passed,22 browser assertions each desktop/mobile, light/dark layouts visually inspected. Live signed Admin HTTP save/apply proved real Docker memory changes, image pinning, literal dollar signs and stopped-state preservation on the disposable container, then removed it. All159 production fields remain available,0pending;43 Kuma checks retained. No production settings were edited for tests.

Admin heading layout correction (2026-10-18): Settings now sits at the right of Admin center in a dedicated .admin-titlebar flex row. Existing global Admin header display:block had placed the link below the title. Scoped admin.css, desktop/mobile visual inspection and authenticated live GET verified. Only web redeployed (ba0c49cb868e); settings behavior/services unchanged. Temporary fixture/build files removed.
