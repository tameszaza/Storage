#!/bin/zsh
set -eu
app_root="${0:A:h:h:h}"
build_cache=$(mktemp -d /private/tmp/ntu-vpn-swift.XXXXXX)
trap 'rm -rf "$build_cache"' EXIT
xcrun swiftc -O -module-cache-path "$build_cache" -framework Cocoa -framework WebKit -framework CoreAudio -framework AVFoundation "$app_root/Contents/Resources/main.swift" "$app_root/Contents/Resources/KaraokeAudio.swift" "$app_root/Contents/Resources/DisplayPresets.swift" -o "$build_cache/NTUVPN"
mv "$build_cache/NTUVPN" "$app_root/Contents/MacOS/NTUVPN"
codesign --force --sign - "$app_root"
