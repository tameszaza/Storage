# Tamestorage

A modular Flask private cloud storage dashboard with file upload, folder management, previews, storage analytics, admin tools, feedback, and an optional Gemini AI assistant.

## What changed

- `app.py` is now small and only creates the Flask app.
- Reusable logic moved into `lib/`.
- Route handlers moved into `routes/`.
- UI JavaScript moved into `static/js/`.
- The file manager, admin panel, login, feedback, charts, and chat pages now share a cleaner modern design.
- File access is safer because uploaded paths are normalized and checked inside the upload directory.
- Gemini is lazy-loaded, so the app can start even if `uploads/Admin/config.txt` is missing.

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
pip install -r requirements.txt
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

Put your Gemini API key inside that file. The app will still run without it, but the chat endpoint will return a helpful setup message.

## Environment variables

Use a `.env` file instead of exporting variables in the terminal. A starter `.env` is included locally, and `.env.example` shows the expected keys.

```dotenv
SECRET_KEY=replace-with-a-strong-secret
UPLOAD_FOLDER=uploads
GEMINI_MODEL=gemini-1.5-flash
GEMINI_CONFIG_PATH=uploads/Admin/config.txt
```
