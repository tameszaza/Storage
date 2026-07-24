from __future__ import annotations

import shutil
import sys
from pathlib import Path

REMOVED_FILES = (
    "routes/speed.py",
    "templates/speed.html",
    "static/js/speed.js",
    "static/css/speed.css",
)


def main() -> int:
    package_root = Path(__file__).resolve().parent
    payload_root = package_root / "payload"
    target_root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser().resolve()

    if not (target_root / "app.py").is_file():
        print(f"Error: {target_root} does not look like the Tamestorage project root.")
        print("Run: python apply_update.py /path/to/Tamestorage")
        return 1

    copied = 0
    for source in payload_root.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(payload_root)
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
        print(f"Updated: {relative}")

    removed = 0
    for relative_name in REMOVED_FILES:
        target = target_root / relative_name
        if target.exists():
            target.unlink()
            removed += 1
            print(f"Removed: {relative_name}")

    for cache_dir in target_root.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)

    print(f"Done. Updated {copied} files and removed {removed} speedometer files.")
    print("Restart Flask, sign in as Admin, and configure the AC panel at /admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
