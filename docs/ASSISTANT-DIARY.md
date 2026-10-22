# Assistant diary logging

Verified 22 October 2026.

## Use

Sign in as Admin, open Assistant, and keep workspace context enabled. Use **Log a photo** or attach an image normally and ask to log/analyse it. The assistant writes a short heading and Markdown appropriate to the request; there is no fixed food/receipt template. Explicit “don't save”, “chat only” or “draft” requests stay in chat. Analysis uses the existing configured external model provider; it is not local inference.

A successful save returns **Open diary**. Entries append to `Diary/YYYY-MM-DD.md` and add a missing date link to `Diary.md`, preserving existing text. New notes carry the existing `diary` tag. Timestamp/date use the server calendar timezone (Asia/Singapore at verification); timestamp is logging time, not necessarily the time pictured. Entry order: time/title, original stored image, generated Markdown. Image interpretations remain machine analysis.

## Storage and safety

`lib/assistant_diary.py` exposes an Admin/request-scoped tool through existing chat tools, protected by the chat CSRF nonce and workspace-context setting. `routes/obsidian_images.py:store_image` reuses existing normalized, SHA-256-deduplicated image storage and read URLs. Media remain online attachments, not offline vault copies.

On save only, `deploy/obsidian-diary.mjs` launches the existing Node/CompatibleClient runtime and existing encrypted LiveSync profile. It writes only the daily note and diary index through live-revision compare-and-swap, retries concurrent edits, and verifies both writes. Existing unresolved conflicts stop the operation. Ordinary offline edits still follow LiveSync conflict handling.

Stable browser message IDs and a small lazy SQLite receipt store at `/srv/tamestorage/.tamestorage_state/assistant-diary.sqlite` make retries idempotent, including interrupted index updates and crossing midnight. Preserve generated `^log-…` block IDs. Regenerate retains the original entry; send a new message for an additional entry. Pending payloads survive until completion; completed rows retain receipts, not a second archive of diary bodies.

Compose reuses readonly `/home/tameszaza/.local/lib/goodnotes-links` at `/opt/livesync` and the existing private config at `/run/obsidian-livesync.json`. Never print/copy that profile into documentation. Web joins the existing `obsidian-livesync_default` private Docker network as `vault-sync`; no new published port/firewall rule. Preserve diary Python/chat JS readonly binds alongside prior binds. No new resident worker, polling loop, Mac model/index or independent sync engine.

## Obsidian

Server Image Paste 1.2.3 handles `obsidian://tames-diary?vault=Notes&date=YYYY-MM-DD`: one native LiveSync Sync now, wait for reflection, then open the daily note. Listeners/timeouts are bounded and removed; no idle polling or sync-preference changes. Mac package was published through native MacOS customization. iPad must apply that updated package once; physical iPad application remains unverified. Regular LiveSync can receive notes independently of this shortcut.

## Verification and maintenance

Unit/regression coverage: diary writer, calendar routing, shared image storage, chat diary service and mobile-safe URI lifecycle. Real model checks verified log/analyse tool use and explicit no-save behavior. Isolated encrypted LiveSync fixtures verified create/append, retry, exact content and native Mac reflection; fixtures were removed. A real production Python→image store→SQLite→Node CLI→encrypted namespaced note check passed. Browser checks covered desktop/mobile, previews, Open diary, reload/reattach retries and UUID reuse; browser POST responses were isolated test fixtures.

Maintained opt-in integration tests under `tests/diary_*` use strict disposable namespaces; never run them against user diary paths. `deploy/build-diary-image.py` makes an overlay from the current image without reinstalling dependencies; normal source build includes these files too. Do not change LiveSync identity/encryption format independently of the vault. Existing backups/queues/recovery/cleanup rules remain separate; image storage and diary receipts are not temporary upload files.
