# Goodnotes → Obsidian cloud PDF library

Runs on the main server. Service: `goodnotes-obsidian-links`.
Source: `/srv/tamestorage/uploads/Goodnotes` on the external NAS.
PDF bodies stay on the server; the vault contains small cloud URL pointers under
`PDF Library/<source relative path>` and the generated `Goodnotes PDF Library.md`.
Review devices need Tailscale and Tames PDF (or compatible cloud PDF support).
Changes arrive through the device's normal LiveSync operation.

## Upload a backup to clean obsolete Goodnotes exports

Create a fresh **full-library Goodnotes Manual Backup ZIP**, then upload it to:

`/srv/tamestorage/uploads/temp/Goodnotes-cleanup`

Web: https://server.tailb7f57c.ts.net/index/temp/Goodnotes-cleanup
SMB Storage share: `temp/Goodnotes-cleanup`.
The immediate `uploads/temp` folder also accepts these ZIPs.

After upload finishes and the 20-second quiet debounce, the watcher reads only the
ZIP directory and small `.goodnotesmeta` inventory. It does not extract the library.
It removes unchanged, older, recorded Goodnotes exports absent from that inventory,
then their exact managed vault URL placeholders, and refreshes the library index.
On success it deletes the uploaded temporary ZIP. Annotation Markdown/personal
notes are preserved; references to a removed PDF may require manual repair.

**Use a current complete library backup, not a selected-notebook/partial backup.**
Goodnotes' archive format does not prove that every notebook was selected. The
inventory is the user's authoritative list for this operation. Malformed, empty,
unsupported and unsafe ZIPs are kept untouched. On service/publishing failure,
pending work and ZIP are retained for retry rather than silently declaring success.

New PDFs created/uploaded through SSH, SMB or the NAS website are protected, even
if later overwritten through Goodnotes. Unknown/unobserved writers, changed files
and files newer than the inventory are also preserved. The designated
`tamestorage-webdav` container is treated as the Goodnotes export channel; manually
uploading through that same WebDAV endpoint cannot be distinguished from Goodnotes.
Use SMB/the NAS website for server-owned documents. Direct server uploads already
appear in Obsidian without going through iPad. Goodnotes itself still requires a
manual import: this does not create bidirectional Goodnotes sync.

## Implementation and idle behavior

- Single blocking inotify/fanotify event loop. No periodic scan, cleanup timer,
  full PDF hashing, extra daemon or always-running Node process.
- Canonical privileged code: `/usr/local/lib/goodnotes-watch/{watch,cleanup}.py`,
  root-owned. Fanotify attributes writes to the actual WebDAV process/container;
  filesystem UID alone cannot distinguish SMB/web and WebDAV writes.
- Root unit has bounded capabilities for fanotify and dropping child privileges.
  PDF metadata parsing and Node publisher run as `tameszaza`, not root.
- Filename catalog scans occur on startup or after filesystem events. Publishing
  skips unchanged hashes; 20-second debounce, 120-second continuous-burst limit.
- Private provenance/pending journal: NAS
  `.tamestorage_state/goodnotes-obsidian-links/cleanup-state.json` (0600).
  `cleanup-current/` is transient same-filesystem deletion transaction state,
  not retained backups. `cleanup-last.json` is a small latest successful report.
- Unknown file changes during downtime are protected on startup. NAS unavailability
  prevents cleanup/empty library publication. Concrete failures retry with backoff.
- Existing isolated Node22/client runtime and `publish.mjs`, `client.mjs`,
  `manifest.py`, `cleanup-links.mjs` remain in
  `/home/tameszaza/.local/lib/goodnotes-links`. Secret config remains
  `~/.config/goodnotes-links/livesync.json` (0600); never print it.
- Unit keeps CPUQuota25%, MemoryMax384M, Nice10 and idle I/O priority. Idle watcher
  observed approximately9–10MB unit memory after verification, with short-lived
  publisher memory additional during updates. Recheck live resources as needed.
- Existing version0 IDs/E2EEv2/internal metadata off preserved. Never initialise or
  overwrite the remote database to repair this worker.

Initial migration on 2026-10-12 seeded legacy provenance using NAS manual-upload
activity plus existing iOS Quartz producer metadata in the historically dedicated
Goodnotes folder. Producer metadata is a heuristic for those legacy files, not
proof of origin. Subsequent automated cleanup uses recorded writer provenance,
not producer strings. Preserve these records across upgrades.

## Commands

```sh
systemctl status goodnotes-obsidian-links --no-pager
journalctl -u goodnotes-obsidian-links -n 30 --no-pager
sudo systemctl restart goodnotes-obsidian-links
sudo systemctl disable --now goodnotes-obsidian-links
```

