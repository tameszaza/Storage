# NAS video downloads

Page: `/videos` (Admin account). Files: `/srv/tamestorage/uploads/Admin/Movies/<job UUID>/`.
The live `/usr/local/sbin/tamestorage-backup` excludes `/uploads/Admin/Movies/`.

Direct public HTTPS videos and HDWatch pages are supported. HDWatch uses Chromium
to operate the player normally, starting with server 2. Unencrypted,
multiplexed HLS VOD is saved and remuxed locally to MP4. Unsupported stream types
fail with an explanation. Prefer 1080p, allow 720p, never save below 720p.
Each missing segment receives six attempts. Gaps may total at most 30 seconds
or 2% of the running time, whichever is smaller. Saved videos with gaps retain
a visible warning including the missing part numbers and estimated duration.
Duration is checked against the playlist with the recorded gap allowance.
Delete stops the job and permanently removes its folder.

The worker is single instance, uses a persistent SQLite queue, and restarts with
Docker. Interrupted downloads restart from the beginning with fresh stream links.

Build on server from `/srv/docker/tamestorage`:

```
docker compose build web
# Only needed initially or when refreshing browser dependencies:
docker build -f Dockerfile.video-base -t tamestorage-video:browser-base .
docker compose build video-worker
docker compose up -d --no-build web video-worker
```

Tests `test_video_downloads.py` and `test_video_streams.py` can run inside the
video-worker image; they use temporary storage and do not modify the NAS library.
