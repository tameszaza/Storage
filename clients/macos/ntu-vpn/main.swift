import Cocoa
import WebKit
import Darwin

let portal = "vpngate-student.ntu.edu.sg"
let openconnect = "/opt/homebrew/bin/openconnect"
let stateDirectory = "/private/var/run/local.tameslist.ntu-vpn"
func pidFile(_ uid: uid_t) -> String { "\(stateDirectory)/\(uid).pid" }
func command(_ executable: String, _ arguments: [String]) -> (Int32, String) {
    let process = Process(), pipe = Pipe()
    process.executableURL = URL(fileURLWithPath: executable); process.arguments = arguments
    process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
    do { try process.run() } catch { return (-1, "") }
    let data = pipe.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
    return (process.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}
func connectedPID(_ uid: uid_t) -> pid_t? {
    guard let text = try? String(contentsOfFile: pidFile(uid), encoding: .utf8),
          let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)), pid > 1 else { return nil }
    let (code, args) = command("/bin/ps", ["-p", String(pid), "-o", "args="])
    guard code == 0, args.hasPrefix(openconnect + " "),
          args.contains("--pid-file=" + pidFile(uid)), args.contains(portal) else { return nil }
    return pid
}
struct Credentials: Codable { let username: String; let cookie: String; let cookieName: String }
func validCredentials(_ c: Credentials) -> Bool {
    !c.username.isEmpty && c.username.utf8.count < 512 && !c.username.contains("\n") &&
    !c.cookie.isEmpty && c.cookie.utf8.count < 16384 && !c.cookie.contains("\n") &&
    ["prelogin-cookie", "portal-userauthcookie"].contains(c.cookieName)
}
func rootControl() -> Never {
    let args = CommandLine.arguments
    guard geteuid() == 0, args.count >= 3, let uid = UInt32(args[2]), uid > 0 else { exit(2) }
    if args[1] == "--root-disconnect" {
        guard let pid = connectedPID(uid) else { print("DISCONNECTED"); exit(0) }
        guard kill(pid, SIGINT) == 0 else { exit(3) }
        for _ in 0..<100 {
            if connectedPID(uid) == nil { try? FileManager.default.removeItem(atPath: pidFile(uid)); print("DISCONNECTED"); exit(0) }
            usleep(100000)
        }
        exit(4)
    }
    guard args[1] == "--root-connect", args.count == 4, connectedPID(uid) == nil else { exit(5) }
    let fifo = args[3]
    var info = stat(), parent = stat()
    guard fifo.hasPrefix("/private/tmp/ntu-vpn-\(uid)-"), fifo.hasSuffix("/credentials"),
          lstat(fifo, &info) == 0, (info.st_mode & S_IFMT) == S_IFIFO,
          info.st_uid == uid, (info.st_mode & 0o777) == 0o600,
          lstat((fifo as NSString).deletingLastPathComponent, &parent) == 0,
          (parent.st_mode & S_IFMT) == S_IFDIR, parent.st_uid == uid,
          (parent.st_mode & 0o777) == 0o700 else { exit(6) }
    let fd = open(fifo, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
    guard fd >= 0 else { exit(7) }
    var data = Data(), buffer = [UInt8](repeating: 0, count: 4096)
    let deadline = Date().addingTimeInterval(20)
    while Date() < deadline && data.count < 65536 {
        let n = read(fd, &buffer, buffer.count)
        if n > 0 { data.append(contentsOf: buffer.prefix(n)); if data.last == 10 { break } }
        else { usleep(50000) }
    }
    close(fd)
    guard let c = try? JSONDecoder().decode(Credentials.self, from: data), validCredentials(c) else { exit(8) }
    umask(0o022)
    if mkdir(stateDirectory, 0o755) != 0 && errno != EEXIST { exit(9) }
    var state = stat()
    guard lstat(stateDirectory, &state) == 0, state.st_uid == 0,
          (state.st_mode & S_IFMT) == S_IFDIR, (state.st_mode & 0o022) == 0 else { exit(9) }
    // All state is inside a root-owned directory; discard only our stale PID file.
    try? FileManager.default.removeItem(atPath: pidFile(uid))
    let process = Process(), input = Pipe()
    process.executableURL = URL(fileURLWithPath: openconnect)
    process.arguments = ["--protocol=gp", "--os=win", "--useragent=PAN GlobalProtect", "--user=" + c.username,
        "--usergroup=gateway:" + c.cookieName, "--passwd-on-stdin", "--non-inter",
        "--background", "--pid-file=" + pidFile(uid), "--script=/opt/homebrew/etc/vpnc/vpnc-script",
        "--reconnect-timeout=60", portal]
    process.standardInput = input; process.standardOutput = FileHandle.nullDevice
    // Anonymous, mode-0600 file: no diagnostic or credential-bearing file survives.
    var template = Array("/private/tmp/ntu-vpn-errors.XXXXXX".utf8) + [0]
    let errorFD = mkstemp(&template)
    guard errorFD >= 0 else { exit(10) }
    template.withUnsafeBufferPointer { buffer in _ = unlink(UnsafeRawPointer(buffer.baseAddress!).assumingMemoryBound(to: CChar.self)) }
    let errors = FileHandle(fileDescriptor: errorFD, closeOnDealloc: true)
    process.standardError = errors
    process.environment = ["PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"]
    do { try process.run() } catch { exit(10) }
    input.fileHandleForWriting.write(Data((c.cookie + "\n").utf8)); try? input.fileHandleForWriting.close()
    process.waitUntilExit()
    guard process.terminationStatus == 0, connectedPID(uid) != nil else {
        try? errors.seek(toOffset: 0)
        let message = String(data: (try? errors.read(upToCount: 65536)) ?? Data(), encoding: .utf8)?.lowercased() ?? ""
        let category: String
        if message.contains("certificate") || message.contains("ssl connection failure") { category = "TLS" }
        else if message.contains("login failed") || message.contains("authentication failed") || message.contains("authentication failure") { category = "LOGIN" }
        else if message.contains("saml") { category = "SAML" }
        else if message.contains("tun") && (message.contains("failed") || message.contains("error")) { category = "TUN" }
        else if message.contains("resolve") || message.contains("connect() failed") { category = "NETWORK" }
        else { category = "CONNECT" }
        print("VPN_ERROR:" + category); exit(11)
    }
    chmod(pidFile(uid), 0o644); print("CONNECTED"); exit(0)
}

final class XMLValues: NSObject, XMLParserDelegate {
    var values = [String: String](), tag = "", value = ""
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) { tag = elementName; value = "" }
    func parser(_ parser: XMLParser, foundCharacters string: String) { value += string }
    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName: String?) { if tag == elementName { values[elementName] = value } }
    static func parse(_ data: Data) -> [String: String] {
        let delegate = XMLValues(), parser = XMLParser(data: data)
        parser.shouldResolveExternalEntities = false; parser.delegate = delegate
        return parser.parse() ? delegate.values : [:]
    }
}
func extractCredentials(_ fields: [String: String]) -> Credentials? {
    guard fields["saml-auth-status"] != "0", let username = fields["saml-username"] else { return nil }
    for name in ["prelogin-cookie", "portal-userauthcookie"] {
        if let cookie = fields[name] { let c = Credentials(username: username, cookie: cookie, cookieName: name); if validCredentials(c) { return c } }
    }
    return nil
}
func htmlCredentials(_ html: String) -> Credentials? {
    var fields = [String: String]()
    for name in ["saml-auth-status", "saml-username", "prelogin-cookie", "portal-userauthcookie"] {
        let expression = try! NSRegularExpression(pattern: "<" + name + ">([\\s\\S]*?)</" + name + ">")
        if let m = expression.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)), let r = Range(m.range(at: 1), in: html) {
            let raw = String(html[r])
            fields[name] = XMLValues.parse(Data(("<value>" + raw + "</value>").utf8))["value"]
        }
    }
    return extractCredentials(fields)
}
func shellQuote(_ string: String) -> String { "'" + string.replacingOccurrences(of: "'", with: "'\\''") + "'" }
func appleQuote(_ string: String) -> String { "\"" + string.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\"" }

