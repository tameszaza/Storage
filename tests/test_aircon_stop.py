from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.ac_control import AcController, DEFAULT_SETTINGS


class FakeWeeklyScheduleManager:
    def __init__(self, path, timezone_name, target_callback, **kwargs):
        self.path = Path(path)
        self.config = {"enabled": True, "presets": {}, "days": {}}

    def disable(self):
        if not self.config["enabled"]:
            return False
        self.config["enabled"] = False
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        return True

    def get_config(self):
        return dict(self.config)


class AirconStopTests(unittest.TestCase):
    def make_controller(self, directory: Path) -> AcController:
        settings = {**DEFAULT_SETTINGS, "root_url": "http://127.0.0.1:8080"}
        settings_path = directory / "settings.json"
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        with patch("lib.ac_control.WeeklyScheduleManager", FakeWeeklyScheduleManager):
            return AcController(
                str(settings_path),
                str(directory / "statistics.json"),
                str(directory / "history.json"),
                str(directory / "weekly.json"),
                str(directory / "runtime.json"),
                "Asia/Singapore",
            )

    def test_manual_stop_disables_weekly_automation(self):
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.make_controller(Path(temporary))
            controller._status.update(active=True, schedule_source="weekly")

            with patch.object(controller, "_send_trigger") as send_trigger:
                weekly_disabled = controller.stop_cycle()

            self.assertTrue(weekly_disabled)
            self.assertFalse(controller.get_weekly_config()["enabled"])
            self.assertFalse(controller.get_status()["active"])
            send_trigger.assert_called_once_with("off", "schedule stop")

    def test_automatic_weekly_stop_keeps_automation_enabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            controller = self.make_controller(Path(temporary))
            controller._status.update(active=True, schedule_source="weekly")

            with patch.object(controller, "_send_trigger"):
                weekly_disabled = controller.stop_cycle(source="weekly")

            self.assertFalse(weekly_disabled)
            self.assertTrue(controller.get_weekly_config()["enabled"])


if __name__ == "__main__":
    unittest.main()
