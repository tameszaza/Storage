# Workspace assistant

The assistant can read workspace files, PDF page ranges and DOCX content; summarize documents; create/edit/delete local events; create/edit/complete/cancel/restore/delete tasks; and manage administrator music playlists and video downloads.

Music actions reuse the existing playlist worker (including lyrics and remote-removal checks). Video actions reuse the existing download queue. Queued work is reported as queued. Action receipts link back to the relevant page. Subscribed calendar events remain read-only. File permissions and protected configuration exclusions apply to document reading; media tools require Admin.

In My Files, choose **Summarize** from a text, PDF or DOCX file's menu to open the assistant with that file selected. Review and send the prepared prompt. Document reading is bounded and paginated. PDFs need an extractable text layer; scanned pages are reported rather than invented. OCR, audio transcription and video-content analysis are not implemented.

Disable **Workspace tools** in Options to turn off private context and system actions for that message. Tools act only on user requests; file contents and logs are treated as untrusted data. Ambiguous targets or ambiguous playlist deletion should be clarified. Detach keeps music; deleting playlist files removes managed music and lyrics through the worker.

Validation:

```sh
python -m unittest discover -s tests -p test_assistant_tools.py -v
```

An optional real-provider check uses temporary task and document data and the configured Gemini key:

```sh
python tests/assistant_live_smoke.py
```
