import tempfile
import unittest
from pathlib import Path

from flask import Flask

from lib.json_store import read_json, write_json
from routes.admin import remove_user, reset_user, set_user_quota, suspend_user


class AdminOwnerGuardTests(unittest.TestCase):
    def test_owner_actions_do_not_change_account_or_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uploads = root / "uploads"
            owner = uploads / "Admin"
            owner.mkdir(parents=True)
            marker = owner / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            orphan = uploads / "orphan"
            orphan.mkdir()
            orphan_marker = orphan / "keep.txt"
            orphan_marker.write_text("keep", encoding="utf-8")
            users_file = root / "users.json"
            users = {"Admin": {"password": "hash", "quota_bytes": 0}, "guest": {"password": "hash"}}
            write_json(str(users_file), users)

            app = Flask(__name__)
            app.config.update(SECRET_KEY="test", USER_DATA_FILE=str(users_file), UPLOAD_FOLDER=str(uploads))
            app.add_url_rule("/admin", "admin", lambda: "admin")
            for action, handler in (
                ("reset", reset_user), ("remove", remove_user),
                ("suspend", suspend_user), ("quota", set_user_quota),
            ):
                app.add_url_rule(f"/admin/{action}/<username>", action, handler, methods=["POST"])

            client = app.test_client()
            with client.session_transaction() as session:
                session.update(logged_in=True, username="Admin")
            for action in ("reset", "remove", "suspend", "quota"):
                response = client.post(f"/admin/{action}/Admin", data={"quota_gb": "1"})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, "/admin")
                self.assertEqual(read_json(str(users_file), {}), users)
                self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            response = client.post("/admin/remove/orphan")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(read_json(str(users_file), {}), users)
            self.assertEqual(orphan_marker.read_text(encoding="utf-8"), "keep")

            guest = uploads / "guest"
            guest.mkdir()
            (guest / "test.txt").write_text("remove", encoding="utf-8")
            response = client.post("/admin/remove/guest")
            self.assertEqual(response.status_code, 302)
            self.assertFalse(guest.exists())
            self.assertNotIn("guest", read_json(str(users_file), {}))


if __name__ == "__main__":
    unittest.main()
