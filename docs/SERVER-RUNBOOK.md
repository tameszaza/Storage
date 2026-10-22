# Tamestorage server runbook

Last verified against the running **main server**: 30 September 2026. Server host:
`tameszaza-Lenovo-G480` (Ubuntu 24.04); Linux user: `tameszaza`.
This is an operations guide, not a place for passwords or recovery keys. The
separate `tameslist` phone runs its own air-conditioner and camera services; this
document covers the main NAS and notes that boundary below.

## Start here after SSH

```bash
less ~/SERVER-RUNBOOK.md
hostnamectl
lsblk -f
findmnt -T /srv/tamestorage
findmnt -T /srv/tamestorage-backup
findmnt -T /srv/music
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
systemctl --failed
systemctl list-timers tamestorage-backup.timer
```

Connect over Tailscale with `ssh tameszaza@server` (or
`ssh tameszaza@100.124.30.94`); on the home LAN use `192.168.0.2`.
The Tailscale MagicDNS name is `server.tailb7f57c.ts.net`. IPs can change;
confirm with `tailscale ip -4` and `ip -br addr` before changing firewall or
client settings. `sudo` may prompt for the Linux password. Do not put it in a
shell command or this file.

**Before restarting containers, check both USB mounts.** An unmounted source
can otherwise appear as an empty directory and lead to misleading disk usage or
missing data. The backup script itself refuses to run without both mounts.

## Service map and ports

| What | How it runs | Listen / access | Code or configuration |
| --- | --- | --- | --- |
| Tamestorage web, files, admin, AI, planner, video/music UI | Docker `tamestorage` (`web`) | host `127.0.0.1:5000`; `https://server.tailb7f57c.ts.net/` | `/srv/docker/tamestorage`, `/etc/tamestorage.env` |
| Playlist and lyric sync | Docker `tamestorage-playlist-worker` | no public port | same Compose project; `/srv/music` and state on primary disk |
| Calendar two-way event sync | Docker `tamestorage-calendar-worker` | no public port | same Compose project; planner state and Radicale |
| CalDAV server | Docker `tamestorage-calendar` (Radicale) | host `127.0.0.1:5232`; tailnet HTTPS port `8443` | Radicale config/data under `/srv/tamestorage/.tamestorage_state/radicale` |
| Video download queue | Docker `tamestorage-video-worker` | no public port | same Compose project; videos and queue on primary disk |
| Navidrome music playback | Docker `navidrome` | host `4533`; tailnet HTTPS port `10000` | `/srv/docker/navidrome/compose.yaml` and `/srv/docker/navidrome/data` |
| Goodnotes WebDAV | Docker `tamestorage-webdav` (rclone) | host `127.0.0.1:8081`; `/webdav/` via HTTPS | `/srv/docker/webdav/compose.yaml` |
| Obsidian LiveSync | Docker `livesync-couchdb` plus one-shot `livesync-init` | host `127.0.0.1:5984`; tailnet HTTPS port `5984` | `/srv/docker/obsidian-livesync/compose.yaml` |
| LanguageTool for Obsidian | Docker `languagetool` | host `127.0.0.1:8010`; tailnet HTTPS port `8010` | `/srv/docker/languagetool/compose.yaml` |
| SMB/NAS share | host `smbd.service` | LAN TCP `445`; tailnet TCP `445` forwarded by Tailscale Serve | `/etc/samba/smb.conf` |
| Tailnet, HTTPS routes, optional exit node | host `tailscaled.service` and Tailscale Serve | tailnet `443`, `5984`, `8010`, `8443`, `10000`, `445` | `tailscale serve status` |
| HTTP reverse proxy | host `caddy.service` | tailnet IP TCP `80` | `/etc/caddy/Caddyfile` |
| Disk-space analysis | host `tamestorage-disk-analysis.service` | local socket, no public port | `/usr/local/lib/tamestorage-disk-analysis.py` |
| Admin VPN/host control | host `tamestorage-host-control.service` | local socket, no public port | `/usr/local/libexec/tamestorage-host-control` |
| Six-hour NAS mirror and temp expiry | `tamestorage-backup.timer` → service | no port | `/usr/local/sbin/tamestorage-backup` and `tamestorage-temp-cleanup` |
| Primary disk recovery | host `tamestorage-primary-recover.timer` | checks `/srv/tamestorage` about every minute | `/srv/docker/tamestorage/deploy/tamestorage-primary-recover` |
| Legacy LocalTunnel | host `localtunnel.service` | outbound tunnel to local port `5000` | `/etc/systemd/system/localtunnel.service` |

