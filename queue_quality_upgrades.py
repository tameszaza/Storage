"""Scan saved HDWatch videos and optionally queue safe 1080p upgrades.

Run inside the video-worker container: python /app/queue_quality_upgrades.py --apply
Without --apply this only reports candidates. Re-running does not duplicate jobs.
"""
import argparse
from collections import Counter
from urllib.parse import urlsplit
from lib import video_downloads as jobs

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    counts = Counter()
    for row in jobs.items():
        if row['status'] != 'ready' or urlsplit(row['url']).hostname not in ('hdwatch.pro','www.hdwatch.pro'):
            continue
        path = jobs.folder(row['id'], row) / row['filename']
        try:
            height, duration, audio = jobs.media_info(path)
            if not height or not duration or path.is_symlink():
                raise ValueError('Unverifiable original')
        except Exception as exc:
            print(row['title'], 'SKIPPED', str(exc), flush=True)
            counts['unverifiable'] += 1
            continue
        counts[str(height)+'p'] += 1
        if height >= 1080:
            continue
        if args.apply:
            with jobs.connection() as db:
                changed = db.execute("UPDATE downloads SET status='queued', message='1080p upgrade queued; original retained' WHERE id=? AND status='ready' AND filename=?",
                                     (row['id'], row['filename'])).rowcount
            counts['queued'] += changed
        else:
            counts['would_queue'] += 1
        print(row['title'], str(height)+'p', 'queued' if args.apply else 'candidate', flush=True)
    print(dict(counts), flush=True)

if __name__ == '__main__':
    main()
