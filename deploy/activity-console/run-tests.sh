#!/bin/sh
set -eu
root=/srv/docker/tamestorage
checks="$root/deploy/activity-console"
docker run --rm --network none --read-only --tmpfs /tmp:size=32m,mode=1777 --user 1000:1000 --memory 192m --memory-swap 192m --cpus 1 -e PYTHONPATH=/checks:/app -v "$checks:/checks:ro" -v "$root/lib/goodnotes_status.py:/app/lib/goodnotes_status.py:ro" -v "$root/lib/goodnotes_download.py:/app/lib/goodnotes_download.py:ro" -v "$root/templates/_goodnotes_warning.html:/app/templates/_goodnotes_warning.html:ro" tamestorage:latest python -m unittest test_activity_console test_presence test_goodnotes_download test_atomic_upload -v
python3 -m unittest discover -s "$checks" -p test_webdav_settings.py -v