All Docker stacks use `restart: unless-stopped`. The LocalTunnel unit is still
enabled, but is **not** the documented primary path for the website; use the
Tailscale URL. Verify its purpose before relying on or disabling it.

Useful read-only checks:

```bash
docker compose -f /srv/docker/tamestorage/compose.yaml ps
docker compose -f /srv/docker/navidrome/compose.yaml ps
docker compose -f /srv/docker/webdav/compose.yaml ps
tailscale status
tailscale serve status
ss -ltnu
systemctl status caddy smbd tailscaled docker --no-pager
```

### Public-facing routes and clients

| Client | Address |
| --- | --- |
| Web browser on tailnet | `https://server.tailb7f57c.ts.net/` |
| Web browser using Caddy HTTP on tailnet | `http://server/` |
| Files app / SMB on LAN | `smb://192.168.0.2/Storage` |
| Files app / SMB on tailnet | `smb://server.tailb7f57c.ts.net/Storage` |
| Goodnotes WebDAV on tailnet | `https://server.tailb7f57c.ts.net/webdav/` |
| Obsidian LiveSync on tailnet | `https://server.tailb7f57c.ts.net:5984/` |
| LanguageTool for Obsidian on tailnet | `https://server.tailb7f57c.ts.net:8010/` |
| CalDAV (Ubuntu/iOS) on tailnet | `https://server.tailb7f57c.ts.net:8443/` |
| Navidrome/Substreamer on tailnet | `https://server.tailb7f57c.ts.net:10000/` |
| Navidrome on LAN | `http://192.168.0.2:4533/` |

Tailscale Serve terminates tailnet HTTPS and proxies to localhost. Caddy
separately handles tailnet HTTP port 80. The `server` hostname alone works when
MagicDNS/search domains are active. Test client access *from the relevant
network*; localhost health alone does not prove the tailnet route works.
Tailscale's exit-node setting is controlled in the web Admin center through
`tamestorage-host-control.service`; changing it affects other devices using
this server as their exit node. Check the current setting in the UI or with
`tailscale status` before changing it. Avoid resetting `tailscale serve` while
troubleshooting a single service, because Serve carries several routes.

## Disks, files, and backups

There is **no RAID**. These are three different roles; Linux `/dev/sdX` letters
may change after reboot or reconnection, so use UUIDs and mountpoints.

| Role | Mount / filesystem | What lives there |
| --- | --- | --- |
| Internal system disk | `/` (`/dev/sda2`, ext4), with `/srv/internal-music` bind-mounted to `/srv/music` | Ubuntu, Docker definitions/data, music audio and playlists |
| Primary NAS disk | `/srv/tamestorage` (ext4, UUID `4dcef454-5a20-484b-b00e-fe9a136f82b9`) | `/uploads` for web/SMB, Goodnotes and movies; `/.tamestorage_state` for app state |
| Independent backup disk | `/srv/tamestorage-backup` (ext4, UUID `c01587a0-900f-48c1-994d-e5a47a758967`) | six-hour mirror and `.backup-versions` |

The mounts are declared in `/etc/fstab`. Check them with:

```bash
lsblk -o NAME,SIZE,FSTYPE,UUID,LABEL,MOUNTPOINTS
findmnt -T /srv/tamestorage/uploads
findmnt -T /srv/tamestorage-backup
findmnt -T /srv/music
df -hT / /srv/tamestorage /srv/tamestorage-backup /srv/music
```

`/srv/tamestorage/uploads` is the `[Storage]` SMB share and the browser file
manager's root. Samba allows user `tameszaza`, uses that Unix ownership, and
is configured for LAN/loopback; Tailscale Serve forwards tailnet port 445 to
loopback. Check the loaded config with `testparm -s` and logs with
`journalctl -u smbd -n 100 --no-pager`. After an intentional Samba config edit,
run `testparm -s` before `sudo systemctl reload smbd`.

The backup timer runs around every six hours (plus up to five minutes jitter)
and performs a guarded `rsync` from primary to backup. It requires both
mountpoints and ext4. Changed/deleted files go into a dated
`/srv/tamestorage-backup/.backup-versions/` directory; versions older than 30
days are pruned. The mirror is not an off-site backup, and it does not protect
against damage to both attached drives.

