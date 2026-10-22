"""Keep the browser planner cache current even while no one has it open."""
from __future__ import annotations

import logging
import os
import time

from flask import Flask

from lib.config import Config
from lib.caldav_sync import CalendarSyncError, record_sync_health, sync_user_events


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)
INTERVAL = Config.CALDAV_SYNC_INTERVAL_SECONDS
app = Flask("tamestorage-calendar-worker")
app.config.from_object(Config)


def _publish_health(success: bool) -> None:
    try:
        with app.app_context():
            record_sync_health(success)
    except OSError:
        LOGGER.warning("Could not write CalDAV worker status", exc_info=True)


def main() -> None:
    while True:
        try:
            with app.app_context():
                result = sync_user_events("Admin")
            _publish_health(True)
            if any(result.values()):
                LOGGER.info("CalDAV sync: %s", result)
        except CalendarSyncError as exc:
            _publish_health(False)
            LOGGER.warning("CalDAV sync deferred: %s", exc)
        except Exception:
            _publish_health(False)
            LOGGER.exception("Unexpected CalDAV sync failure")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
