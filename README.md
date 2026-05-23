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
static/
  css/styles.css
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