The generated library index is managed; keep personal prose in separate notes.
Source renames/deletes update the index and prune only unchanged hash-verified managed cloud pointers after rechecking source absence. Edited/unmanaged files and personal notes stay intact. The vault prefix migration removed the redundant
Goodnotes level on 2026-10-10; NAS/WebDAV paths remain unchanged.

## Verification, 2026-10-12

Uploaded complete backup contained401 notebooks, matching401 of415 existing PDFs.
Removed14 stale Goodnotes PDFs (15,201,147bytes),14 matching managed CouchDB URL
placeholders, and the completed2,094,033,976byte ZIP.401 original PDFs remain.
A real server-created test PDF carrying iPad producer metadata was protected when
a second complete inventory omitted it. Event processing removed that test ZIP
and no real PDFs. Test PDF, pointer, ledger/report entries and staging were removed.
Local safety cases covered unknown/server origins, changed/newer files, unsafe
archive paths, late writer attribution and sticky server protection. Existing
NAS backups, annotation content and server temp lecture/image uploads retained.

## iPad import reminder
Admin Center and Goodnotes folder pages display an expandable list of manually uploaded or unconfirmed PDFs. The event watcher writes ipad-import-status.json beside its private ledger; the web app only reads this small summary. No web polling or scheduled scan. A Goodnotes WebDAV export or matching fresh full-library backup confirms presence and clears the reminder; later manual changes or absence from the next full inventory restore it. Confirmation does not remove server-owned cleanup protection. This checks presence, not identical PDF content.


## Persistent Goodnotes inbox and cleanup conflict (2026-10-14)

- Lasting user instruction: before adding/changing features, inspect existing cleanup/retention/backup jobs, watchers, path ownership and lifecycle dependencies. Reconcile conflicting deletion/expiry rules in the same task. A persistent workflow inbox is runtime infrastructure even when located under temp; deleting its contents after use and deleting the inbox itself are separate actions. Do not assume an older cleanup job knows new feature paths.
- Found actual /srv/tamestorage/uploads/temp/Goodnotes-cleanup directory missing. /usr/local/sbin/tamestorage-temp-cleanup had a blanket age24h ctime deletion for files/symlinks and then empty directories. It is invoked by existing six-hour tamestorage-backup.timer/service; the separate temp-cleanup.timer is disabled. This explains the incompatible folder lifecycle; no claim of a separately enabled daily cleaner.
- Restored the exact inbox as tameszaza:tameszaza mode0770. Changed only installed root-owned temp-cleanup script to exclude that exact path and descendants from both find deletion passes. Other temp expiry and backup schedule retained. ZIPs are not retained after successful processing: existing root-owned goodnotes-watch/watch.py settle calls cleanup-links.mjs, publishes/verifies the managed catalog, then Reconciler.complete unlinks the unchanged ZIP. Generic expiry cannot race a pending/failed upload there; invalid/failed ZIPs remain for investigation/retry. Prefer this dedicated inbox over direct uploads/temp for backup ZIPs.
- Reused existing watcher/ZIP success cleanup; no new daemon, timer, polling, dependency, service restart or backup run. Existing watcher active and recognizes recreated inbox via existing directory event. Source PDFs, server-created PDF protection, annotations and private provenance state untouched.
- Isolated temp fixture used real installed cleaner logic with age eligibility forced only inside fixture: ordinary expired file/symlink/empty folder deleted, inbox/pending ZIP preserved. Existing Reconciler.complete tested against isolated in-memory job/state removed only test ZIP, folder survived next cleanup. No real library reconciliation or whole-NAS cleaner invoked for testing. Deployed script syntax/source verified; restored folder permissions verified. Test fixtures/staging removed; no backup retained. Runbook and Goodnotes README updated.

## Missing-only iPad ZIP (2026-10-22)

Admin Center and Goodnotes reminder now provide Download for iPad: one ZIP of the existing missing/unconfirmed PDF list, preserving relative folders and excluding confirmed files. In Goodnotes use New → Import on the ZIP. The list depends on latest export/full backup; downloading never confirms import or changes cleanup protection. Streamed ZIP_STORED/ZIP64,256KiB reads, no temporary archive/background job, no change to protected inbox/cleanup rules. Implementation/tests/limits: /srv/docker/tamestorage/docs/GOODNOTES-DOWNLOAD.md.
# Goodnotes iPad imports and cleanup

Open Admin Center or a Goodnotes folder → Review iPad imports. Only PDFs explicitly absent from the last validated full-library backup can enter the ZIP. Unconfirmed files are listed separately and excluded. Downloading never confirms an import or changes provenance/protection.

