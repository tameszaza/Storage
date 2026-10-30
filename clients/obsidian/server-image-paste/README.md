# Server Image Paste

Pasted images upload to the configured private server and become ordinary Markdown image links. Tailscale is needed to upload and view them. The scoped upload key stays in this plugin's private `data.json`; never copy it into diagnostics or documentation.

HEIC/HEIF/HIF photographs are converted on the server to JPEG. Transparency uses PNG. TIFF and other supported raster clipboard formats become browser-friendly PNG, with JPEG as a size fallback for opaque images. Ordinary PNG, JPEG (including MPO/HDR JPEG), GIF, WebP and AVIF retain their original bytes. Filenames and incomplete clipboard MIME labels do not determine the server's decoder; it checks actual image bytes.

If uploading fails, the original image saves locally using its correct extension. **Settings → Server Image Paste → Image cleanup** uploads local images, updates resolved image references and removes originals only after verifying both the uploaded source fingerprint and the downloaded server image. A changed or still-referenced original is retained. Existing width aliases and annotation JSON are preserved. Tames PDF stores flush and pause only during the reference rewrite, then resume even after an error. Cleanup runs only when requested; there is no retry queue, background scan or recurring job.

Version 1.1.0 repairs rejection of JPEG files that Pillow identifies as MPO, adds conversion-aware verification and filename recognition for HEIC files with missing MIME types, and targets the current Tames PDF plugin when pausing annotation stores. Its public `pasteImages`/`uploadImages` return values remain Markdown strings. `cleanupImages()` handles all local images; an optional array of paths limits a one-time repair to selected files.

Server source: `/srv/docker/tamestorage/routes/obsidian_images.py`. HEIC support uses `pillow-heif==1.8.1` alongside the existing Pillow installation. Storage remains the external NAS attachment directory, with atomic writes, content-addressed names and the existing 20 MB upload limit. The server reports both source and stored-image SHA-256 values when conversion changes bytes.

Focused client tests use Node's built-in VM, crypto and File APIs; no test dependency is installed:

```sh
env ELECTRON_RUN_AS_NODE=1 '/Applications/Visual Studio Code.app/Contents/MacOS/Code' tests/upload.test.cjs
```

Server regression tests are maintained in `tests/test_obsidian_images.py`. They use isolated temporary storage and a fixture key, not production notes or credentials. The optional original MPO regression fixture is private and is not retained after verification.

## Searchable image metadata (1.2.2)

Pasted images reuse the server's cached English OCR. Recognized text is stored in
ordinary Markdown image **alt text**, not a separate comment or visible overlay.
Live Preview and Reading view show the image; source mode/editing the image link
shows its metadata. Vault search and editor Find can search this text offline.
Existing manual alt text, width aliases and image titles are preserved. Existing
1.2.0 OCR comments migrate into the image metadata without recognizing images again.
Annotation note fields are edited through parsed JSON, preserving other fields.

New pastes and note-open/save events queue a single debounced check. Completed
images need no network request. Busy OCR gets at most three finite retries;
offline work retries on another open/save or **Server Image Paste: Add searchable
OCR to pasted images**. The command also backfills older notes. No idle polling,
local OCR/model/index or extra image copy. Concurrent edits are retained with
`vault.process`; unload cancels queued work and prevents late writes. Recovery
`.previous.md` files and fenced code examples are excluded. OCR can misread
handwriting, code and equations; it is labeled machine text.

Private `/semantic-search/image-text` reuses the existing image_ocr cache and
shared OCR lock. The server excludes generated alt metadata from prose embeddings
because image passages are indexed separately. Maintained tests: `tests/ocr.test.cjs`.

Version 1.2.2 encodes OCR square brackets as character entities because Live Preview
can fail to recognize image embeds whose generated alt text contains backslash-escaped
brackets (for example Verilog array indexes). Existing generated suffixes repair on
open/save or the manual OCR command, without another recognition run. Manual labels,
widths, titles, source wording and image bytes are preserved. Reading-view Markdown
accepts both forms; this specifically addresses Live Preview embed tokenization.

## Website diary links (1.2.3)

The server assistant can append a timestamped image/text entry to the daily diary through existing LiveSync. Its **Open diary** link uses `obsidian://tames-diary?vault=Notes&date=YYYY-MM-DD`. This handler performs one native LiveSync Sync now operation, waits for reflection and opens that exact daily note. It does not enable live/periodic/file-open sync, run an idle timer, read credentials or create notes locally. Wrong vaults/invalid dates are refused; missing/failed sync is reported. Per-request listeners/deadline are removed at completion or plugin unload. Native APIs and the same handler work on iPad after applying the updated MacOS customization package.
