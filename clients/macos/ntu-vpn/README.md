# NTU VPN

Open `/Applications/NTU VPN.app` and choose **Connect**. On the first connection, sign in with your NTU Singapore student account in the browser window and complete MFA. If Microsoft asks **Stay signed in?**, choose **Yes** to retain your browser sign-in session. Later connections reuse the valid session; NTU can still request sign-in or MFA when its session expires or its policy requires it. Approve the macOS administrator prompt to start the VPN.

Open the same app and choose **Disconnect** when finished. Closing the launcher after connection leaves the VPN connected; reopen it to disconnect. No login item, launchd service, automatic connection, updater, or recurring cleanup task is installed. It does not appear in System Settings > VPN.

The native window has one Connect/Disconnect action, five matching Cyborg switches, two external display presets and Safari karaoke. A single continuous native translucent background includes the title bar, with normal window dragging, Close and Minimize controls. Appearance follows macOS and its accessibility transparency settings. No extra UI framework is installed.

## External display presets

Under **External display**, choose **10-bit · 65 Hz** to try deeper color or **Original · 75 Hz** to return to the previous mode. Both keep the PXP 27-QGG at 2048 × 1152 HiDPI, rendered at 4096 × 2304 and sent to the physical 2560 × 1440 panel. The display may briefly blink during a mode change. The choices work independently of the VPN connection. Opening, refreshing or quitting the app does not select a preset; the Mac keeps the current mode.

These are two saved native display-mode configurations in `DisplayPresets.json`, implemented with CoreGraphics in `DisplayPresets.swift`. The display is matched by UUID/vendor/model/serial, and current mode IDs are discovered each time. The built-in screen and desktop arrangement are preserved. The picker disables unavailable modes or a disconnected monitor; failed mode verification restores the previous mode. No BetterDisplay installation, polling loop, helper, daemon, gamma dimming, color-profile edit or privileged configuration write is needed.

On the tested HDMI adapter/cable, selecting the 65 Hz native mode negotiates **10-bit SDR YCbCr 4:4:4 limited range**, while 75 Hz negotiates **8-bit SDR YCbCr 4:4:4 limited range**. A separate 10-bit RGB full-range connection mode was also tested successfully, but is not selected by this native-mode preset. These presets preserve the monitor’s existing automatic range/encoding selection. The status line reports resolution and refresh rate; CoreGraphics’ framebuffer depth alone is not proof of HDMI connection depth. Recheck connection-level bit depth if the cable, adapter, monitor settings or another display app changes. The monitor’s native panel precision versus 8-bit plus FRC remains unverified.

Native macOS Displays settings can also switch the 65 Hz and 75 Hz modes; ordinary external displays do not gain a named reference-preset menu from this app. The names above are in NTU VPN. No native system preset or ICC profile is installed.

Read-only diagnostics: `Contents/MacOS/NTUVPN --display-presets-status`. Explicit mode selection: `--display-preset ten-bit` or `--display-preset original`. `--verify-ui` covers the picker, Refresh and existing native window controls.

Gateway: `vpngate-student.ntu.edu.sg`. Like the existing Cyborg configuration, login uses the gateway endpoint and reports `clientos=Windows` / OpenConnect `--os=win` with `PAN GlobalProtect` as the user agent. OpenConnect and its active dependencies are managed by Homebrew. The launcher uses Apple's built-in WebKit; it installs no separate browser, Python environment, or GUI framework. The login window uses this app's persistent default website data store to retain NTU/Microsoft session cookies. Passwords and MFA secrets are never requested or stored by the launcher itself. The short-lived VPN SAML cookie travels in memory through a private FIFO, never in a command argument or preferences file. Failed connections show a fixed error category; raw diagnostics are collected in an anonymous private temporary file descriptor, then discarded without retaining a named log. Default TLS certificate validation remains enabled.

Root-owned runtime PID file, created only after choosing Connect: `/private/var/run/local.tameslist.ntu-vpn/<uid>.pid`. Only the matching OpenConnect process is signalled on Disconnect. It gets SIGINT for normal route/DNS cleanup. Tailscale settings are left intact. NTU's server-provided routes and DNS may affect simultaneous connectivity; verify your required services after login.

NTU must enable VPN access for the account. Installation and unauthenticated portal access do not establish successful account login or a working tunnel.

