"""Keep the browser planner cache current even while no one has it open."""
from __future__ import annotations

import logging
import os
import time

from flask import Flask

from lib.config import Config
from lib.caldav_sync import CalendarSyncError, sync_user_events


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)
INTERVAL = max(30, int(os.environ.get("CALDAV_SYNC_INTERVAL_SECONDS", "120")))
app = Flask("tamestorage-calendar-worker")
app.config.from_object(Config)


def main() -> None:
    while True:
        try:
            with app.app_context():
                result = sync_user_events("Admin")
            if any(result.values()):
                LOGGER.info("CalDAV sync: %s", result)
        except CalendarSyncError as exc:
            LOGGER.warning("CalDAV sync deferred: %s", exc)
        except Exception:
            LOGGER.exception("Unexpected CalDAV sync failure")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