struct FanReading {
    let on: Bool
    let rpm: String
    static func parse(_ output: String) -> FanReading? {
        guard let line = output.split(separator: "\n").first(where: { $0.hasPrefix("Cooler Boost: ") }) else { return nil }
        let fields = line.components(separatedBy: " | ")
        guard fields.count == 3, ["Cooler Boost: ON", "Cooler Boost: OFF"].contains(fields[0]),
              fields[1].hasPrefix("Fan: "), fields[1].hasSuffix(" RPM"), fields[2].hasPrefix("EC: 15K1IMS1.") else { return nil }
        return FanReading(on: fields[0].hasSuffix("ON"), rpm: String(fields[1].dropFirst(5).dropLast(4)))
    }
}

struct NightReading {
    let on: Bool
    static func parse(_ output: String) -> NightReading? {
        switch output.trimmingCharacters(in: .whitespacesAndNewlines) {
        case "Night mode: ON": return NightReading(on: true)
        case "Night mode: OFF": return NightReading(on: false)
        default: return nil
        }
    }
}

struct ServerControlsReading: Decodable {
    let guardOn: Bool
    let accepting: Bool
    enum CodingKeys: String, CodingKey { case guardOn = "guard", accepting }
    static func parse(_ output: String) -> ServerControlsReading? {
        try? JSONDecoder().decode(Self.self, from: Data(output.utf8))
    }
}

struct CyborgReading: Decodable {
    let guardOn: Bool
    let accepting: Bool
    let fan: String
    let night: String
    let speaker: String
    enum CodingKeys: String, CodingKey { case guardOn = "guard", accepting, fan, night, speaker }
    var fanOn: Bool { FanReading.parse(fan)!.on }
    var rpm: String { FanReading.parse(fan)!.rpm }
    var nightOn: Bool { NightReading.parse(night)!.on }
    var speakerOn: Bool { speaker.hasPrefix("Speaker Mode: ON\n") }
    static func parse(_ output: String) -> CyborgReading? {
        guard let value = try? JSONDecoder().decode(Self.self,from:Data(output.utf8)),
              FanReading.parse(value.fan) != nil, NightReading.parse(value.night) != nil,
              ["Speaker Mode: ON\nStart at login/boot: OFF", "Speaker Mode: OFF\nStart at login/boot: OFF", "Speaker Mode: ON\nStart at login/boot: ON", "Speaker Mode: OFF\nStart at login/boot: ON"].contains(value.speaker.trimmingCharacters(in:.whitespacesAndNewlines)) else { return nil }
        return value
    }
}

