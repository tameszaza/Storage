# Tames PDF

Private local Obsidian plugin. ID: `tames-pdf`. Upstream PDF++/PDF Annotator updates do not target this folder.

- Uses Obsidian's native PDF viewer for reading, search, page navigation, and cloud URL pointers.
- Embeds with `#page=…&rect=x1,y1,x2,y2` render as static cropped images in Reading view and Live Preview editing, following PDF++'s PDF-coordinate crop convention. Crops use the core PDF engine, at most two concurrent renders and a bounded bitmap; each document/worker is destroyed after rendering. No PDF copies, disk image cache, polling or optional annotation parser needed. Regular PDF embeds keep the native reader. Reopening renders the current server PDF; a matching vault file modification refreshes its crop.
- Preserves the existing annotation engine, Markdown sidecars, region/image notes, completion, math/editor integrations, resizing, and manual empty-file cleanup.
- Settings → Tames PDF → Adapt PDF to theme. Command: Tames PDF: Toggle PDF color inversion.
- Cloud PDFs: a small `.pdf` file containing only an HTTP(S) URL loads the PDF in memory when opened. The server/Tailscale must be reachable. No document disk cache, copy into the vault, periodic sync, cloud prefetch, or new idle polling job is added.
- Native PDF color adaptation uses precisely PDF++'s `pdfjs-is-themed` setting and the core viewer's `onCSSChange()`, preserving embedded image colors. No blanket CSS canvas inversion.
- The optional standalone PDF engine loads only for the custom viewer/fallback and is released after its last viewer closes. Keep `pdf-runtime.js` and `pdf-worker.js` with `main.js`.
- Annotation storage paths and view type keep their legacy identifiers for compatibility. Lecture Chat and Server Image Paste now look up `tames-pdf`.
- Do not enable PDF++ or the old PDF Annotator at the same time. Keep them disabled if their folders are present.

Implementation: `main.js` contains the existing customized PDF Annotator engine; `native-reader.js` is the small cloud/inversion bridge. Native cloud support wraps private core PDF methods, with restoration on unload. Obsidian core upgrades can require compatibility maintenance. Original real-PDF recovery-bundle behavior is preserved; cloud PDFs bypass it.

Credits: annotation engine derived from Alexander Tian's PDF Annotator and existing local modifications; optional PDF.js 3.11.174 retains its existing bundled notices. Cloud bridge and local integration are private adaptations for this vault.

## Note pin defaults
New text notes created with Annotate start pinned in the native overlay and standalone viewer. First conversion from a highlight to a text note pins its card; manual unpin is retained on later edits. Other creation tools keep their defaults. This applies to new notes, with no bulk migration or idle work.


## Transparent text boxes
- In annotation mode, click the **Text box** (`type`) toolbar icon, then drag a rectangle on the page. Single clicks create nothing.
- Click the text box to edit its text, font size (6–144 PDF points, scaled with page zoom) and font color. Existing Tag/Region notes can be converted with **Text box** in the edit panel without changing their text.
- Drag the text or border to move; drag the bottom-right handle to resize. Background is transparent, with a dashed outline/handle only during interaction. Text wraps and clips inside the box. Native text boxes have no duplicate margin card.
- The standalone viewer uses its existing card editor; right-click → Show as text box, then Change color for font size/color.
- Reuses tag storage and geometry: `type: tag`, `tagStyle: textbox`, additive `textFontSize` and `textColor` fields in the existing Markdown JSON. Source PDFs are not modified. No extra parser, dependencies, timers, observers or idle jobs. Other devices need this Tames PDF code to render/edit the new appearance.
- Shared movement/resizing commits immediately on pointerup or a released-button move. Unexpected capture loss while the button remains pressed keeps document tracking active. Escape/pointercancel/blur/teardown cancel. Repeated pointer coordinates skip geometry/layout calculations.


## Collapsible annotation column
The panel-right toolbar button hides or shows the native annotation column, including while drawing Text boxes. Dragging the divider to within 40px of the right edge also closes it; use the toolbar button to reopen. PDF refits once on the toggle/released divider, and page overlays remain active. Closed/open preference is a device-local boolean; expanded width is retained separately. Hidden rails skip layout scheduling and scroll-settle work. Width transitions are disabled in annotation layout so page fitting uses the final width immediately.

## AI annotation feature removed (1.1.0)
The built-in AI button, provider/API clients, credentials manager, model chooser, prompt dialogs, job/checkpoint service, settings and dedicated CSS were removed. Lecture Chat is a separate plugin and remains available. Existing annotation data, including legacy generated sets, remains readable; those compatibility fields do not run AI.

## Copy a PDF region (1.2.0)
Click **Copy PDF region embed** (the scan/rectangle icon), drag a rectangle on the page, then paste into a note. Annotate need not be enabled. The temporary neutral outline disappears after copying; Escape, leaving the tab, switching annotation mode, or unloading cancels selection. A single click creates nothing.

The copied `![[PDF.pdf#page=N&rect=x1,y1,x2,y2|PDF, p.N]]` follows PDF++'s PDF-coordinate link convention and respects the vault's link format. It uses the existing static crop renderer in Reading view and Live Preview, retaining an ordinary Obsidian link/backlink to the source PDF. No colored persistent overlay, annotation sidecar, copied image/PDF, new parser, polling or idle computation is created. Gesture listeners exist only while choosing a region, and selection borrows the already loaded native PDF. The standalone viewer shares the same gesture and copy helper.

## iPad support (1.2.1)

Mobile uses Obsidian's existing PDF viewer and the same annotation store. Touch and Pencil drawing/move/resize commit on pointer release; no mouse-button assumption during touch moves. Notes start collapsed on a new mobile device, respecting any saved choice. No annotation data migration.

The small native-reader helper is bundled in main.js for ordinary LiveSync customization transfer. PDF.js stays lazy and releases its runtime after use. If standard customization sync omits pdf-runtime.js/pdf-worker.js, the first feature needing that fallback engine downloads the exact pinned assets from the private server, verifies SHA-256, and stores them inside this plugin folder. Normal native overlays do not fetch/load this engine. Connect Tailscale for the first fallback-engine use; afterward the files are reused offline. Keep helper source and the bundled tamesBundledHelper function in main.js matched when editing.

## Window dragging and annotation tooltips

The CodeMirror/LanguageTool tooltip portal is fixed at the viewport origin with
zero width/height and visible overflow. Each visible tooltip keeps its own fixed
position, stacking order and pointer events. An empty portal must never span the
whole viewport: Obsidian marks body-level popup containers as Electron no-drag,
which can disable window dragging even with pointer-events:none.

Version1.2.2 fixes this with one portal CSS rule, retaining native Obsidian window
movement. No polling, window handler or extra dependency. Verified live: the same
blank tab-bar point moved the window0px with the old full-viewport portal and40px
after reducing it to0x0. The thin native top strip remained draggable. Test data
is temporary; primary notes and annotations are not used as fixtures.
