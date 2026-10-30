#!/bin/zsh
set -eu
test_root="${0:A:h}"
test_build=$(mktemp -d /private/tmp/ntu-karaoke-tests.XXXXXX)
trap 'rm -rf "$test_build"' EXIT
xcrun swiftc -O -module-cache-path "$test_build/cache" "$test_root/main.swift" "$test_root/../KaraokeAudio.swift" -o "$test_build/karaoke-tests"
"$test_build/karaoke-tests"
