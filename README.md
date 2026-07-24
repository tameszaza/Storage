# Tamestorage

A modular Flask private cloud storage dashboard with file upload, folder management, previews, storage analytics, admin tools, feedback, and an optional Gemini AI assistant.

## What changed

- `app.py` is now small and only creates the Flask app.
- Reusable logic moved into `lib/`.
- Route handlers moved into `routes/`.
- UI JavaScript moved into `static/js/`.
- The file manager, admin panel, login, feedback, charts, and chat pages now share a cleaner modern design.
- File access is safer because uploaded paths are normalized and checked inside the upload directory.
- Gemini is lazy-loaded and now uses the newer `google-genai` SDK, so the app can start even if `uploads/Admin/config.txt` is missing.

## Project structure

```text
app.py
lib/
  ai_client.py
  charts.py
  config.py
  extensions.py
  feedback_store.py
  json_store.py
  request_logging.py
  security.py
  storage.py
  system_info.py
  users.py
routes/
  admin.py
  ai.py
  auth.py
  feedback.py
  files.py
  public.py
src/
  tailwind.css
static/
  css/styles.css
  css/tailwind.css
  vendor/
  js/app.js
  js/storage.js
  js/admin.js
  js/chat.js
templates/
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U -r requirements.txt
python app.py
```

Open:

```text
http://localhost:5000
```

## Tailwind frontend

The browser-facing design system is maintained in `src/tailwind.css` and compiled locally into `static/css/tailwind.css`. The compiled stylesheet is committed, so the Flask app can run immediately after the Python setup.

Install the frontend development dependencies and rebuild the stylesheet after changing the Tailwind component layer:

```bash
npm install
npm run build:css
```

During styling work, use:

```bash
npm run watch:css
```

Bootstrap behavior, Font Awesome icons, and PDF.js thumbnail rendering are bundled under `static/vendor/`. The interface no longer depends on remote CSS or JavaScript CDNs, which keeps the private dashboard reliable on local networks.

## Frontend verification

```bash
npm run verify
python -m compileall -q app.py lib routes
```

## Gemini AI assistant

Create this file if you want the AI chat page to work:

```text
uploads/Admin/config.txt
```

Put your Gemini API key inside that file. The app will still run without it, but the chat endpoint will return a helpful setup message. The project uses the newer `google-genai` package instead of the deprecated `google-generativeai` package.

## Environment variables

```bash
export SECRET_KEY="replace-with-a-strong-secret"
export UPLOAD_FOLDER="uploads"
export GEMINI_MODEL="gemini-1.5-flash"
export GEMINI_CONFIG_PATH="uploads/Admin/config.txt"
```

## Latest UI and upload fixes

- Dark mode now uses a cleaner card/table/input color system.
- The file manager toolbar is aligned better on desktop and mobile.
- Folder upload is supported through the Choose folder button. Nested folders are preserved.
- Drag and drop folder upload is supported in browsers with directory drag APIs, such as Chromium-based browsers.
- Uploaded nested paths are sanitized on the backend before saving.

## Secure file sharing system

Tamestorage now includes a complete sharing center for files and folders.

### Features

- Share any file or folder from the file manager with the share icon.
- Create public link shares for anyone with the link.
- Create restricted shares for specific Tamestorage usernames.
- Permission presets:
  - View only
  - View and download
  - Folder drop box
  - Editor
  - Full control
- Optional password protection per share.
- Optional expiry date and time.
- Optional maximum download count.
- Folder shares support browsing nested folders.
- Folder shares can allow external upload when permission allows it.
- Text files can be edited through a shared link when permission allows it.
- Owners can copy, open, revoke, and delete share records from the Sharing Center.
- Admin can see and manage all share records.
- Share events are saved in `share_audit.json` for auditing.

### New files created at runtime

```text
shares.json
share_audit.json
```

You can change their locations with:

```bash
export SHARE_DATA_FILE=/path/to/shares.json
export SHARE_AUDIT_FILE=/path/to/share_audit.json
```

## Storage feature pack

This build includes a larger personal-cloud feature set:

- Trash bin with restore, delete forever, and empty trash.
- Browser previews for images, video, audio, PDF, CSV, JSON, code, markdown, and text.
- Version history for files edited in the browser or replaced by upload.
- Tags, notes, and starred files.
- Advanced search by name, note, tag, file kind, content, size, and modified date.
- Exact duplicate detection using SHA-256.
- Gallery mode for image folders.
- Move and copy actions.
- File requests for public upload-only links.
- Notifications for file request uploads.
- Activity audit log.
- Admin backup export.
- Admin integrity scan.
- Per-user storage quota setting.
- PWA manifest and service worker shell cache.

