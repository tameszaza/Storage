import time
import psutil


def format_uptime(seconds: int) -> str:
    days, seconds = divmod(max(0, seconds), 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def system_usage() -> dict:
    cpu_usage = psutil.cpu_percent(interval=0.2)
    memory_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage("/")
    return {
        "cpu_usage": cpu_usage,
        "memory_usage": memory_info.percent,
        "memory_used": (memory_info.total - memory_info.available) // (1024 ** 2),
        "memory_total": memory_info.total // (1024 ** 2),
        "disk_usage": disk_info.percent,
        "disk_used": disk_info.used // (1024 ** 3),
        "disk_total": disk_info.total // (1024 ** 3),
        "uptime": format_uptime(int(time.time() - psutil.boot_time())),
    }
