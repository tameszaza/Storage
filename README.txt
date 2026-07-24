Tamestorage AC Control Update v2

This update removes the cycling speedometer, adds an admin-only MacroDroid aircon controller, and fixes the AC panel layout on wide screens.

Apply it with:

  python apply_update.py /path/to/your/Tamestorage/project

The installer copies the updated files, removes the four speedometer files, and clears stale Python cache folders. It does not change your .env, users, uploads, logs, or other runtime data.

After applying:
1. Restart Flask.
2. Hard-refresh the browser with Ctrl+Shift+R.
3. Sign in as Admin.
4. Open /admin and configure Hall aircon control.
