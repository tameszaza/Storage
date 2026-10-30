# Source map and provenance

2026-10-30 snapshot. All paths below are source; environment values, tokens, private keys, PDFs, image files, SQLite databases and models are excluded.

| Repository path | Installed source / owner |
|---|---|
| Root app, `routes/`, `lib/`, `static/`, `templates/`, `tests/`, `deploy/` | Main server `/srv/docker/tamestorage` |
| `services/lecture-search/` | `/home/tameszaza/.local/lib/lecture-search` |
| `services/quartz-site/` | Host exporter/watcher; upstream Quartz dependencies stay upstream |
| `services/goodnotes-links/` | `/home/tameszaza/.local/lib/goodnotes-links` |
| `services/goodnotes-watch/` | `/usr/local/lib/goodnotes-watch` |
| `services/tamestorage-transcription/` | Main-server inbox/dispatcher, not unavailable native Cyborg worker |
| `services/cyborg-grammar-gateway/` | Main-server gateway/validation; excludes SSH identity and control key |
| `services/goodnotes-stats/` | New isolated statistics worker and cache; config from private env file |
| `services/{webdav,languagetool,obsidian-livesync,navidrome,uptime-kuma}/` | Installed service compose/configuration/provisioning source, excludes runtime data |
| `deploy/host/`, `deploy/systemd/` | Installed main-server control helpers and relevant unit/drop-in files |
| `deploy/cyborg-helper/` | Main server's maintained helper/storage/admission/OCR/search/build implementation and tests |
| `clients/macos/ntu-vpn/` | `/Applications/NTU VPN.app/Contents/Resources`, source only |
| `clients/obsidian/` | Custom installed plugin entry points, manifests and styles; no `data.json` |

The root `deploy/` also contains earlier installation/update utilities. Installed copies under `services/` and `deploy/host/` identify the current host implementations; do not blindly rerun historical installers. Public examples use environment keys and defaults, never current credentials. Root legacy phone files are preserved from the repository; current phone and Linux-only Cyborg source were not available for a fresh comparison.

## Upstream services

Container image references are recorded in each committed compose file. These are third-party projects; their upstream source remains the authoritative implementation. No claim is made to own or rewrite them.

- [Uptime Kuma](https://github.com/louislam/uptime-kuma): monitoring server; our compose/provisioning integration is in `services/uptime-kuma/`.
- [LanguageTool](https://github.com/languagetool-org/languagetool): native grammar engine; our compose/gateway/client integration is separate.
- [CouchDB](https://github.com/apache/couchdb) and [Obsidian LiveSync](https://github.com/vrtmrz/obsidian-livesync): document replication and client customization sync.
- [Navidrome](https://github.com/navidrome/navidrome): music server, alongside our playlist/lyrics workflows.
- [Apache HTTP Server](https://github.com/apache/httpd): Goodnotes WebDAV.
- [Radicale](https://github.com/Kozea/Radicale): CalDAV calendar service.
- [Quartz](https://github.com/jackyzha0/quartz): published note site; our export/build control is in `services/quartz-site/`.
- [Samba](https://gitlab.com/samba-team/samba): native Finder storage/maintenance shares.
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF) and [fontTools](https://github.com/fonttools/fonttools): pinned geometry dependencies for statistics. Their licenses remain applicable.

Upstream source links complement the exact package/image versions in compose files, dependency locks and manifests; no node_modules, virtual environments, downloaded package sources or model weights are checked in.
