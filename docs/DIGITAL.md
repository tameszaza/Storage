# Digital in the browser

Open **https://server.tailb7f57c.ts.net/digital/** with Tailscale connected. Keep the trailing slash.

The real Digital v0.31 application runs in the `cyborg-digital` Docker container on Cyborg. The main server only proxies its browser interface through existing Tailscale HTTPS and Caddy. Digital's normal circuit editor, interactive inputs, simulation, test cases, analysis, FSM editor, component libraries and exports are the original application. Icarus Verilog 12, Yosys, Graphviz, Make and Mousepad are installed inside the container; no replacement simulator was written.

## Files and daily use

- File → Open: use `/projects`, which is the existing `/home/tameszaza/Documents/Verilog` folder on Cyborg. Existing `Sim`, `src`, `tb`, build tasks and files are shared directly with Remote SSH.
- File → Save / Save As: save project files in `/projects`. Files under an unmounted container directory can disappear if the container is replaced. Digital can warn when saving a built-in example outside its original project; choose Save anyway when making your own copy.
- Open the left edge handle → File Manager → `projects` to upload/download `.dig`, `.v`, VCD and exported files. The browser manager is restricted to `/projects`.
- The same sidebar has the container's terminal. `cd /projects && make simulate` runs the existing Verilog task. `mousepad /projects/src/your_module.v` opens a text editor for HDL.
- Shortcuts follow Linux: use Ctrl, including Ctrl+O and Ctrl+S. Clipboard transfer is available in the sidebar; automatic host clipboard support depends on the browser.
- Closing a browser tab preserves the running session. All tabs/devices control the same application session.
- Save before turning Server tasks Off, stopping/restarting Digital, or shutting down Cyborg. These stop the application and discard unsaved in-memory edits. Saved project files and settings persist.

## Controls and health

The existing **NTU VPN → Server tasks** switch controls this container along with the other Cyborg server workers. No new Mac agent, polling or boot task was added. Cyborg's shared command is `~/.local/bin/cyborg-server-tasks on|off|status`.

Tamestorage Admin → Services → Cyborg also lists **Digital circuit simulator**, with existing Start/Stop/Restart/Details controls. An explicit Admin Stop persists; turning Server tasks On respects this manual stop. Use Start to clear it. Start/Restart requires Server tasks On. Existing protected controller also accepts `sudo -n /usr/local/libexec/cyborg-services start digital` and the equivalent `stop`, `restart` and `detail` operations.

Uptime Kuma: **https://server.tailb7f57c.ts.net:3001/status/services**, Cyborg → **Digital circuit simulator · on demand** (monitor 53). Healthy means Digital's Java process, VNC service and HTTP interface pass the container health check. An intentional stop/shared Off is healthy standby; an unexpected stop or unhealthy running session while enabled fails. Status uses the existing restricted SSH monitoring command and loopback broker cache (up to 30 seconds), then Kuma's 60-second interval with one retry. Brief startup transitions may appear pending. No inference/model is invoked by Digital checks.

## Deployment and recovery

- Runtime image: `cyborg-digital:1`, built from the pinned official `jlesage/baseimage-gui:debian-13-v4.14.0` image plus the existing official Digital distribution. Its built-in TigerVNC/noVNC, NGINX, file manager and terminal are reused.
- Cyborg source: `/run/media/tameszaza/New Volume/Cyborg Digital/deploy`.
- Persistent container settings: `/srv/cyborg-services/work/digital` → `/config`. Docker/containerd already live in the existing external ext4 service image. Check external UUID `A87A-15B6` and `/srv/cyborg-services` before deployment; never silently create substitute storage on an unmounted disk.
- Projects: existing `/home/tameszaza/Documents/Verilog` → `/projects`. No project tree was copied or replaced.
- Listener: Cyborg tailnet IP `100.66.32.22:9030` → container `5800`. Raw VNC is not published. No host desktop, GPU device, Docker socket or USB device is mounted. Physical FPGA flashing requires a separately configured device connection.
- Limits: 2 CPU, 2 GiB RAM with no additional swap, 256 PIDs, 128 MiB shared memory; Java heap 768 MiB. Observed warm container memory about 247 MiB, not a peak guarantee. Application runs as UID/GID1000; only container initialization runs as root. Restart policy `no` preserves shared/manual startup control. Closing Digital itself reopens it through the base supervisor.
- Server: Caddy loopback `127.0.0.1:8097`, strips `/digital` and proxies Cyborg. Tailscale Serve maps only `/digital` to this listener, preserving other routes. The service remains private to the tailnet; no Funnel/public DNS/tunnel was added.
- Root-owned controls: Cyborg `/usr/local/libexec/cyborg-services`; main server `/usr/local/libexec/tamestorage_containers.py`. Maintained copies in existing Cyborg Services deploy and server `deploy/cyborg-helper` are synchronized.
- Read-only monitor: Cyborg `~/.local/lib/cyborg-monitor/status.py`; existing server adapter `http://127.0.0.1:9780/cyborg/digital`. Kuma inventory/map remain in `/srv/docker/uptime-kuma`; the dedicated additive provisioner in server `deploy/digital` preserves existing monitors/groups. Credentials remain only in the existing private operator record and are passed over stdin, never logged.

To rebuild intentionally: save circuits, run Docker build in the source directory, then recreate only `cyborg-digital` using maintained `create.py` after removing its old stopped container. Preserve `/config` and `/projects`. `create.py` refuses wrong/unmounted storage and does not remove any existing container. Do not rerun the original Docker-storage migration.

## Verification on 22 October 2026

Verified the real browser canvas and menu interaction through the final server HTTPS route, save and reopen of a circuit in `/projects`, persistence across shared Off/On, and matching browser upload/download contents. Digital's graphical half-adder test passed all four cases; its official v0.31 external-Verilog adder circuit passed its 256-case test through Digital/Icarus co-simulation. Existing project `make -B simulate` passed all eight full-adder combinations inside the container. The design's pre-existing missing-timescale warning remains.

Six focused Digital lifecycle/allowlist checks and ten existing service-control regression tests passed. Actual shared Off/On stopped/restored Digital and existing workers; existing native AI services returned active. Kuma monitor53 recorded UP → DOWN → UP when Digital was deliberately stopped outside the control policy and then restarted. Intentional shared Off was independently verified as standby by the monitor. Test circuits/transfers, diagnostic browser files, old staging and task-specific build cache were removed after verification. Final shared Server tasks On, Digital healthy, Docker boot startup still disabled. Physical Cyborg desktop Digital remained separate and running. No physical FPGA/USB workflow or VHDL simulation was tested.
