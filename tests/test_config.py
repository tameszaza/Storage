import unittest

from lib.config import Config


class ConfigTests(unittest.TestCase):
    def test_disk_dashboard_has_backup_path(self):
        self.assertEqual(Config.DISK_CHECK_BACKUP_PATH, "/srv/tamestorage-backup")


if __name__ == "__main__":
    unittest.main()