**Excluded from backup:** `/uploads/Admin/Movies/` (replaceable downloads) and
`/uploads/temp/` (temporary handoff files). The music library on `/srv/music`
is on the internal disk and is **not** in this primary-disk mirror. Do not
assume all server data is backed up by this timer.

```bash
systemctl list-timers tamestorage-backup.timer
systemctl status tamestorage-backup.timer --no-pager
cat /srv/tamestorage-backup/.nas-last-success
journalctl -u tamestorage-backup.service -n 100 --no-pager
```

To trigger an extra backup after verifying the mounts and available space:
`sudo systemctl start tamestorage-backup.service`. This writes to the backup
disk, mirrors deletions, and prunes expired versions; do not run it while
diagnosing suspected accidental deletion until you have inspected/recovered
the versioned data. The script has a lock to avoid overlapping copies.

`/srv/tamestorage/uploads/temp` is the handoff folder. The same backup service
runs `tamestorage-temp-cleanup` first; files/symlinks older than 24 hours (by
ctime) and then old empty folders are removed. The separate
`tamestorage-temp-cleanup.timer` is disabled. The RAID check/monitor timers are
masked leftovers; do not enable them expecting a RAID array.

The Disk space web page reads mounts and uses the read-only disk-analysis host
service for folder breakdowns. If the analysis fails while files remain
accessible, check `systemctl status tamestorage-disk-analysis.service` and
`journalctl -u tamestorage-disk-analysis.service -n 100 --no-pager` before
touching disks. The disk page is not a replacement for SMART diagnostics.

### Primary USB disk disconnect and automatic recovery

On 4 October 2026 at 17:04 local time, the primary disk lost USB power while
the server itself stayed up. The kernel reported failed writes and an aborted
ext4 journal; the disk reappeared under a different `/dev/sdX` letter but did
not remount automatically. The primary filesystem was checked with
`e2fsck -p`, its journal recovered, and it was remounted by UUID. Disk-backed
containers and SMB were restarted. The independent backup disk stayed mounted.
A successful service check does not prove that every in-flight write survived
an abrupt power loss.

`tamestorage-primary-recover.timer` now checks the primary mount about every
minute. If it is missing, the recovery script stops only the disk-backed
containers and SMB. When the expected UUID returns, it runs automatic ext4
repair, remounts the filesystem, and restarts those services. LanguageTool and
Navidrome remain independent. The script and systemd units are kept under
`/srv/docker/tamestorage/deploy/` and installed at `/usr/local/sbin/` and
`/etc/systemd/system/`. The web container health check also reads `users.json`
from the primary disk, so a lost disk should no longer appear as a healthy web
service merely because the login page still responds.

```bash
findmnt -T /srv/tamestorage/uploads
lsblk -f
systemctl status tamestorage-primary-recover.timer --no-pager
journalctl -u tamestorage-primary-recover.service -n 80 --no-pager
docker compose -f /srv/docker/tamestorage/compose.yaml ps
```

If `e2fsck -p` cannot fix a problem safely, the script leaves services stopped
and creates `/run/tamestorage-primary-recovery-failed`. Inspect the disk and
repair output before clearing that marker. Do not run a backup while the
primary mount is absent or suspect. Avoid disconnecting the disk power while
the server is running; software cannot preserve writes already in flight.

## Website and app state

The running source is `/srv/docker/tamestorage`; its Compose file defines web,
playlist worker, calendar worker, Radicale, and video worker. The main web
container serves the file manager, search, previews, sharing, trash, admin,
disk/network checks, AI assistant, planner, music, and video pages. Those are
routes in one application, not separately restartable services. Web settings
and sensitive environment variables are in `/etc/tamestorage.env`; never paste
its contents into logs, tickets, or this runbook.

Persistent app state is `/srv/tamestorage/.tamestorage_state`, including users,
planner events/tasks, playlist definitions/logs/runtime, search indices,
Radicale calendars, video queue (`video_downloads.sqlite`), activity/transfer
logs, and trash index. Do **not** hand-edit live JSON or SQLite while the web
or workers are running. Make a copy and stop the relevant worker first if a
repair is unavoidable. The browser **Trash** is not the Linux filesystem
trash; use the web UI to restore/delete browser-managed files.

```bash
cd /srv/docker/tamestorage
docker compose ps
docker compose logs --tail=100 web
docker compose logs --tail=100 playlist-worker calendar-worker video-worker
docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' tamestorage
curl -I http://127.0.0.1:5000/
```

