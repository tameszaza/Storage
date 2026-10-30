
import json
from pathlib import Path
from urllib.parse import quote
from flask import Flask
from lib.config import Config
from lib.shares import create_share, load_shares, share_status
from lib.users import load_users

app = Flask("obsidian-goodnotes-pointers")
app.config.from_object(Config)
with app.app_context():
    assert "Admin" in load_users(), "Expected server administrator account is absent"
    root = Path(app.config["UPLOAD_FOLDER"]) / "Goodnotes"
    assert root.is_dir(), "Goodnotes source is unavailable"
    shares = load_shares()
    def get_or_create(path):
        marker = "Obsidian PDF++ Goodnotes: " + path
        for share in shares.values():
            if (share.get("note") == marker and share.get("path") == path
                    and share.get("permission") == "view"
                    and share.get("access_mode") == "link"
                    and not share.get("password_hash")
                    and not share.get("expires_at")
                    and not share.get("max_downloads")
                    and share_status(share)[0]):
                return share
        share = create_share(owner="Admin", path=path, access_mode="link",
                             permission="view", note=marker)
        shares[share["token"]] = share
        return share
    folder_share = get_or_create("Goodnotes")
    base = "https://server.tailb7f57c.ts.net/s/"
    entries = []
    short_links = 0
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".pdf":
            continue
        relative = path.relative_to(root).as_posix()
        url = base + folder_share["token"] + "/raw/" + quote(relative, safe="/")
        if len(url.encode("utf-8")) > 300:
            share = get_or_create("Goodnotes/" + relative)
            url = base + share["token"] + "/raw"
            short_links += 1
        assert len(url.encode("utf-8")) <= 300
        entries.append({"path": relative, "url": url})
        total += path.stat().st_size
    print(json.dumps({"entries": entries, "source_bytes": total,
                      "short_links": short_links}))
