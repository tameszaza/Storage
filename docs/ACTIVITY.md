# Activity console

Open Admin Center → Activity, or https://server.tailb7f57c.ts.net/admin/activity . This is a server website, not an Obsidian view. Admin login is required. The existing Services panel still provides Start/Stop/Restart controls.

The console reads existing Server/Cyborg queues, systemd status, fixed health endpoints and bounded worker reports: PDF/image OCR and page progress, search/vector updates, Quartz note builds, Goodnotes reconciliation/link publishing, LiveSync maintenance, music/video sync, transcription/lyrics, calendar, backup and storage recovery. Expand a task for its source path and details. Filters separate running, waiting, attention and idle work. It reports actual available stages; Cyborg AI currently exposes availability/model but not individual inference-request progress.

No new monitoring service, timer, model or Mac index. The existing restricted host broker accepts only `{"action":"activity-snapshot"}` for observation; it cannot start work through this action. Demand snapshots are cached for five seconds with a single-flight lock; SQLite is opened read-only with bounded busy/query deadlines and no schema initialization. Only fixed paths/commands/endpoints are allowed. Secrets, raw process command lines, arbitrary logs and database credentials are not returned. Existing Server tasks Off/manual stop preferences are respected. Unreachable and paused are separate states.

The browser fetches only while visible and unpaused, never overlaps requests, aborts on hide/unload, preserves the last snapshot on failure, and creates text nodes rather than rendering untrusted HTML. No full streaming log or historical metrics store is added. Short status collection/IO timeouts can temporarily mark a source unavailable; refresh retries without starting services.

## Maintained sources

- `deploy/tamestorage_activity.py` → root `/usr/local/libexec/tamestorage_activity.py`.
- Existing `deploy/tamestorage-host-control` → root broker. Existing service/socket/authentication retained.
- `routes/activity.py`, `templates/activity_console.html`, `static/css/activity-console.css`, `static/js/activity-console.js`.
- Header link in `templates/admin_panel.html`; Admin navigation stays selected via `templates/base.html`.
- `deploy/activity-console/` contains isolated collector/API regressions and the corresponding source fixtures. No running service reads that test directory.

Web deployment reuses the current image and read-only source binds; only web is recreated. Root modules require privileged installation/restarting the existing broker. Preserve other compose mounts and container controls. Tests cover unavailable/Off/offline states, queue stages, read-only SQLite, secrets, auth, fixed requests, caching and single-flight behavior. Browser checks cover desktop/mobile layout, task/host filters, pause/resume and no hidden-tab polling. These browser checks used mocked transport with a real production snapshot; authenticated live HTTP/API was verified separately.