final class WindowBackdrop: NSVisualEffectView {
    override func hitTest(_ point: NSPoint) -> NSView? {
        guard let hit = super.hitTest(point) else { return nil }
        var ancestor: NSView? = hit
        while let view = ancestor, view !== self {
            if let control = view as? NSControl {
                if let label = control as? NSTextField, !label.isEditable, !label.isSelectable {
                    // Static labels are part of the draggable background.
                } else { return hit }
            }
            ancestor = view.superview
        }
        return self
    }
    override var mouseDownCanMoveWindow: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    // Give Window Server the original event; it owns the complete drag,
    // including display transitions, native constraints and Space gestures.
    override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!, status: NSTextField!, actionButton: NSButton!
    var statusDot: NSView!, spinner: NSProgressIndicator!
    var cyborgBusy = false
    var guardSwitch: NSSwitch!, tasksSwitch: NSSwitch!, readControls: NSButton!
    var serverControlsKnown = false
    var webWindow: NSWindow?, web: WKWebView?, request: URLSessionDataTask?
    var busy = false { didSet { updateActivity() } }
    var authenticating = false, authFields = [String: String]()
    var transferDirectory: String?
    let uid = getuid()
    var fanSwitch: NSSwitch!, nightSwitch: NSSwitch!, speakerSwitch: NSSwitch!
    var cyborgStatus: NSTextField!
    var controlReading: CyborgReading?
    let karaoke = KaraokeAudio()
    var karaokeSwitch:NSSwitch!, karaokeSlider:NSSlider!, karaokeValue:NSTextField!, karaokeStatus:NSTextField!
    var karaokeKey=0
    var displayPresetPicker: NSPopUpButton!, displayPresetStatus: NSTextField!, displayPresetRefresh: NSButton!
    var displayPresetBusy = false
    var displayPresetOptions = [DisplayPreset]()
    var displayPresetReading: DisplayPresetReading?
    let displayPresetQueue = DispatchQueue(label: "local.tameslist.ntu-vpn.display-presets")
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), item = NSMenuItem(), appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit NTU VPN", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu; menu.addItem(item); NSApplication.shared.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 350, height: 650), styleMask: [.titled, .closable, .miniaturizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = "NTU VPN"; window.titleVisibility = .hidden; window.delegate = self; window.isReleasedWhenClosed = false
        window.titlebarAppearsTransparent = true; window.isMovable = true; window.isMovableByWindowBackground = false
        window.isOpaque = false; window.backgroundColor = .clear
        let content = NSView()
        let effect = WindowBackdrop(); effect.material = .hudWindow; effect.blendingMode = .behindWindow; effect.state = .active
        content.autoresizingMask = [.width,.height]; effect.addSubview(content); window.contentView = effect
        let layout = NSStackView(); layout.orientation = .vertical; layout.alignment = .leading; layout.spacing = 18
        layout.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(layout)
        NSLayoutConstraint.activate([layout.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 22),layout.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -22),layout.topAnchor.constraint(equalTo: content.topAnchor, constant: 55)])
        func label(_ text: String, size: CGFloat, weight: NSFont.Weight = .regular) -> NSTextField {
            let value = NSTextField(labelWithString: text); value.font = .systemFont(ofSize: size, weight: weight); return value
        }
        let vpnTitle = label("NTU VPN", size: 15, weight: .semibold)
        status = label("Disconnected", size: 12); status.textColor = .secondaryLabelColor
        statusDot = NSView(); statusDot.wantsLayer = true; statusDot.layer?.cornerRadius = 3
        spinner = NSProgressIndicator(); spinner.style = .spinning; spinner.controlSize = .small; spinner.isDisplayedWhenStopped = false
        NSLayoutConstraint.activate([statusDot.widthAnchor.constraint(equalToConstant: 6),statusDot.heightAnchor.constraint(equalToConstant: 6),spinner.widthAnchor.constraint(equalToConstant: 14),spinner.heightAnchor.constraint(equalToConstant: 14)])
        let statusRow = NSStackView(views: [statusDot,spinner,status!]); statusRow.alignment = .centerY; statusRow.spacing = 6
        let vpnText = NSStackView(views: [vpnTitle,statusRow]); vpnText.orientation = .vertical; vpnText.alignment = .leading; vpnText.spacing = 6
        actionButton = NSButton(title: "Connect", target: self, action: #selector(toggleClicked)); actionButton.bezelStyle = .rounded; actionButton.controlSize = .large; actionButton.keyEquivalent = "\r"
        let vpnRow = NSStackView(views: [vpnText,actionButton!]); vpnRow.distribution = .equalSpacing; vpnRow.alignment = .centerY
        layout.addArrangedSubview(vpnRow); vpnRow.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        let separator = NSBox(); separator.boxType = .separator; layout.addArrangedSubview(separator); separator.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        readControls = NSButton(image: NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Refresh Cyborg controls")!, target: self, action: #selector(readServerControls)); readControls.isBordered = false; readControls.toolTip = "Refresh Cyborg controls"
        let heading = NSStackView(views: [label("Cyborg",size: 13,weight: .semibold),readControls!]); heading.distribution = .equalSpacing; heading.alignment = .centerY
        layout.addArrangedSubview(heading); heading.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        let controls = NSStackView(); controls.orientation = .vertical; controls.spacing = 0; controls.alignment = .leading
        fanSwitch = NSSwitch(); nightSwitch = NSSwitch(); guardSwitch = NSSwitch(); tasksSwitch = NSSwitch(); speakerSwitch = NSSwitch()
        let rows: [(String,String,NSSwitch,Selector,String)] = [
            ("Fan boost","fanblades",fanSwitch,#selector(fanChanged),"Off restores automatic cooling."),
            ("Night mode","moon",nightSwitch,#selector(nightChanged),"Blank Cyborg’s screens and keyboard backlight."),
            ("GPU safeguard","shield",guardSwitch,#selector(guardChanged),"Use Cyborg’s existing GPU temperature guard."),
            ("Server tasks","server.rack",tasksSwitch,#selector(tasksChanged),"Accept AI and transcription work. Off releases model memory; saved jobs can resume."),
            ("Bluetooth speaker","hifispeaker",speakerSwitch,#selector(speakerChanged),"Enable Cyborg Speaker, then choose it in this Mac’s Sound output. Bluetooth range is required.")
        ]
        for (title,symbol,control,selector,hint) in rows {
            let image = NSImageView(image: NSImage(systemSymbolName: symbol, accessibilityDescription: nil) ?? NSImage()); image.contentTintColor = .secondaryLabelColor
            image.widthAnchor.constraint(equalToConstant: 20).isActive = true; image.heightAnchor.constraint(equalToConstant: 20).isActive = true
            let text = label(title,size: 13)
            let left = NSStackView(views: [image,text]); left.spacing = 10; left.alignment = .centerY
            let row = NSStackView(views: [left,control]); row.distribution = .equalSpacing; row.alignment = .centerY
            row.heightAnchor.constraint(equalToConstant: 43).isActive = true; control.target = self; control.action = selector; control.isEnabled = false; control.toolTip = hint; control.setAccessibilityLabel(title)
            controls.addArrangedSubview(row); row.widthAnchor.constraint(equalTo: controls.widthAnchor).isActive = true
        }
        layout.addArrangedSubview(controls); controls.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        layout.setCustomSpacing(6,after: heading); layout.setCustomSpacing(10,after: controls)
        cyborgStatus = label("Checking Cyborg…",size: 11); cyborgStatus.textColor = .secondaryLabelColor; layout.addArrangedSubview(cyborgStatus)
        let displaySeparator = NSBox(); displaySeparator.boxType = .separator
        layout.addArrangedSubview(displaySeparator); displaySeparator.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        displayPresetRefresh = NSButton(image: NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Refresh display preset")!, target: self, action: #selector(refreshDisplayPreset))
        displayPresetRefresh.isBordered = false; displayPresetRefresh.toolTip = "Read this Mac’s external display mode."
        let displayHeading = NSStackView(views: [label("External display", size: 13, weight: .semibold), displayPresetRefresh!])
        displayHeading.distribution = .equalSpacing; displayHeading.alignment = .centerY
        layout.addArrangedSubview(displayHeading); displayHeading.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        displayPresetOptions = (try? DisplayPresetConfiguration.load().presets) ?? []
        displayPresetPicker = NSPopUpButton(); displayPresetPicker.target = self; displayPresetPicker.action = #selector(displayPresetChanged)
        displayPresetPicker.addItem(withTitle: "Choose a preset…")
        displayPresetPicker.addItems(withTitles: displayPresetOptions.map(\.title)); displayPresetPicker.isEnabled = false
        displayPresetPicker.setAccessibilityLabel("External display preset")
        displayPresetPicker.toolTip = "PXP 27-QGG: 10-bit at 65 Hz or original 75 Hz. Both preserve your HiDPI text size."
        layout.addArrangedSubview(displayPresetPicker); displayPresetPicker.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        displayPresetStatus = label("Checking display…", size: 11); displayPresetStatus.textColor = .secondaryLabelColor
        layout.addArrangedSubview(displayPresetStatus)
        layout.setCustomSpacing(6, after: displayHeading); layout.setCustomSpacing(6, after: displayPresetPicker)
        let audioSeparator=NSBox(); audioSeparator.boxType = .separator; layout.addArrangedSubview(audioSeparator); audioSeparator.widthAnchor.constraint(equalTo:layout.widthAnchor).isActive=true
        karaokeSwitch=NSSwitch(); karaokeSwitch.target=self; karaokeSwitch.action=#selector(karaokeChanged); karaokeSwitch.setAccessibilityLabel("Karaoke Safari audio")
        let karaokeHeading=NSStackView(views:[label("Karaoke · Safari",size:13,weight:.semibold),karaokeSwitch!]); karaokeHeading.distribution = .equalSpacing; karaokeHeading.alignment = .centerY
        layout.addArrangedSubview(karaokeHeading); karaokeHeading.widthAnchor.constraint(equalTo:layout.widthAnchor).isActive=true
        karaokeValue=label("0",size:15,weight:.semibold); karaokeValue.alignment = .center; karaokeValue.widthAnchor.constraint(equalToConstant:30).isActive=true
        karaokeSlider=NSSlider(value:0,minValue:-12,maxValue:12,target:self,action:#selector(karaokePitchChanged)); karaokeSlider.isContinuous=true; karaokeSlider.numberOfTickMarks=25; karaokeSlider.allowsTickMarkValuesOnly=true
        karaokeSlider.setAccessibilityLabel("Karaoke semitones"); karaokeSlider.toolTip="Lower or raise Safari audio by up to 12 semitones. Playback speed stays the same."
        let karaokeReset=NSButton(title:"Reset",target:self,action:#selector(karaokeResetClicked)); karaokeReset.controlSize = .small
        let keyRow=NSStackView(views:[karaokeSlider!,karaokeValue!,karaokeReset]); keyRow.spacing=8; keyRow.alignment = .centerY
        layout.addArrangedSubview(keyRow); keyRow.widthAnchor.constraint(equalTo:layout.widthAnchor).isActive=true
        karaokeStatus=NSTextField(wrappingLabelWithString:"Off · normal Safari audio"); karaokeStatus.font = .systemFont(ofSize:11); karaokeStatus.textColor = .secondaryLabelColor
        layout.addArrangedSubview(karaokeStatus); karaokeStatus.widthAnchor.constraint(equalTo:layout.widthAnchor).isActive=true
        layout.setCustomSpacing(8,after:karaokeHeading); layout.setCustomSpacing(8,after:keyRow)
        karaoke.onState = { [weak self] active,pending,message in
            guard let self=self else { return }
            self.karaokeSwitch.state=active ? .on : .off; self.karaokeSwitch.isEnabled = !pending; self.karaokeStatus.stringValue=message
        }
        NotificationCenter.default.addObserver(self, selector: #selector(refreshDisplayPreset), name: NSApplication.didChangeScreenParametersNotification, object: nil)
        window.center(); window.makeKeyAndOrderFront(nil); NSApplication.shared.activate(ignoringOtherApps: true); refresh(); runServerControls("status"); refreshDisplayPreset(); renderPreviewIfRequested(); verifyWindowIfRequested()
    }
    @objc func refreshDisplayPreset() { runDisplayPreset(nil) }
    @objc func displayPresetChanged() {
        let index = displayPresetPicker.indexOfSelectedItem - 1
        guard displayPresetOptions.indices.contains(index) else { refreshDisplayPreset(); return }
        runDisplayPreset(displayPresetOptions[index].id)
    }
    func runDisplayPreset(_ preset: String?) {
        guard displayPresetPicker != nil, !displayPresetBusy else { return }
        displayPresetBusy = true; displayPresetPicker.isEnabled = false; displayPresetRefresh.isEnabled = false
        displayPresetStatus.stringValue = preset == nil ? "Checking display…" : "Applying preset…"
        displayPresetQueue.async { [weak self] in
            var reading: DisplayPresetReading?, message: String?
            do { reading = try preset.map { try DisplayPresets.apply($0) } ?? DisplayPresets.read() }
            catch { message = error.localizedDescription; reading = try? DisplayPresets.read() }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.displayPresetBusy = false; self.displayPresetReading = reading
                self.displayPresetPicker.selectItem(at: reading?.current.flatMap { current in self.displayPresetOptions.firstIndex(where: { $0.id == current }).map { $0 + 1 } } ?? 0)
                for (index, option) in self.displayPresetOptions.enumerated() { self.displayPresetPicker.item(at: index + 1)?.isEnabled = reading?.available.contains(option.id) == true }
                self.displayPresetPicker.isEnabled = reading != nil; self.displayPresetRefresh.isEnabled = true
                self.displayPresetStatus.stringValue = message ?? reading?.summary ?? "Display unavailable"
            }
        }
    }
    @objc func karaokeChanged() { if karaokeSwitch.state == .on { karaoke.start() } else { karaoke.stop() } }
    @objc func karaokePitchChanged() {
        karaokeKey=max(-12,min(12,Int(karaokeSlider.doubleValue.rounded()))); karaokeSlider.doubleValue=Double(karaokeKey)
        karaokeValue.stringValue=karaokeKey>0 ? "+\(karaokeKey)" : "\(karaokeKey)"; karaoke.setPitch(karaokeKey)
    }
    @objc func karaokeResetClicked() { karaokeSlider.doubleValue=0; karaokePitchChanged() }
    func renderPreviewIfRequested() {
        guard let index = CommandLine.arguments.firstIndex(of: "--render-preview"), CommandLine.arguments.count > index + 1 else { return }
        let destination = CommandLine.arguments[index + 1]
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            if self.cyborgBusy || self.displayPresetBusy { self.renderPreviewIfRequested(); return }
            guard let view = self.window.contentView, let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { exit(2) }
            view.cacheDisplay(in: view.bounds, to: bitmap)
            let flattened = NSImage(size:view.bounds.size)
            flattened.lockFocus(); NSColor.windowBackgroundColor.setFill(); NSBezierPath(rect:view.bounds).fill(); bitmap.draw(in:view.bounds); flattened.unlockFocus()
            guard let tiff = flattened.tiffRepresentation, let result = NSBitmapImageRep(data:tiff), let data = result.representation(using:.png,properties:[:]) else { exit(2) }
            do { try data.write(to: URL(fileURLWithPath: destination)) } catch { exit(2) }
            print("Rendered Mac window: \(Int(view.bounds.width))×\(Int(view.bounds.height)); controls confirmed: \(self.serverControlsKnown)")
            NSApplication.shared.terminate(nil)
        }
    }
    func verifyWindowIfRequested() {
        guard CommandLine.arguments.contains("--verify-ui") else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            if self.cyborgBusy || self.displayPresetBusy { self.verifyWindowIfRequested(); return }
            guard let close = self.window.standardWindowButton(.closeButton), let mini = self.window.standardWindowButton(.miniaturizeButton), let frame = self.window.contentView?.superview else { exit(3) }
            for button in [close,mini] {
                let center = NSPoint(x:button.bounds.midX,y:button.bounds.midY)
                let hit = frame.hitTest(button.convert(center,to:frame))
                guard button.isEnabled, !button.isHidden, let hit = hit, hit === button || hit.isDescendant(of:button) else { exit(4) }
            }
            guard type(of: self.window!) == NSWindow.self, self.window.isMovable,
                  !self.window.isMovableByWindowBackground,
                  self.window.titlebarAccessoryViewControllers.isEmpty,
                  self.window.contentView is WindowBackdrop else { exit(5) }
            guard frame.hitTest(NSPoint(x: frame.bounds.midX, y: frame.bounds.maxY - 12)) != nil else { exit(8) }
            for control in [self.actionButton!, self.karaokeSwitch!, self.karaokeSlider!, self.displayPresetPicker!, self.displayPresetRefresh!] as [NSView] {
                let hit = frame.hitTest(control.convert(NSPoint(x: control.bounds.midX, y: control.bounds.midY), to: frame))
                guard let hit = hit, hit === control || hit.isDescendant(of: control) else { exit(9) }
            }
            guard let content = self.window.contentView, content.bounds.contains(self.karaokeStatus.convert(self.karaokeStatus.bounds, to: content)) else { exit(10) }
            mini.performClick(nil)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                guard self.window.isMiniaturized else { exit(6) }
                self.window.deminiaturize(nil)
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                    close.performClick(nil)
                    guard !self.window.isVisible else { exit(7) }
                    print("Native title-bar hit tests, Minimize, Close and draggable background passed")
                    NSApplication.shared.terminate(nil)
                }
            }
        }
    }
    func setCyborgEnabled(_ enabled: Bool) {
        for control in [fanSwitch,nightSwitch,guardSwitch,tasksSwitch,speakerSwitch] { control?.isEnabled = enabled && serverControlsKnown }
        readControls.isEnabled = enabled
    }
    @objc func readServerControls() { runServerControls("status") }
    @objc func guardChanged() { runServerControls(guardSwitch.state == .on ? "guard-on" : "guard-off") }
    @objc func tasksChanged() { runServerControls(tasksSwitch.state == .on ? "tasks-on" : "tasks-off") }
    @objc func fanChanged() { runServerControls(fanSwitch.state == .on ? "fan-on" : "fan-off") }
    @objc func nightChanged() { runServerControls(nightSwitch.state == .on ? "night-on" : "night-off") }
    @objc func speakerChanged() { runServerControls(speakerSwitch.state == .on ? "speaker-on" : "speaker-off") }
    func runServerControls(_ action: String) {
        guard !cyborgBusy else { return }
        let gate = "/home/tameszaza/.local/bin/cyborg-server-tasks"
        let commands = ["status":"", "tasks-on":gate+" on", "tasks-off":gate+" off",
            "guard-on":"/usr/bin/sudo -n /usr/local/libexec/cyborg-gpu-guard/control on", "guard-off":"/usr/bin/sudo -n /usr/local/libexec/cyborg-gpu-guard/control off",
            "fan-on":"/usr/bin/sudo -n /usr/local/libexec/cyborg-controls/fan on", "fan-off":"/usr/bin/sudo -n /usr/local/libexec/cyborg-controls/fan off",
            "night-on":"/usr/bin/sudo -n /usr/local/libexec/cyborg-controls/night on", "night-off":"/usr/bin/sudo -n /usr/local/libexec/cyborg-controls/night off",
            "speaker-on":"/home/tameszaza/.local/bin/cyborg-speaker on", "speaker-off":"/home/tameszaza/.local/bin/cyborg-speaker off"]
        guard let change = commands[action] else { return }
        let script = """
        import subprocess,json
        def read(args):
            return subprocess.run(args,check=True,capture_output=True,text=True,timeout=8).stdout
        result=json.loads(read(['\(gate)','desktop-status']))
        result['fan']=read(['/usr/bin/sudo','-n','/usr/local/libexec/cyborg-controls/fan','status'])
        result['night']=read(['/usr/bin/sudo','-n','/usr/local/libexec/cyborg-controls/night','status'])
        result['speaker']=read(['/home/tameszaza/.local/bin/cyborg-speaker','status'])
        print(json.dumps(result))
        """
        let remote = (change.isEmpty ? "" : change+" >/dev/null && ")+"/usr/bin/python3 -c "+shellQuote(script)
        cyborgBusy = true; setCyborgEnabled(false); cyborgStatus.stringValue = action == "status" ? "Checking Cyborg…" : "Updating Cyborg…"
        DispatchQueue.global().async { [weak self] in
            let process = Process(), pipe = Pipe()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
            process.arguments = ["-T","-o","BatchMode=yes","-o","ConnectTimeout=8","-o","ServerAliveInterval=5","-o","ServerAliveCountMax=2","100.66.32.22",remote]
            process.standardInput = FileHandle.nullDevice; process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
            var reading: CyborgReading?
            do {
                try process.run()
                let deadline = DispatchWorkItem { if process.isRunning { process.terminate() } }
                DispatchQueue.global().asyncAfter(deadline: .now() + (action.hasPrefix("speaker-") ? 55 : 25),execute: deadline)
                let data = pipe.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit(); deadline.cancel()
                if process.terminationStatus == 0 { reading = CyborgReading.parse(String(data:data.prefix(8192),encoding:.utf8) ?? "") }
            } catch { }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.cyborgBusy = false; self.serverControlsKnown = reading != nil
                if let r = reading {
                    self.controlReading = r; self.guardSwitch.state = r.guardOn ? .on : .off; self.tasksSwitch.state = r.accepting ? .on : .off
                    self.fanSwitch.state = r.fanOn ? .on : .off; self.nightSwitch.state = r.nightOn ? .on : .off; self.speakerSwitch.state = r.speakerOn ? .on : .off
                    self.fanSwitch.toolTip = "Fan: "+r.rpm+" RPM. Off restores automatic cooling."
                    self.cyborgStatus.stringValue = "Up to date"
                } else {
                    if let r = self.controlReading { self.guardSwitch.state = r.guardOn ? .on : .off; self.tasksSwitch.state = r.accepting ? .on : .off; self.fanSwitch.state = r.fanOn ? .on : .off; self.nightSwitch.state = r.nightOn ? .on : .off; self.speakerSwitch.state = r.speakerOn ? .on : .off }
                    self.cyborgStatus.stringValue = "Cyborg unavailable · Refresh to retry"
                    if action != "status" { let alert = NSAlert(); alert.messageText = "Could not confirm the change"; alert.informativeText = "Check Cyborg and Tailscale, then refresh its controls."; alert.runModal() }
                }
                self.setCyborgEnabled(true)
            }
        }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { window.makeKeyAndOrderFront(nil); refresh(); runServerControls("status"); refreshDisplayPreset(); return true }
    func applicationDidBecomeActive(_ notification: Notification) { if window != nil && !busy { refresh(); refreshDisplayPreset() } }
    func refresh() {
        let on = connectedPID(uid) != nil
        status.stringValue = on ? "Connected" : "Disconnected"
        actionButton.title = on ? "Disconnect" : "Connect"
        actionButton.bezelColor = on ? nil : .controlAccentColor
        actionButton.isEnabled = !busy
        updateActivity(connected: on)
    }
    func updateActivity(connected: Bool? = nil) {
        guard statusDot != nil, spinner != nil else { return }
        statusDot.isHidden = busy; spinner.isHidden = !busy
        if busy { spinner.startAnimation(nil) } else {
            spinner.stopAnimation(nil)
            statusDot.layer?.backgroundColor = ((connected ?? (connectedPID(uid) != nil)) ? NSColor.systemGreen : NSColor.secondaryLabelColor).cgColor
        }
    }
    func fail(_ message: String) {
        busy = false; authenticating = false; request?.cancel(); request = nil; closeBrowser(); cleanupTransfer(); refresh()
        let alert = NSAlert(); alert.messageText = "NTU VPN"; alert.informativeText = message; alert.runModal()
    }
    func closeBrowser() { web?.stopLoading(); web?.navigationDelegate = nil; web?.uiDelegate = nil; web = nil; webWindow?.delegate = nil; webWindow?.close(); webWindow = nil; authFields.removeAll() }
    func cleanupTransfer() { if let dir = transferDirectory { try? FileManager.default.removeItem(atPath: dir); transferDirectory = nil } }
    @objc func toggleClicked() {
        guard !busy else { return }
        if connectedPID(uid) != nil { disconnectClicked() } else { connectClicked() }
    }
    @objc func connectClicked() {
        guard !busy, connectedPID(uid) == nil else { return }
        guard FileManager.default.isExecutableFile(atPath: openconnect) else { fail("OpenConnect is missing. Reinstall it with Homebrew before connecting."); return }
        busy = true; authenticating = true; actionButton.isEnabled = false
        status.stringValue = "Signing in…"
        var req = URLRequest(url: URL(string: "https://" + portal + "/ssl-vpn/prelogin.esp")!)
        req.httpMethod = "POST"; req.timeoutInterval = 25
        req.setValue("PAN GlobalProtect", forHTTPHeaderField: "User-Agent")
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("tmp=tmp&kerberos-support=yes&ipv6-support=yes&clientVer=4100&clientos=Windows".utf8)
        request = URLSession.shared.dataTask(with: req) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self = self, self.authenticating else { return }
                guard error == nil, (response as? HTTPURLResponse)?.statusCode == 200, let data = data else { self.fail("Could not reach NTU’s VPN portal. Check your internet connection and try again."); return }
                let fields = XMLValues.parse(data)
                guard fields["status"] == "Success", let encoded = fields["saml-request"], let content = Data(base64Encoded: encoded), let login = String(data: content, encoding: .utf8) else { self.fail("NTU did not return a supported sign-in request. No VPN connection was started."); return }
                self.showBrowser(login, method: fields["saml-auth-method"] ?? "")
            }
        }; request?.resume()
    }
    func showBrowser(_ login: String, method: String) {
        // Retain the user's NTU/Microsoft browser session between manual connections.
        // NTU still controls token expiry and MFA; no password is saved by this app.
        let configuration = WKWebViewConfiguration(); configuration.websiteDataStore = .default()
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 860, height: 680), configuration: configuration)
        web!.navigationDelegate = self; web!.uiDelegate = self
        webWindow = NSWindow(contentRect: web!.frame, styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        webWindow!.title = "NTU VPN — Student sign-in"; webWindow!.contentView = web; webWindow!.delegate = self
        webWindow!.isReleasedWhenClosed = false; webWindow!.center(); webWindow!.makeKeyAndOrderFront(nil)
        status.stringValue = "Signing in…"
        if method == "REDIRECT", let url = URL(string: login), url.scheme == "https" { web!.load(URLRequest(url: url)) }
        else if method == "POST" { web!.loadHTMLString(login, baseURL: URL(string: "https://" + portal)) }
        else { fail("NTU returned an unsupported browser sign-in method.") }
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if response.response.url?.host == portal, let http = response.response as? HTTPURLResponse {
            for (key, value) in http.allHeaderFields { let k = String(describing: key).lowercased(); if ["saml-auth-status", "saml-username", "prelogin-cookie", "portal-userauthcookie"].contains(k) { authFields[k] = String(describing: value) } }
            if let c = extractCredentials(authFields) { decisionHandler(.cancel); authenticated(c); return }
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "globalprotectcallback" {
            let sourceHost = action.sourceFrame.request.url?.host
            if sourceHost == portal {
                let raw = String(url.absoluteString.dropFirst("globalprotectcallback:".count)).trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
                if let data = Data(base64Encoded: raw.removingPercentEncoding ?? raw), let html = String(data: data, encoding: .utf8), let c = htmlCredentials(html) { decisionHandler(.cancel); authenticated(c); return }
            }
            decisionHandler(.cancel); return
        }
        guard ["https", "about", "data"].contains(url.scheme ?? "") else { decisionHandler(.cancel); return }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard authenticating, webView.url?.host == portal else { return }
        webView.evaluateJavaScript("document.documentElement.outerHTML") { [weak self] value, _ in
            guard let self = self, self.authenticating, let html = value as? String, let c = htmlCredentials(html) else { return }
            self.authenticated(c)
        }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard authenticating, (error as NSError).code != NSURLErrorCancelled else { return }
        fail("The NTU sign-in page could not load. Check your connection and try again.")
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if action.targetFrame == nil { webView.load(action.request) }; return nil
    }
    func authenticated(_ credentials: Credentials) {
        guard authenticating else { return }; authenticating = false; closeBrowser()
        status.stringValue = "Connecting…"
        let directory = "/private/tmp/ntu-vpn-\(uid)-" + UUID().uuidString
        do { try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]) }
        catch { fail("Could not prepare a private connection channel."); return }
        transferDirectory = directory; let fifo = directory + "/credentials"
        guard mkfifo(fifo, 0o600) == 0, let payload = try? JSONEncoder().encode(credentials) else { fail("Could not prepare the connection."); return }
        let finished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            signal(SIGPIPE, SIG_IGN)
            let deadline = Date().addingTimeInterval(180)
            while Date() < deadline {
                if finished.wait(timeout: .now()) == .success { return }
                let fd = open(fifo, O_WRONLY | O_NONBLOCK | O_NOFOLLOW)
                if fd >= 0 {
                    let bytes = payload + Data([10])
                    bytes.withUnsafeBytes { buffer in
                        var offset = 0
                        while offset < buffer.count && Date() < deadline {
                            let n = write(fd, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                            if n > 0 { offset += n } else if errno == EAGAIN { usleep(50000) } else { break }
                        }
                    }; close(fd); return
                }
                usleep(50000)
            }
        }
        authorize(["--root-connect", String(uid), fifo]) { [weak self] success, reason in
            finished.signal()
            guard let self = self else { return }; self.cleanupTransfer(); self.busy = false
            if success && connectedPID(self.uid) != nil { self.refresh() }
            else { self.fail(reason ?? "OpenConnect could not establish the tunnel. This does not establish that your NTU VPN access is missing.") }
        }
    }
    func authorize(_ arguments: [String], completion: @escaping (Bool, String?) -> Void) {
        let executable = Bundle.main.executablePath!
        let cmd = ([executable] + arguments).map(shellQuote).joined(separator: " ")
        let source = "do shell script " + appleQuote(cmd) + " with administrator privileges"
        DispatchQueue.global().async {
            // Run AppleScript in its own transient process; NSAppleScript is main-thread-only.
            let process = Process(), input = Pipe(), output = Pipe(), diagnostic = Pipe()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            process.standardInput = input; process.standardOutput = output; process.standardError = diagnostic
            do { try process.run() }
            catch { DispatchQueue.main.async { completion(false, "Could not open the macOS administrator prompt.") }; return }
            input.fileHandleForWriting.write(Data(source.utf8)); try? input.fileHandleForWriting.close()
            let result = output.fileHandleForReading.readDataToEndOfFile()
            let errorData = diagnostic.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
            let value = String(data: result, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let success = process.terminationStatus == 0 && ["CONNECTED", "DISCONNECTED"].contains(value)
            let error = String(data: errorData, encoding: .utf8) ?? ""
            let combined = value + error
            let reason: String?
            if combined.contains("(-128)") { reason = "macOS administrator authentication was cancelled. Choose Connect again to retry." }
            else if combined.contains("VPN_ERROR:TLS") { reason = "The VPN’s TLS certificate could not be verified. No certificate validation was bypassed." }
            else if combined.contains("VPN_ERROR:LOGIN") { reason = "NTU rejected the gateway authentication. The browser sign-in succeeded, but the tunnel login needs investigation." }
            else if combined.contains("VPN_ERROR:SAML") { reason = "The gateway requested another SAML sign-in. The browser session was not accepted for the tunnel." }
            else if combined.contains("VPN_ERROR:TUN") { reason = "OpenConnect could not create the Mac VPN interface." }
            else if combined.contains("VPN_ERROR:NETWORK") { reason = "OpenConnect could not reach the VPN gateway. Check your network connection." }
            else if !success { reason = "The VPN connection helper failed. Try Connect again; your NTU access has not been determined to be missing." }
            else { reason = nil }
            // Only a fixed category is displayed; never log raw authentication diagnostics.
            DispatchQueue.main.async { completion(success, reason) }
        }
    }
    @objc func disconnectClicked() {
        guard !busy else { return }; busy = true; actionButton.isEnabled = false
        status.stringValue = "Disconnecting…"
        authorize(["--root-disconnect", String(uid)]) { [weak self] success, _ in
            guard let self = self else { return }; self.busy = false
            if success { self.refresh() } else { self.fail("Disconnection was not completed. Choose Disconnect again and approve the administrator prompt.") }
        }
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if CommandLine.arguments.contains("--verify-ui"), sender === window { return true }
        if sender === webWindow { authenticating = false; busy = false; request?.cancel(); DispatchQueue.main.async { self.closeBrowser(); self.refresh() }; return true }
        if busy || cyborgBusy || displayPresetBusy { return false }; NSApplication.shared.terminate(nil); return true
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { (cyborgBusy || displayPresetBusy || (busy && !authenticating)) ? .terminateCancel : .terminateNow }
    func applicationWillTerminate(_ notification: Notification) { karaoke.stopSync(); request?.cancel(); cleanupTransfer() }
}

if CommandLine.arguments.contains("--root-connect") || CommandLine.arguments.contains("--root-disconnect") { rootControl() }
if CommandLine.arguments.contains("--status") { print(connectedPID(getuid()) == nil ? "Disconnected" : "Connected"); exit(0) }
if CommandLine.arguments.contains("--display-presets-status") || CommandLine.arguments.contains("--display-preset") {
    do {
        let reading: DisplayPresetReading
        if let index = CommandLine.arguments.firstIndex(of: "--display-preset") {
            guard CommandLine.arguments.count > index + 1 else { throw DisplayPresetError.message("Choose original or ten-bit.") }
            reading = try DisplayPresets.apply(CommandLine.arguments[index + 1])
        } else { reading = try DisplayPresets.read() }
        print(String(data: try JSONEncoder().encode(reading), encoding: .utf8)!); exit(0)
    } catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
}
if CommandLine.arguments.contains("--self-test") {
    let sample = "<html><!-- <saml-auth-status>1</saml-auth-status><prelogin-cookie>test&amp;value</prelogin-cookie><saml-username>test@example.invalid</saml-username> --></html>"
    guard let c = htmlCredentials(sample), c.cookie == "test&value", c.username == "test@example.invalid", !validCredentials(Credentials(username: "x", cookie: "bad\nvalue", cookieName: "prelogin-cookie")), extractCredentials(["saml-auth-status": "0", "saml-username": "x", "prelogin-cookie": "x"]) == nil else { exit(1) }
    guard let fan = FanReading.parse("Cooler Boost: ON | Fan: 6956 RPM | EC: 15K1IMS1.113"), fan.on, fan.rpm == "6956",
          FanReading.parse("Cooler Boost: OFF | Fan: 3555 RPM | EC: 15K1IMS1.113")?.on == false,
          FanReading.parse("unexpected output") == nil else { exit(1) }
    guard NightReading.parse("Night mode: ON")?.on == true,
          NightReading.parse("Night mode: OFF\n")?.on == false,
          NightReading.parse("Night mode: ON\nunexpected") == nil,
          NightReading.parse("unexpected") == nil else { exit(1) }
    guard ServerControlsReading.parse("{\"guard\":true,\"accepting\":false}")?.guardOn == true,
          ServerControlsReading.parse("{\"guard\":false,\"accepting\":true}")?.accepting == true,
          ServerControlsReading.parse("{\"guard\":true}") == nil,
          ServerControlsReading.parse("{\"guard\":\"on\",\"accepting\":true}") == nil else { exit(1) }
    let snapshot: [String:Any] = ["guard":true,"accepting":true,"fan":"Cooler Boost: OFF | Fan: 3555 RPM | EC: 15K1IMS1.113","night":"Night mode: OFF","speaker":"Speaker Mode: ON\nStart at login/boot: OFF\n"]
    let raw = String(data:try! JSONSerialization.data(withJSONObject:snapshot),encoding:.utf8)!
    guard let parsed = CyborgReading.parse(raw), parsed.speakerOn, !parsed.fanOn, !parsed.nightOn, CyborgReading.parse("{}") == nil else { exit(1) }
    print("SAML, credential and all five Cyborg control parsers passed"); exit(0)
}
let app = NSApplication.shared, delegate = AppDelegate()
app.setActivationPolicy(.regular); app.delegate = delegate; app.run()
