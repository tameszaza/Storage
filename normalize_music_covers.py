"""One-time safe repair for non-square embedded playlist artwork."""
from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path

from lib.artwork import AUDIO_SUFFIXES, normalize_embedded_cover


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write verified square covers")
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("MUSIC_FOLDER", "/srv/music")))
    args = parser.parse_args()
    counts: Counter[str] = Counter()
    for path in sorted((args.root / "Managed Playlists").rglob("*")):
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        try:
            outcome = normalize_embedded_cover(path) if args.apply else "candidate"
        except Exception as exc:
            outcome = "error"
            print(f"ERROR {path.name}: {exc}", flush=True)
        counts[outcome] += 1
    print(dict(counts), flush=True)


if __name__ == "__main__":
    main()
