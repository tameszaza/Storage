from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")
_SEVERITY = {"healthy": 0, "warning": 1, "critical": 2, "offline": 3}


def _decode_mount_field(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _mount_info(path: Path) -> dict:
    resolved = str(path.resolve())
    best: dict = {}
    best_length = -1
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return best

    for line in lines:
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 6 or len(right_fields) < 2:
            continue
        mount_point = _decode_mount_field(left_fields[4])
        prefix = mount_point.rstrip("/") + "/"
        if resolved != mount_point and not resolved.startswith(prefix):
            continue
        if len(mount_point) <= best_length:
            continue
        best_length = len(mount_point)
        best = {
            "mount_point": mount_point,
            "filesystem": right_fields[0],
            "device": _decode_mount_field(right_fields[1]),
            "mount_options": left_fields[5].split(","),
        }
    return best


def classify_usage(percent: float, free_bytes: int) -> tuple[str, str]:
    if percent >= 95 or free_bytes < 5 * 1024**3:
        return "critical", "Very little free space remains"
    if percent >= 85 or free_bytes < 20 * 1024**3:
        return "warning", "Free space is getting low"
    return "healthy", "Capacity is within the safe range"


def _read_sysfs(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def _leading_int(value: str, default: int = 0) -> int:
    try:
        return int(value.split(maxsplit=1)[0])
    except (AttributeError, IndexError, ValueError):
        return default


def _raid_info(device: str) -> dict | None:
    if not device.startswith("/dev/md"):
        return None
    block_name = Path(device).resolve().name
    md_root = Path("/sys/class/block") / block_name / "md"
    if not md_root.is_dir():
        return None
    # During reshape Linux may report "3 (2)": current and previous geometry.
    expected = _leading_int(_read_sysfs(md_root / "raid_disks", "0"))
    degraded = _leading_int(_read_sysfs(md_root / "degraded", "0"))
    action = _read_sysfs(md_root / "sync_action", "idle")
    progress = None
    completed = _read_sysfs(md_root / "sync_completed")
    if completed and completed != "none" and " / " in completed:
        try:
            done, total = (int(value.strip()) for value in completed.split(" / ", 1))
            progress = round(done / total * 100, 1) if total else None
        except ValueError:
            pass
    members = []
    for member in sorted(md_root.glob("dev-*")):
        members.append(
            {
                "name": member.name.removeprefix("dev-"),
                "state": _read_sysfs(member / "state", "unknown").replace(",", ", "),
            }
        )
    return {
        "level": _read_sysfs(md_root / "level", "unknown").upper(),
        "state": _read_sysfs(md_root / "array_state", "unknown"),
        "expected": expected,
        "active": max(0, expected - degraded),
        "degraded": degraded,
        "action": action,
        "progress": progress,
        "members": members,
    }


def collect_disk_report(targets: list[dict]) -> dict:
    disks: list[dict] = []
    system_device_id: int | None = None

    for target in targets:
        path = Path(str(target["path"]))
        disk = {
            "key": str(target["key"]),
            "label": str(target["label"]),
            "role": str(target.get("role") or "Storage"),
            "display_path": str(target.get("display_path") or path),
            "check_path": str(path),
            "kind": str(target.get("kind") or "storage"),
            "available": False,
            "status": "offline",
            "status_message": "Drive path is unavailable",
            "total": 0,
            "used": 0,
            "free": 0,
            "percent": 0.0,
            "device_id": None,
            "device": "Unavailable",
            "filesystem": "Unknown",
            "mount_point": "Unavailable",
        }
        try:
            if not path.is_dir():
                raise OSError("Drive path does not exist")
            usage = shutil.disk_usage(path)
            device_id = path.stat().st_dev
            mount = _mount_info(path)
            percent = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0
            status, message = classify_usage(percent, usage.free)
            disk.update(
                available=True,
                status=status,
                status_message=message,
                total=usage.total,
                used=usage.used,
                free=usage.free,
                percent=percent,
                device_id=device_id,
                device=mount.get("device") or "Unknown device",
                filesystem=mount.get("filesystem") or "Unknown",
                mount_point=mount.get("mount_point") or str(path),
            )
            if disk["kind"] == "raid":
                raid = _raid_info(str(disk["device"]))
                disk["raid"] = raid
                if not raid:
                    disk.update(status="critical", status_message="RAID metadata is unavailable")
                elif raid["degraded"]:
                    disk.update(
                        status="critical",
                        status_message=(
                            f"RAID is degraded: {raid['active']} of {raid['expected']} members active"
                        ),
                    )
                elif raid["action"] not in {"idle", "frozen"}:
                    progress_text = (
                        f" ({raid['progress']}%)" if raid["progress"] is not None else ""
                    )
                    disk.update(
                        status="warning",
                        status_message=f"RAID {raid['action']} in progress{progress_text}",
                    )
                else:
                    disk["status_message"] = (
                        f"RAID healthy: {raid['active']} of {raid['expected']} members active"
                    )
            if disk["kind"] == "system":
                system_device_id = device_id
        except OSError as exc:
            disk["status_message"] = str(exc) or "Drive path is unavailable"
        disks.append(disk)

    external_devices: dict[int, str] = {}
    for disk in disks:
        if not disk["available"] or disk["kind"] not in {"external", "raid"}:
            continue
        device_id = disk["device_id"]
        if system_device_id is not None and device_id == system_device_id:
            disk.update(
                available=False,
                status="offline",
                status_message="External disk is not mounted; path resolves to the internal disk",
                total=0,
                used=0,
                free=0,
                percent=0.0,
            )
            continue
        if device_id in external_devices:
            disk.update(
                status="critical",
                status_message=f"Shares a filesystem with {external_devices[device_id]}",
            )
        else:
            external_devices[device_id] = disk["label"]

    available = [disk for disk in disks if disk["available"]]
    worst = max(disks, key=lambda disk: _SEVERITY[disk["status"]], default=None)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disks": disks,
        "drive_count": len(disks),
        "available_count": len(available),
        "highest_usage": max((disk["percent"] for disk in available), default=0.0),
        "overall_status": worst["status"] if worst else "offline",
    }