After a code/config change, validate `docker compose config --quiet`, build the
affected image(s), and use `docker compose up -d --no-deps SERVICE`. Rebuilding
the web image does **not** automatically replace an already-running playlist
or calendar worker; recreate the worker(s) when their shared code changed.
For example, after a deliberate web-only code deployment:

```bash
cd /srv/docker/tamestorage
docker compose config --quiet
docker compose build web
docker compose up -d --no-deps web
docker compose ps
docker compose logs --tail=80 web
```

The video worker has its own image/build (`Dockerfile.video`); consult
`/srv/docker/tamestorage/VIDEO-DOWNLOADS.md` before rebuilding it. Do not run
`docker compose down -v`: volumes/state are meant to survive container changes.

### Music and lyrics

Use **Music sync** in the website to add, pause, quick-sync, or remove managed
YouTube playlists. `tamestorage-playlist-worker` runs scheduled jobs and lyric
fetching. Definitions are in
`/srv/tamestorage/.tamestorage_state/playlists.json`; per-playlist logs are in
`playlist_logs/`. Downloaded audio and lyrics sidecars are on `/srv/music`,
principally `Managed Playlists/`, with M3U files in `Playlists/`. A quick sync
or scheduled playlist sync is the supported way to retry failed lyrics; do not
start an unrelated global lyrics job. A detach/remove-sync operation is
different from **Delete playlist + files**—read the confirmation carefully.
Unavailable remote videos can remain pending instead of causing existing files
to be removed.

Navidrome (`/srv/docker/navidrome`) mounts `/srv/music` read-only and stores
its database in `/srv/docker/navidrome/data`. It scans the music and playlist
files for playback. Lyrics priority includes `.ttml`, `.lrc`, `.srt`, `.txt`
and embedded lyrics. Music resides on the **internal** disk, not either NAS
USB disk. Check both services when songs are downloaded but absent in a player:

```bash
docker compose -f /srv/docker/tamestorage/compose.yaml logs --tail=150 playlist-worker
docker compose -f /srv/docker/navidrome/compose.yaml ps
docker compose -f /srv/docker/navidrome/compose.yaml logs --tail=150 navidrome
findmnt -T /srv/music
```

If Navidrome itself is down, restart only its stack with
`cd /srv/docker/navidrome && docker compose up -d`, then inspect logs. Avoid
deleting its database to fix an indexing delay.

### Calendar and dated tasks

The web **Schedule & tasks** page stores planner state in the primary disk's
`.tamestorage_state/planner.json`. Radicale serves CalDAV from the primary disk;
`tamestorage-calendar-worker` synchronizes web **events** both ways with the
CalDAV calendar. It normally checks on the configured interval (see
`CALDAV_SYNC_INTERVAL_SECONDS` in `/etc/tamestorage.env` without disclosing
other values). Client address is the `:8443` HTTPS URL above. CalDAV has its
own credentials, distinct from the web login. In the current deployed sync
code, tasks/todos are stored in the web planner but **not exported as CalDAV
VTODOs**; do not promise two-way native task sync to Ubuntu/iOS until that is
implemented and tested. An external ICS calendar import is a separate feature
and is not a two-way editing channel.

```bash
docker compose -f /srv/docker/tamestorage/compose.yaml ps radicale calendar-worker
docker compose -f /srv/docker/tamestorage/compose.yaml logs --tail=150 radicale calendar-worker
tailscale serve status
```

If native clients ask for authentication repeatedly, verify the `:8443` URL,
tailnet connection, and the CalDAV-specific account; do not change the web
admin password as a first response. Do not delete `radicale/data` or
`planner.json` to reset a sync problem.

### Goodnotes WebDAV

`tamestorage-webdav` serves only
`/srv/tamestorage/uploads/Goodnotes` using rclone. The client URL is
`https://server.tailb7f57c.ts.net/webdav/` (include the trailing slash).
Authentication is HTTP Basic from `/srv/docker/webdav/htpasswd`; it is
**separate** from the website and Samba credentials. That file is sensitive
even though it stores hashes. The same Goodnotes files are visible in the web
file manager/SMB, and are included in the normal NAS backup.

```bash
docker compose -f /srv/docker/webdav/compose.yaml ps
docker compose -f /srv/docker/webdav/compose.yaml logs --tail=100 webdav
findmnt -T /srv/tamestorage/uploads/Goodnotes
tailscale serve status
```

If an iPad reports an invalid host, enter the full HTTPS URL rather than an
SMB path or a bare hostname; verify Tailscale is connected on that device.

### Obsidian LiveSync

