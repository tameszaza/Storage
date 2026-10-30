# Goodnotes statistics

A separate, private Docker service for low-priority analysis of exported PDFs. The Flask app provides the authenticated UI at `/goodnotes/statistics`; the browser cannot reach this service directly. No OCR, model or Mac daemon is involved.

`analyze.py` uses pinned PyMuPDF core and fontTools Bézier arc lengths. Native PDF Ink records are counted directly. Quartz rounded open vector paths are an estimate of handwriting, not a physical pen event log. Distance and widths use PDF page scale, not physical hand motion; rasterized pages are not recoverable, printed graphics may match, and duplicate exports count twice. Erased strokes and historical writing are unavailable. Source PDFs are mounted read only.

`store.py` checkpoints pages in SQLite on the NAS. Inode/size/nanosecond mtime identify a changed export; only that file is recalculated. Completed files are reused. A complete inventory must succeed before removed files leave the cache. Metadata paths reject traversal and symlinks. `service.py` opens one child process per page and releases parser memory afterward. Read requests use cached results, never PDF extraction.

The worker checks existing OCR/index/fallback locks and the Quartz build marker before and during a page. When essential work begins, it cancels the current page, preserves completed checkpoints, and waits. A CPU quota, low CPU weight, nice 19, 192 MiB memory limit with no extra swap and a 30-second page timeout bound it further. It also waits when available memory is low or load is high. This is best-effort priority control, not zero impact on a shared HDD.

Configure `/etc/tamestorage-goodnotes-stats.env` from [.env.example](.env.example). The root [compose.yaml](../../compose.yaml) defines the container limits and read-only mounts. No host port or Docker socket is exposed. `/health`, `/summary`, `/files`, `/file`, and POST `/control` are internal endpoints. Pause, resume and rescan are available through the Admin UI. Manual container Stop persists through the existing restricted Docker control broker.

Tests: `docker run --rm --network none --tmpfs /tmp:size=32m,mode=1777 tamestorage-goodnotes-stats:1 python -B -m unittest test_stats -v`. Eight tests cover line/cubic geometry, native ink records, printed-shape exclusion, unchanged sources, cache invalidation, unavailable libraries, path safety, pause and critical-job admission.

Deployment verification on 2026-10-30: anonymous redirect, authenticated page/API, CSRF rejection/acceptance; Chromium at 1440/390 px with production styles in dark/light themes, no horizontal overflow or JavaScript errors. Real exports were inspected from AB1201 Note, CX1005 Tut7 solution, and MH1812 Chapter6 homework. Counts are descriptive estimates, not an audit of Goodnotes' native stroke history. Initial analysis intentionally waits for critical workloads to finish.
