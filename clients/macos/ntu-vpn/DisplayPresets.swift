import Cocoa

struct DisplayPreset: Decodable {
    let id: String, title: String
    let width: Int, height: Int, pixelWidth: Int, pixelHeight: Int
    let refreshRate: Double
    func matches(_ mode: CGDisplayMode) -> Bool {
        mode.width == width && mode.height == height && mode.pixelWidth == pixelWidth && mode.pixelHeight == pixelHeight && abs(mode.refreshRate - refreshRate) < 0.1
    }
}
struct DisplayPresetConfiguration: Decodable {
    let displayUUID: String
    let vendor: UInt32, model: UInt32, serial: UInt32
    let presets: [DisplayPreset]
    static func load() throws -> DisplayPresetConfiguration {
        guard let url = Bundle.main.url(forResource: "DisplayPresets", withExtension: "json") else { throw DisplayPresetError.message("Display presets are missing.") }
        let value = try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
        guard !value.displayUUID.isEmpty, value.vendor != 0, value.model != 0,
              !value.presets.isEmpty, Set(value.presets.map(\.id)).count == value.presets.count,
              value.presets.allSatisfy({ !$0.id.isEmpty && $0.width > 0 && $0.height > 0 && $0.pixelWidth == $0.width * 2 && $0.pixelHeight == $0.height * 2 && $0.refreshRate.isFinite && $0.refreshRate >= 24 && $0.refreshRate <= 240 }) else { throw DisplayPresetError.message("Display presets are invalid.") }
        return value
    }
}
enum DisplayPresetError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let value) = self { return value }; return nil }
}
struct DisplayPresetReading: Encodable {
    let current: String?
    let available: [String]
    let width: Int, height: Int, pixelWidth: Int, pixelHeight: Int
    let refreshRate: Double
    var summary: String {
        let scale = pixelWidth == width * 2 && pixelHeight == height * 2 ? " HiDPI" : ""
        return "\(width) × \(height)\(scale) · \(Int(refreshRate.rounded())) Hz"
    }
}

// Uses the Mac's existing modes and resolves runtime IDs afresh. No DDC command,
// color-profile edit, gamma dimming, external app, driver or resident helper.
// The preset's connection depth is independently verified on this display/cable;
// CoreGraphics itself reports the rendering mode, not HDMI transport bit depth.
enum DisplayPresets {
    private static func target(_ config: DisplayPresetConfiguration) throws -> CGDirectDisplayID {
        var displays = [CGDirectDisplayID](repeating: 0, count: 32), count: UInt32 = 0
        guard CGGetOnlineDisplayList(32, &displays, &count) == .success else { throw DisplayPresetError.message("Could not read connected displays.") }
        let matches = displays.prefix(Int(count)).filter { id in
            guard CGDisplayIsBuiltin(id) == 0, CGDisplayVendorNumber(id) == config.vendor,
                  CGDisplayModelNumber(id) == config.model, CGDisplaySerialNumber(id) == config.serial,
                  let uuid = CGDisplayCreateUUIDFromDisplayID(id)?.takeRetainedValue() else { return false }
            return CFUUIDCreateString(nil, uuid) as String == config.displayUUID
        }
        guard matches.count == 1, let id = matches.first else { throw DisplayPresetError.message("Connect your PXP 27-QGG to this Mac.") }
        guard CGDisplayIsInMirrorSet(id) == 0 else { throw DisplayPresetError.message("Turn off mirroring to use these presets.") }
        return id
    }
    private static func modes(_ id: CGDirectDisplayID) -> [CGDisplayMode] {
        CGDisplayCopyAllDisplayModes(id, [kCGDisplayShowDuplicateLowResolutionModes: true] as CFDictionary) as? [CGDisplayMode] ?? []
    }
    private static func configure(_ id: CGDirectDisplayID, mode: CGDisplayMode) -> CGError {
        var config: CGDisplayConfigRef?
        let begin = CGBeginDisplayConfiguration(&config)
        guard begin == .success, let config = config else { return begin == .success ? .failure : begin }
        let result = CGConfigureDisplayWithDisplayMode(config, id, mode, nil)
        guard result == .success else { CGCancelDisplayConfiguration(config); return result }
        // A standalone CGDisplaySetDisplayMode can alter the rendering mode
        // without changing this flexible-scaled display's HDMI timing. Commit
        // a complete native configuration, as the system Displays UI does.
        return CGCompleteDisplayConfiguration(config, .permanently)
    }
    static func read() throws -> DisplayPresetReading {
        let config = try DisplayPresetConfiguration.load(), id = try target(config)
        guard let current = CGDisplayCopyDisplayMode(id) else { throw DisplayPresetError.message("Could not read the current display mode.") }
        let all = modes(id)
        return DisplayPresetReading(current: config.presets.first(where: { $0.matches(current) })?.id,
            available: config.presets.filter { preset in all.contains(where: { preset.matches($0) }) }.map(\.id),
            width: current.width, height: current.height, pixelWidth: current.pixelWidth, pixelHeight: current.pixelHeight, refreshRate: current.refreshRate)
    }
    static func apply(_ presetID: String) throws -> DisplayPresetReading {
        let config = try DisplayPresetConfiguration.load()
        guard let preset = config.presets.first(where: { $0.id == presetID }) else { throw DisplayPresetError.message("Unknown display preset.") }
        let id = try target(config)
        guard let previous = CGDisplayCopyDisplayMode(id), let desired = modes(id).first(where: { preset.matches($0) }) else { throw DisplayPresetError.message("This preset is unavailable on the current connection.") }
        guard configure(id, mode: desired) == .success else { throw DisplayPresetError.message("macOS could not apply the display preset.") }
        usleep(300_000)
        guard let actual = CGDisplayCopyDisplayMode(id), preset.matches(actual) else {
            _ = configure(id, mode: previous)
            throw DisplayPresetError.message("Could not confirm the preset. The previous mode was restored.")
        }
        return try read()
    }
}
