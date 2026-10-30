# Tamestorage and the Tames network mesh

Source and deployment mechanisms for the personal storage website, Obsidian/Goodnotes pipeline, media workers, monitoring, Mac controls and room automation. Updated from the running main server and this Mac on **2026-10-30**.

Start with [the mechanism guide](docs/MESH.md) and [the source map](docs/SOURCE-MAP.md). This repository contains application source and configuration templates. Documents, PDFs, music, model weights, databases, live credentials and private keys are separate runtime data.

| Component | Source / configuration | Runtime |
|---|---|---|
| Website, Assistant, diary logging, file storage | [app.py](app.py), [routes](routes), [lib](lib), [templates](templates), [static](static) | Main server Docker |
| Calendar, video and music sync | [calendar_worker.py](calendar_worker.py), [video_worker.py](video_worker.py), [playlist_worker.py](playlist_worker.py) | Separate workers in [compose.yaml](compose.yaml) |
| Goodnotes statistics | [services/goodnotes-stats](services/goodnotes-stats) | Separate low-priority container |
| Goodnotes WebDAV, backup/provenance/cleanup | [services/webdav](services/webdav), [services/goodnotes-watch](services/goodnotes-watch) | Apache container + host watcher |
| Obsidian PDF pointers and publication | [services/goodnotes-links](services/goodnotes-links) | Native host service |
| PDF/image OCR, lexical/vector search, invisible PDF text | [services/lecture-search](services/lecture-search) | Socket activation + queued host jobs |
| Quartz site export/build/publication | [services/quartz-site](services/quartz-site) | Host watcher; local or Cyborg build |
| Cyborg CPU helpers and admission/storage | [deploy/cyborg-helper](deploy/cyborg-helper) | Cyborg Docker, external disk |
| Grammar and optional AI checks | [services/languagetool](services/languagetool), [services/cyborg-grammar-gateway](services/cyborg-grammar-gateway) | Native LanguageTool container + socket gateway |
| Transcription dispatch and inbox | [services/tamestorage-transcription](services/tamestorage-transcription) | Durable main-server queue; Cyborg worker |
| Admin controls, settings and live task console | [routes/services.py](routes/services.py), [routes/settings.py](routes/settings.py), [routes/activity.py](routes/activity.py), [deploy/host](deploy/host) | Restricted host broker |
| Uptime Kuma, Navidrome, LiveSync | [services](services) | Separate upstream containers |
| Mac VPN, Cyborg switches, display/audio helpers | [clients/macos/ntu-vpn](clients/macos/ntu-vpn) | Native macOS app |
| Custom Obsidian clients | [clients/obsidian](clients/obsidian) | Desktop/iPad plugins; no private settings |
| Phone/aircon automation | [aircon.py](aircon.py), [ac_control.py](ac_control.py), [ac_weekly.py](ac_weekly.py), [termux-boot.sh](termux-boot.sh) | Existing phone source; live phone version not revalidated |

## Configuration and operation

[compose.yaml](compose.yaml) keeps the main website, calendar/music/video workers and statistics container separate. Other existing Docker stacks have their own compose files under `services/`. Secrets and machine parameters are supplied by private environment/configuration files; safe examples and defaults are committed. The web stack currently reads `/etc/tamestorage.env`; statistics reads `/etc/tamestorage-goodnotes-stats.env` from its [.env.example](services/goodnotes-stats/.env.example). Native services have their own documented config paths and systemd units.

Inspect the configuration before deploying: this is the existing mesh, with host paths, read-only mounts and restricted broker dependencies, not a turnkey anonymous public cloud deployment. `docker compose config --quiet` validates the selected host's environment before a targeted `up -d --no-deps SERVICE`. Never put production environment files or state into Git. See [deployment documentation](deploy) and [systemd units](deploy/systemd).

The Server tasks switch in the Mac app controls Cyborg admission. Essential OCR/index/web-build work falls back to the main server when Cyborg is unavailable or admission is off. AI inference is not moved to the small main server. Goodnotes statistics yields to critical work and reuses completed page results.

The native Finder maintenance connection is `smb://server/Server-root` (saved through Cmd+K). It uses the existing authenticated Samba service and ordinary Unix permissions; root-owned maintenance still needs SSH/sudo. [Share configuration](deploy/samba/tamestorage-maintenance.conf).

## Source availability

Main-server and Mac files above were read from their installed source locations. Cyborg's maintained CPU-helper/deployment sources are included. Cyborg was in Windows during this update; its Linux-only native AI/GPU/speaker/transcription worker implementation could not be refreshed. Do not treat documentation or old deployment copies as a freshly verified dump of those unavailable files. See [the source map](docs/SOURCE-MAP.md) for this boundary and third-party upstream links.