Self-hosted LiveSync uses CouchDB. The Compose project and private `.env` are in
`/srv/docker/obsidian-livesync`; data is on the primary NAS disk at
`/srv/tamestorage/.tamestorage_state/obsidian-livesync/data`. The Mac vault is
local at `/Users/tameslist/Obsidian/Notes` and uses the LiveSync real-time
mode; Obsidian's built-in Sync plugin is disabled for this Vault.
Tailscale Serve adds tailnet HTTPS port `5984` without changing existing routes.
The plugin database name is `obsidiannotes`; credentials are in the private
Compose `.env`, not in this run-book. The vault encryption passphrase is kept
only on the Mac in its private LiveSync setup files; do not put it on the server.

```bash
docker compose -f /srv/docker/obsidian-livesync/compose.yaml ps
docker compose -f /srv/docker/obsidian-livesync/compose.yaml logs --tail=100 couchdb couchdb-init
tailscale serve status
curl -I http://127.0.0.1:5984/obsidiannotes # 401 without credentials
```

The normal NAS mirror includes the database directory while CouchDB runs.
CouchDB permits copying live `.couch` files because they are append-only, but
its backup guide recommends copying index files before database files. The NAS
mirror does not guarantee that order and does not include this stack's `.env`
or Compose configuration, so test a restore before relying on it as a full
backup. The Mac vault is a second live copy of synced notes. Check the NAS
mount before starting the container after a disk issue. Do not delete the
CouchDB data directory or run `docker compose down -v` to fix sync problems.

### LanguageTool for Obsidian

The Obsidian LanguageTool Integration plugin uses the private server at
`https://server.tailb7f57c.ts.net:8010`. In the plugin settings, choose
**Endpoint: Custom URL** and enter that base URL without `/v2/check`; the
plugin appends the API path. The Mac must be connected to Tailscale.
LanguageTool checks text sent by the plugin; it does not receive the whole vault.
The open-source server does not provide LanguageTool cloud-only AI rules.

The Docker backend binds only to loopback. Tailscale Serve proxies tailnet
HTTPS port `8010` to `127.0.0.1:8010`; no public port forwarding is needed.
The Compose stack uses `restart: unless-stopped`. To check it:

```bash
docker compose -f /srv/docker/languagetool/compose.yaml ps
docker compose -f /srv/docker/languagetool/compose.yaml logs --tail=100 languagetool
tailscale serve status
curl -fsS -d "language=en-US" -d "text=This is an test." http://127.0.0.1:8010/v2/check
```

From the Mac, test the tailnet URL with the same POST request, replacing the
localhost URL above with `https://server.tailb7f57c.ts.net:8010/v2/check`.

### Video downloads

The **Videos** page submits to the persistent queue handled by
`tamestorage-video-worker`. Downloaded files live under
`/srv/tamestorage/uploads/Admin/Movies`; the queue database is
`/srv/tamestorage/.tamestorage_state/video_downloads.sqlite`. Movies are
deliberately **excluded** from the six-hour backup. Delete in the Videos page
is permanent, not the file-manager Trash. Source websites can change; use
the job error and worker logs before retrying. A stopped/failed worker should
not be mistaken for completed downloads.

```bash
docker compose -f /srv/docker/tamestorage/compose.yaml ps video-worker
docker compose -f /srv/docker/tamestorage/compose.yaml logs --tail=150 video-worker
df -h /srv/tamestorage
```

## Common failures: shortest safe path

1. **Whole site unavailable:** check Tailscale on client and server,
   `tailscale serve status`, `docker compose ... ps`, then web logs. If only
   `http://server` fails, check Caddy separately; HTTPS is via Tailscale Serve.
2. **Files gone or storage usage suddenly changed:** check `findmnt` and
   `lsblk -f` before starting Docker or a backup. Never create replacement
   data under an unmounted `/srv/tamestorage` path.
3. **SMB fails but website works:** check `smbd`, `testparm -s`, the correct
   LAN/tailnet address, and Tailscale TCP 445 Serve. Use `journalctl -u smbd`.
4. **Calendar/WebDAV/Navidrome fails alone:** check its container, its separate
   HTTPS route in `tailscale serve status`, and its own authentication. Do not
   restart every stack at once.
5. **Backup late/failed:** verify both mounts, free space, the timer, service
   journal, and `.nas-last-success`. Investigate disk errors with `dmesg` and
   SMART tools before forcing another backup.
6. **Disk analysis page errors:** check the host analysis service and its
   journal, then the web logs. The scanner should not need a full NAS remount.