**Do not import this ZIP directly into Goodnotes. Goodnotes creates duplicate folders rather than merging existing folders.** Extract it in Files, open the matching existing Goodnotes folder, then import that folder's PDFs together. Repeat for other destination folders. The ZIP preserves original relative paths for navigation; it cannot automate Goodnotes folder merging. A fresh complete `.goodnotes` library backup refreshes the presence inventory. The server cannot inspect the live iPad library.

## Presence and protection

Root watcher `cleanup.py` now stores notebook presence independently from PDF byte/version fingerprints. A new export or server replacement of an existing notebook must not make it look missing merely because bytes changed. Full future backups retain their complete path inventory. Migration from older state uses only recorded positive evidence plus the last backup's explicit absence list; unresolved paths remain unconfirmed. Server-created/manual files remain protected from automatic deletion. Late exports older than a known absence do not automatically reverse it. No deletion is inferred solely from `(1)` names.

The October22 folder ZIP import created NTU (1), Docs (1), Tutor (1) trees. User deleted the duplicates on iPad; its latest full backup confirmed absence. One reviewed cleanup removed57obsolete PDFs/473969378bytes only after checking that source fingerprints had not changed and a complete canonical replacement remained. The unique/protected canonical Health&Fitness(1) file was kept. L15/L16's truncated327680-byte canonical file was first atomically restored from its matching complete35-page copy. Twelve affected notes had25short links made explicit, plus the original L15/L16 note's earlier path correction. Existing nonempty/recovery annotation files were retained. This was a one-time reviewed cleanup, not a new name-based deletion policy.

Managed cloud PDF pointers are now pruned by the normal publisher after actual source deletion. It reuses `cleanup-links.mjs`: recheck source absence; remove only a short server URL whose digest equals the saved published digest; verify deletion. Edited/unmanaged pointers stay intact. No ordinary note is removed. Generated library uses Tames PDF terminology. Tames PDF1.2.3 reuses its exact generated-empty annotation checks after debounced PDF deletion; no idle polling. It preserves linked, open, edited, nonempty and recovery groups. Empty directories are retained because desktop adapter nonrecursive removal fails; recursive deletion could race new content. Twenty-nine nonempty/recovery files were hash-verified unchanged during cleanup. Mac plugin customization published; physical iPad application remains a user step.

## Upload integrity

Website uploads now stage bytes in a same-directory `.partial`, fsync, check the original destination fingerprint, retain existing version behavior, then atomically replace. Interrupted uploads remove the stage and retain the previous file. Other writers changing the destination cause a retry error.

The dedicated WebDAV container now uses pinned official Apache httpd2.4/mod_dav_fs instead of rclone: same container name, loopback8081, /webdav URL, Goodnotes source directory and original htpasswd. Interrupted rclone PUTs were reproduced truncating the original even with VFS writes cache. Apache's normal temporary-file/rename behavior passed aborted and successful PUT, GET, PROPFIND, MKCOL, COPY, MOVE, LOCK, UNLOCK, DELETE and auth checks. No custom WebDAV implementation or second proxy daemon. Nonroot UID1000, read-only root filesystem,128MiB/64PIDs,8request threads, bounded logs; DAV locks stored under NAS `.tamestorage_state/webdav`. Root origin watcher accepts only recognized httpd/rclone writers proven to belong to this exact container. Other server writers remain protected. External filesystem/SMB writers still need to publish complete files atomically; WebDAV protection cannot enforce how unrelated clients write.

## Download implementation

Admin-only `/goodnotes/import` review and `/goodnotes/download-for-ipad?existing_folders=yes` handoff. Uses version2event-produced projection, secure openat/O_NOFOLLOW traversal, bounded stdlib ZIP_STORED/ZIP64 streaming256KiB/one source open, private/no-store and direct_passthrough to prevent transfer logging buffering the archive. No staged ZIP or new daemon. Existing temp cleaner/inbox protection remains; successful library-backup ZIP is deleted only after reconciliation. Unknown/invalid summary fails closed. Normal source changes abort a download rather than silently yielding a partial valid archive.

Maintained sources: `lib/goodnotes_status.py`, `lib/goodnotes_download.py`, `lib/atomic_upload.py`, `routes/goodnotes.py`, `routes/files.py`, review/reminder templates, `deploy/goodnotes-watch/{watch,cleanup}.py`, host runtime `/usr/local/lib/goodnotes-watch`, publisher `~/.local/lib/goodnotes-links/{publish,cleanup-links}.mjs`, `/srv/docker/webdav/{compose.yaml,httpd.conf}`. No troubleshooting backup retained. Regression sources are in tests and deploy/activity-console; production state, source PDFs and existing scheduled NAS backup are primary data, not leftovers.