Source: `main.swift` in this directory. Build with `build.command`. Connection mechanics follow OpenConnect's GlobalProtect interface and the gp-saml-gui project's documented SAML field mapping; this launcher is independent custom code.

References:
- https://vpngate-student.ntu.edu.sg/global-protect/getsoftwarepage.esp
- https://www.infradead.org/openconnect/globalprotect.html
- https://github.com/dlenski/gp-saml-gui

## Cyborg fan

The **Fan boost** switch controls Cooler Boost. Off restores normal automatic cooling; it does not stop cooling. Its tooltip shows the last confirmed RPM. Cyborg and Tailscale must be available. Controls use existing narrowly scoped passwordless helpers and do not collect/store a password.

All five switches refresh together using one bounded SSH connection on open, reopen or Refresh, and after a change. Unknown states are disabled until confirmed; no idle polling, new remote daemon or automatic fan/VPN linking. Ordinary commands have a 25-second bound. Bluetooth changes allow 55 seconds because the existing speaker service can take up to 45 seconds to start.
## Cyborg night mode

The **Night mode** switch blanks its keyboard backlight and configured GNOME displays when On. Off wakes the displays and restores previous keyboard brightness (low if previously off). The switch reads confirmed remote state on open/refresh; no password prompt. Restoration state remains on Cyborg.

This does not suspend Cyborg or change its monitor layout, screen brightness, fan boost, audio or VPN. GNOME can wake the displays on local input. No daemon, polling, startup action or saved password is added. Cyborg must be reachable over Tailscale with its GNOME desktop signed in. Fan and night commands share the same bounded SSH handling and cannot run concurrently in this app.

Protected remote helpers: `/usr/local/libexec/cyborg-controls/fan` and `night`. The old `/home/tameszaza/.local/bin/cyborg-fan` and `cyborg-night` names are compatible launchers. `/etc/sudoers.d/cyborg-controls` permits only exact fan on/off/status and night on/off/toggle/status commands for tameszaza. Helpers and parent directory are root-owned and not user-writable. Other administrator commands retain normal authentication. Removing this sudoers file revokes passwordless controls; no stored credential is involved.

A small root-owned runtime snapshot under `/run/cyborg-night` is deleted on successful restoration and clears at reboot. Keyboard control is restricted to board MS-15K1 and verified 15K1IMS1.110–113 firmware, with EC write permission restored after each write and the existing fan lock respected. Only the documented keyboard brightness register is changed.

Implementation references:
- https://github.com/BeardOverflow/msi-ec/blob/main/msi-ec.c (CONF_G2_1 keyboard backlight)
- https://gitlab.gnome.org/GNOME/mutter/-/blob/main/data/dbus-interfaces/org.gnome.Mutter.DisplayConfig.xml (PowerSaveMode)

## Cyborg safeguard and server tasks

The two switches read Cyborg’s confirmed state when the app opens or reopens.
Use the small refresh button after changing a setting elsewhere. An unreachable
Cyborg leaves the switches disabled until refresh succeeds. There is no idle
polling, automatic VPN linking, or saved password. The controls share the existing
bounded SSH handling with fan/night controls.

**GPU safeguard** starts/stops the existing system-wide Cyborg GPU Guard. Its
thresholds and optional boot startup remain configurable in that app on Cyborg.
Off restores automatic GPU clocks. This switch does not change boot startup.

**Server tasks** controls admission of new jobs to Cyborg: grammar AI, manual LLM
writing/organization and transcription. Off returns503 for new AI requests and
leaves new recordings queued on the server. Off interrupts active processing and releases its model memory. Saved transcription
checkpoints remain for automatic resume when enabled. Normal main-server LanguageTool checks continue. On permits work and resumes queued checkpoints. Grammar/model services that were
running before Off are restored; services already Off stay Off. Failed jobs still
require Retry.
The preference persists across reboots independently of the safeguard.

The same Server tasks switch is in Cyborg GPU Guard on Cyborg. The shared CLI is
`~/.local/bin/cyborg-server-tasks on|off|status|desktop-status`. Source/tests are in
`~/.local/lib/cyborg-server-tasks`, private preference in
`~/.config/cyborg-server-tasks/state.json`. No additional daemon, listener,
startup job or sudo permission was added. Guard changes use its existing narrowly
scoped protected sudo controller. The scope covers currently installed Cyborg
server entrypoints; future processing integrations must use the same admission
check. Rendering helper: `NTUVPN --render-preview /path/to/image.png` captures a
native layout preview and exits without connecting the VPN.