Runtime metadata files created by these features:

```text
trash_index.json
versions.json
file_metadata.json
activity_log.json
notifications.json
file_requests.json
```

The internal recovery/version storage is kept under:

```text
uploads/.tamestorage_system/
```

## Private MacroDroid aircon portal

The hall aircon controller is available at `/aircon` in a separate password-protected portal. It is intentionally separated from the Admin center so the controller can be bookmarked and used without exposing storage or server administration tools.

Configure a separate portal password before use:

```text
AIRCON_PORTAL_PASSWORD=replace-with-a-separate-aircon-password
AIRCON_SESSION_HOURS=24
```

The portal supports:

- One local MacroDroid root URL with automatic `/ac_on` and `/ac_off` paths.
- ON and OFF durations in seconds, minutes, or hours.
- Duration, repeat count, root URL, timeout, and stop behavior changes while the schedule is active.
- Immediate shortening or extension of the current phase after applying changes.
- Start, stop, and skip-current-phase actions.
- Direct ON and OFF commands while no automatic schedule is running.
- Live timeline progress and session cost estimates at SGD 0.39 per ON hour.
- Spend and savings values rounded up to the nearest SGD 0.01.

Settings remain stored in `ac_control.json` with owner-only file permissions. The schedule does not automatically resume after a server restart.

### MacroDroid setup

Create two MacroDroid HTTP Server Request macros on the Android phone:

1. Use identifier `ac_on` for the macro that opens the hall aircon application and ensures the aircon is ON.
2. Use identifier `ac_off` for the macro that ensures the aircon is OFF.
3. Set the MacroDroid local HTTP server port, for example `8080`.
4. Enter this root URL in the aircon portal:

```text
http://127.0.0.1:8080
```

Tamestorage calls:

```text
http://127.0.0.1:8080/ac_on
http://127.0.0.1:8080/ac_off
```

Using `127.0.0.1` keeps the trigger request inside the phone and avoids a public webhook.

### Runtime settings file

The default path is `ac_control.json`. You can change it with:

```text
AC_CONTROL_FILE=/path/to/ac_control.json
```

### Restarting from the Admin center

Use **Server control → Restart server** after replacing backend or interface files. If an automatic schedule is active, Tamestorage stops it and sends the configured OFF trigger before restarting.

## Reference-inspired responsive interface

The current interface was rebuilt around a consistent personal-cloud design system:

- A fixed desktop sidebar and compact global-search top bar.
- A mobile navigation drawer that works from 320 px wide screens upward.
- A redesigned landing page, login page, registration page, file manager, preview, sharing center, trash, recent files, and account settings.
- A New dialog that keeps the original drag-and-drop, multi-file, and folder upload behavior.
- Light, dark, and system theme preferences stored in the browser.
- A Recent page ordered by modification time.
- Account storage summaries and a password-change form.
- A complete in-page PDF viewer with page navigation and zoom controls.
- Keyboard shortcuts: press `/` to focus global search and `Escape` to close the mobile drawer or upload dialog.

The redesign keeps the existing storage, sharing, administration, file-request, search, metadata, versioning, and AI features intact, with the MacroDroid aircon controller available in its own private portal.

## File-manager quality-of-life features

The file manager also includes:

- Real upload progress with transferred bytes, speed, estimated time, cancellation, and multi-file queue details.
- Creation of plain-text, Markdown, code, JSON, YAML, CSS, HTML, and log files directly from the New menu.
- An in-browser editor with line numbers, live line/word/character counts, line wrapping, `Ctrl+S` or `Cmd+S`, and local draft recovery.
- An accessible MP3/audio player with seeking, skip controls, volume, mute, playback speed, keyboard-friendly controls, and Media Session integration.
- Search results that always show the item name, location, type, size, modified date, tags, notes, and direct Preview/Details actions.
- A responsive list view with visible filenames, type, size, modified time, selection controls, and action menus.
- Expanded technical metadata including MIME type, extension, owner, full path, byte size, timestamps, permissions, SHA-256 checksum, image dimensions, text statistics, and folder counts when applicable.
- Accessible labels, focus indicators, skip navigation, button hints, live status announcements, reduced-motion support, and improved mobile layouts.
