# Private Obsidian website

URL: https://server.tailb7f57c.ts.net:9443/ (Tailscale access required).
Links are also in Tamestorage About and Admin > Maintenance.

Quartz 5.0.0, upstream tag ab346fa, renders the existing vault. Stock Quartz
navigation, search, graph, backlinks and KaTeX remain enabled. Theme uses the
existing converted Catppuccin theme plus explicit Mocha/teal colors and local
Vollkorn/Nunito Sans fonts. Dark is the initial preference; the theme toggle keeps
the visitor's choice. Source PDFs remain in the existing Goodnotes WebDAV folder;
the website uses small wrapper pages and lazy cloud PDF frames.

## Runtime

- Code: /home/tameszaza/.local/lib/quartz-site
- Generated source, export manifest/checkpoint, current output and build log:
  /srv/tamestorage/.tamestorage_state/quartz-site
- Service: systemctl status quartz-site; restart to reconcile manually.
- watch.py blocks on CouchDB changes, then waits 20 seconds of quiet, at most
  120 seconds per burst. It does not repeatedly scan files. Export/build processes
  exit when finished. Failed work retries with a delay and keeps the current site.
- Unit has CPUQuota=50% (half one CPU), MemoryMax=1G, Nice=15 and idle IO priority.
- Node 22 is shared with goodnotes-links; isolated npm 10.9.2 is under tools/.
- Caddy's existing process serves static output on loopback8094, proxied by
  tailnet-only HTTPS9443. No Funnel or additional web server process.

Generated source and export.json contain private note data; keep their restrictive
permissions. Public static output is readable by Caddy through execute-only ACLs
on NAS ancestors. Caddy's root is only public/, not the source/manifest directory.
Use `:8094` plus `bind 127.0.0.1`; a `http://127.0.0.1:8094` site address adds a Host
matcher that does not match Tailscale's forwarded domain.

## Updates and maintenance

The exporter uses the existing private LiveSync client/config; never print its
credentials. Changes' raw paths are encrypted: watch document type/ID, then let
the upstream client decrypt paths. Filtering raw changes by .md misses edits.
PDF links target wrapper pages for graph/backlink connections; embedded PDFs use
the original server URL. Annotation JSON is converted into readable Markdown for
the site only. Original vault files remain the source of truth.

build-required persists until a successful build; export revision caching must
not clear it. Builds reject nonzero exit, missing homepage, and plugin load errors.
Output promotion swaps a symlink atomically, then deletes the previous generated
output. It does not retain troubleshooting snapshots.

Quartz and plugin revisions are pinned in Git/locks. Core types/utils must match
the plugins: types c7df97d0 and utils98ee433a provide the required JSX exports.
28 plugin packages share the root node_modules via symlinks; no separate copy of
all dependencies per plugin. Root package-lock.json contains the shared build
dependencies. Git-installed remark/rehype-obsidian omit compiled distribution:
their pinned sources are kept in .quartz/support and built by build-plugins.py.

For dependency restoration: run the isolated npm `ci --ignore-scripts`, then
`python3 ../build-plugins.py` from quartz/. Use the existing CPU/memory/IO limits
for installation/builds on this two-CPU HDD machine. The helper builds runtime
JavaScript without optional declaration/maps. Ordinary publishing does not run
installation or this helper. `--only=graph,content-index` rebuilds selected plugins.

Graph options in quartz.config.yaml mirror the Mac's current snapshot
obsidian-graph.json: exclude .json/.previous/goodnote filenames, tags off, orphans
hidden, Life/underdeveloped/academic color groups. These are graph-only rules;
pages remain searchable. Content-index marks generated navigation pages using
isSource; Graph ignores them so folder links do not disguise real orphans.
Maintained patches/ holds graph, content-index and darkmode source patches.
Reapply/check them deliberately before upgrading plugins; upstream updates can
replace local changes. Changing a Mac graph preference later requires updating
the site config; no Mac settings poller was installed.

After code/theme changes, create build-required as user tameszaza (never root),
then restart quartz-site. A root-owned flag prevents the exporter from writing it.
Check last-build.log and service journal. Keep only current required code/data;
delete completed installer caches and temporary diagnostic/staging files.


## Essential fallback (2026-10-30)
Server tasks Off now disables Cyborg processing only. Quartz prefers a ready helper,
then reuses the existing local Node build if the helper is Off/busy/offline or interrupted.
One shared local processing slot serializes builds/OCR/indexing; contention preserves
build-required and retries through the existing watcher. CPU50%, RAM1GiB, swap0;
old site remains published until a validated atomic switch. No new daemon/package.
Maintained watcher: /srv/docker/tamestorage/deploy/cyborg-helper/quartz_watch.py.