## Bluetooth speaker

Enable **Bluetooth speaker**, then connect to **Cyborg Speaker** in the Mac’s Bluetooth/Sound output menu. Keep MacBook Air Microphone selected for input for stereo music. The Mac must be within Bluetooth range; Tailscale carries control commands only. The switch invokes the existing `~/.local/bin/cyborg-speaker on|off|status` without sudo. It does not alter automatic startup, pair devices, select Mac audio or change volume. Off restores the normal Cyborg desktop Bluetooth setup. Switching can briefly interrupt Cyborg audio while its existing lifecycle reloads WirePlumber.

Native verification helpers: `NTUVPN --self-test`, `--verify-ui` (read-only remote status, native title-bar hit tests, minimize/close), and `--render-preview /path/to/image.png`. None starts VPN or changes a Cyborg control.

## Karaoke · Safari

Play your backing track in Safari, then turn on **Karaoke · Safari**. Allow NTU VPN to capture audio when macOS asks. The slider changes the key from −12 to +12 semitones; negative values lower it, positive values raise it. Playback speed remains unchanged. **Reset** selects the original key and bypasses the pitch unit. **Off**, closing the app, or quitting restores Safari's ordinary output and destroys the temporary audio route. Minimize the app to keep karaoke running.

This applies to all audio from Safari, including other Safari tabs. Other apps and the microphone are excluded. The source is Safari's verified WebKit audio process, not a system-wide mix. Audio is processed locally in memory, never recorded, saved or sent anywhere. Karaoke is independent of VPN, Cyborg and Bluetooth speaker controls. It always starts Off; there is no login item, daemon, driver, external library, subscription or updater.

Choose your desired Sound output before turning Karaoke on. A changed output, audio format or replaced Safari audio process stops Karaoke safely; turn it on again afterward. Outputs with microphone input streams or more than stereo tap channels are rejected rather than capturing a microphone. The feature uses macOS Core Audio process taps and Apple's native pitch unit. A small processing delay is expected; output volume stays under the usual Mac/Safari controls. If the permission prompt was missed, allow NTU VPN's audio capture in System Settings → Privacy & Security, start Safari audio, then retry. An eight-second silent-input timeout turns Karaoke off and restores original audio.

Source: `KaraokeAudio.swift`, integrated with `main.swift`. `Tests/main.swift` checks actual Apple pitch-unit output frequencies, stereo separation, sample rates, FIFO behavior and tempo. Build and run those tests with `Tests/run.command`. Existing `--self-test` and `--verify-ui` retain their original VPN/control checks. The earlier Safari web-extension prototype could not obtain samples from this Mac's YouTube streams and was superseded by this native path.

References: [Apple Core Audio taps](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps), [AVAudioUnitTimePitch](https://developer.apple.com/documentation/avfaudio/avaudiounittimepitch), [AVAudioEngine manual rendering](https://developer.apple.com/documentation/avfaudio/avaudioengine/manualrenderingblock).

## Moving between displays

Window dragging belongs to macOS. The main window is a normal movable NSWindow
with its real title bar and no title-bar overlay. Empty background/static labels
start a native drag using the original mouse-down event and
NSWindow.performDrag(with:); buttons, switches and the slider keep their native
hit targets. The implicit whole-background drag flag is off, so background input
has one explicit handoff to Window Server.

There is no app-side mouse tracking, grab-point state, frame override, screen
repositioning callback, mouse monitor, polling or new dependency. macOS owns the
drag, screen constraints, display reconfiguration and native Space-edge behavior.
The translucent appearance and native Close/Minimize controls remain.

The previous custom coordinate-tracking workaround and title-bar accessory were
removed on 2026-10-21. Four generated native cross-display gestures (title bar and
background, both directions) passed with less than one logical point of offset.
Tests used a valid corridor shared by both displays and supplied real mouse deltas;
synthetic points in the gap between differently positioned displays are not a
valid physical mouse path. --verify-ui also checks a plain movable NSWindow,
absence of title-bar accessories, actual controls hit tests, Minimize and Close.
Physical confirmation of the original intermittent report remains useful; these
checks do not establish a macOS defect. Space changes and monitor unplugging were
not driven during verification.

Apple reference: https://developer.apple.com/documentation/appkit/nswindow/performdrag(with:)
