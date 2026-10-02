"""VM metrics (psutil) and instance info (Azure Instance Metadata Service)."""
import json
import os
import platform
import socket
import time
import urllib.request
from datetime import datetime, timezone

import psutil

STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")
IMDS_URL = "http://169.254.169.254/metadata/instance?api-version=2021-02-01"

_imds_cache: dict | None = None
_imds_checked_at = 0.0

# Prime cpu_percent so the first real call returns a meaningful value.
psutil.cpu_percent(interval=None)


def metrics() -> dict:
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(os.environ.get("DISK_PATH", "/") if os.name != "nt" else "C:\\")
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (AttributeError, OSError):  # Windows dev machines have no load average
        load = None
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "memory": {"total": vm.total, "used": vm.total - vm.available, "percent": vm.percent},
        "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        "load_avg": load,
        "uptime_seconds": int(time.time() - psutil.boot_time()),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _fetch_imds() -> dict | None:
    """Ask the Azure metadata service who we are. Only reachable from inside an Azure VM."""
    req = urllib.request.Request(IMDS_URL, headers={"Metadata": "true"})
    # IMDS must never go through a proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=1.0) as resp:
            data = json.load(resp)
    except Exception:
        return None
    compute = data.get("compute", {})
    public_ip = None
    try:
        iface = data["network"]["interface"][0]["ipv4"]["ipAddress"][0]
        public_ip = iface.get("publicIpAddress") or None
    except (KeyError, IndexError, TypeError):
        pass
    # Only non-sensitive fields are kept: no subscription ID, resource IDs or tags.
    return {
        "region": compute.get("location"),
        "vm_size": compute.get("vmSize"),
        "vm_name": compute.get("name"),
        "public_ip": public_ip,
    }


def _imds() -> dict | None:
    global _imds_cache, _imds_checked_at
    # Retry at most every 5 minutes when IMDS was unreachable (e.g. local dev).
    if _imds_cache is None and time.time() - _imds_checked_at > 300:
        _imds_checked_at = time.time()
        _imds_cache = _fetch_imds()
    return _imds_cache


def info() -> dict:
    imds = _imds() or {}
    # The app container sits on an internal-only Docker network (no egress), so IMDS is
    # unreachable from inside it. The deploy script passes these non-secret values in as
    # environment variables instead. metadata_source says honestly where they came from.
    azure = {}
    for key in ("region", "vm_size", "vm_name", "public_ip"):
        azure[key] = imds.get(key) or os.environ.get(f"AZURE_{key.upper()}") or None
    if imds:
        source = "imds"
    elif any(azure.values()):
        source = "deploy"
    else:
        source = None
    azure = {k: v for k, v in azure.items() if v}
    return {
        "app": "CloudTasks",
        "version": os.environ.get("APP_VERSION", "dev"),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "hostname": socket.gethostname(),
        "container": os.environ.get("CONTAINER_NAME") or None,
        "on_azure": bool(azure),
        "region": azure.get("region"),
        "vm_size": azure.get("vm_size"),
        "vm_name": azure.get("vm_name"),
        "public_ip": azure.get("public_ip"),
        "metadata_source": source,
        "deployed_at": os.environ.get("DEPLOY_TIME") or STARTED_AT,
        "started_at": STARTED_AT,
    }