For a single affected host unit, restart only that unit after reading its
journal, e.g. `sudo systemctl restart caddy` or
`sudo systemctl restart tamestorage-disk-analysis.service`. For a single Docker
service, use `cd /srv/docker/tamestorage && docker compose restart SERVICE`;
`restart` does not load a newly built image. Note any manual change in this
runbook and keep a copy of config before editing it.

## Security and maintenance notes

- `/etc/tamestorage.env`, Radicale auth/config, and WebDAV `htpasswd` contain
  secrets or hashes. Back them up securely, but do not commit or paste them.
- Keep Ubuntu, Docker images, yt-dlp/video dependencies, and the app updated
  deliberately. Read release notes and test the relevant service before
  replacing all live containers. `:latest` tags do not update themselves.
- Backup integrity needs occasional restore tests to a **different** location;
  a successful timer alone does not prove files can be recovered. Versioned
  copies live on the backup disk; inspect exact filenames before restoring.
- Use `journalctl -u NAME`, `docker compose logs SERVICE`, and the website's
  Admin logs for diagnostics. Avoid broad cleanup commands on `/srv`.
- The phone (`tameslist`, usually port 5000 on its own host) has separate
  credentials and runs the air-con/camera system. A link from the main site
  does not mean these services run in a main-server Docker container.

This runbook describes what was observed on 30 September 2026. Re-run the
inventory commands after significant redeployments or disk/network changes
and update this file. The server copy is `~/SERVER-RUNBOOK.md`; the source copy
is `docs/SERVER-RUNBOOK.md` in the Storage repository.


## Cyborg offload (2026-10-21)

Cached OCR, PDF text-layer recognition, embeddings, vector ranking and Quartz5 builds
now use the optional Cyborg CPU helper on its external HDD. Main-server original
files, text/keyword index, database and web remain authoritative. Server tasks in
NTU VPN gates background work; the existing dispatcher preserves waiting jobs.
See docs/CYBORG-HELPERS.md and the Admin Services host filter. Do not add a duplicate
queue/timer, Mac model/index, or place this state under uploads/temp cleanup.

## Assistant diary logging (2026-10-22)

See [ASSISTANT-DIARY.md](ASSISTANT-DIARY.md). Web reuses the existing encrypted LiveSync runtime and private Docker network, with on-demand diary appends and shared image storage; no new worker. Preserve its readonly runtime/profile and source binds.

## OCR event watcher recovery (2026-10-22)

The systemd start limiter counts successful event starts, not only failures. The previous three-start/600-second limit disabled OCR after ordinary updates. Installed `lecture-search-ocr.service.d/40-success-counter.conf`: a fixed privileged ExecStopPost resets only this service's start counter when SERVICE_RESULT=success (including existing SuccessExitStatus=75 deferred standby). Failure exits do not reset it; the original three-start/600-second circuit breaker and60-second failure restart delay remain. Existing PathExists marker, one-shot worker,256MB/one-core bounds, Cyborg admission switch and durable queues/caches are unchanged. No new resident process, timer or retry queue. A short systemctl invocation runs only after completion.

Maintained source/drop-in installer/native regression in ~/.local/lib/lecture-search and /srv/docker/tamestorage/deploy/cyborg-helper. Opt-in regression: `python3 ~/.local/lib/lecture-search/test_ocr_result.py`. Tests eight successful starts, eight accepted75 pauses and exactly three actual failure executions with a fourth blocked, using disposable user-manager path/service units. Wait for stop-post completion; use execution count rather than Result alone (systemd255 user manager retained exit-code after rejecting another start). Installer requires root, validates exact drop-in contents, reloads, clears the previous failed states and rearms only OCR path. Actual production backlog processing resumed; final queue verification is recorded below. Preserve the drop-in across unit reinstalls. Do not disable the failure limit or add a periodic watchdog merely to recover this issue.

## Missing-only iPad ZIP (2026-10-22)

Admin Center and Goodnotes reminder now provide Download for iPad: one ZIP of the existing missing/unconfirmed PDF list, preserving relative folders and excluding confirmed files. In Goodnotes use New → Import on the ZIP. The list depends on latest export/full backup; downloading never confirms import or changes cleanup protection. Streamed ZIP_STORED/ZIP64,256KiB reads, no temporary archive/background job, no change to protected inbox/cleanup rules. Implementation/tests/limits: /srv/docker/tamestorage/docs/GOODNOTES-DOWNLOAD.md.
