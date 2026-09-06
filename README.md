# Tamestorage NAS

Private Flask NAS dashboard and browser file manager for the Ubuntu server.
The app runs in Docker behind Caddy and is exposed only on the server's
Tailscale address.

## Running layout

- Web UI: `http://server` or `http://100.124.30.94`
- Web container: `tamestorage`, bound to host `127.0.0.1:5000`
- Playlist worker: `tamestorage-playlist-worker`
- Primary USB 3 disk: `/srv/tamestorage`
- Browser/SMB files: `/srv/tamestorage/uploads`
- USB 2 backup disk: `/srv/tamestorage-backup`
- Navidrome music: `/srv/music`
- SMB share over Tailscale: `smb://server/Storage`
- Faster SMB share on the home LAN: `smb://192.168.0.2/Storage`

Caddy listens only on the Tailscale IPv4 address. Samba binds only to loopback
and the server's `192.168.0.2` Ethernet address. Tailscale Serve securely
forwards tailnet port 445 to loopback because Samba cannot directly bind its
listener to the non-broadcast TUN interface. UFW allows direct SMB only from
the trusted `192.168.0.0/24` LAN. Use the LAN address at home to avoid Tailscale
encryption overhead on the old two-core CPU; use the MagicDNS name everywhere
else.

## Music playlist sync

Log in as `Admin` and open **Music sync** in the sidebar. The page can:

- add any HTTP/HTTPS playlist URL;
- run an immediate quick sync;
- choose a sync interval in hours (0 means manual-only; up to 720 hours);
- pause or remove playlist entries without deleting downloaded songs;
- display the live and historical yt-dlp output for each playlist.

Each source has a private archive and managed directory, so later checks only
download newly added entries. Output is organized as:

```text
/srv/music/Managed Playlists/<name> [<source id>]/<index> - <title> [<video id>].<extension>
/srv/music/Playlists/<name>.m3u
```

Navidrome mounts `/srv/music` read-only and imports the maintained M3U file on
its normal scan cycle. The worker validates a complete remote snapshot before
it considers removal. A normal removal needs two identical successful checks
at least five minutes apart; an empty or 50%+ drop needs three. Any connection,
snapshot, extraction, or download error keeps all existing files and playlist
entries. Only files inside that source's managed directory can be deleted.

The image includes Deno and yt-dlp's EJS components so current YouTube
JavaScript challenges can be solved inside the read-only worker container.

## Docker operations

```bash
cd /srv/docker/tamestorage
sudo docker compose ps
sudo docker compose logs -f web playlist-worker
sudo docker compose up -d --build
```

The source deployment copy is `/srv/docker/tamestorage`. Runtime state is kept
on the primary USB disk, outside the image and containers.

At boot, systemd attempts both UUID-identified USB mounts before Docker. Docker
bind mounts are configured not to create missing source directories, preventing
uploads from silently landing on the internal disk if the primary USB disk is
absent. Caddy waits for Tailscale, Samba waits for the network, and all three
Docker applications use `restart: unless-stopped`.

## Backup

`tamestorage-backup.timer` runs every six hours. The guarded backup script
checks both expected filesystem UUIDs before using rsync to mirror the complete
primary disk to the backup disk. Deleted primary files are deleted from the
mirror on its next run.

```bash
systemctl list-timers tamestorage-backup.timer
sudo systemctl start tamestorage-backup.service
cat /srv/tamestorage-backup/.nas-last-success
```

## Deployment files

- `deploy/install-server.sh`: mounts disks and installs Caddy, Samba, backup,
  Tailscale Serve, firewall, and environment configuration.
- `deploy/install-docker.sh`: installs/rebuilds the web app and worker in Docker.
- `deploy/tamestorage-backup`: UUID-guarded mirror script.
- `compose.yaml`: container runtime configuration.

Public registration and the old phone-only air-conditioner, camera, sound,
voice, planner, feedback, and AI features are not included in this deployment.
