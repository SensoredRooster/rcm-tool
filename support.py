"""RCM Tool local-first telemetry and support diagnostics.

No support bundle is uploaded automatically. Users explicitly choose Send Diagnostics.
"""

from __future__ import annotations

import atexit
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "RCMTool"
REPOSITORY_URL = "https://github.com/SensoredRooster/rcm-tool"
ISSUES_URL = REPOSITORY_URL + "/issues/new"
SESSION_ID = uuid.uuid4().hex[:12]
STARTED_AT = datetime.now(timezone.utc).isoformat()
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_BACKUPS = 6
_LOCK = threading.Lock()
_HEARTBEAT_THREAD: threading.Thread | None = None


def support_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    path = base / APP_NAME / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return support_root() / "rcm-tool.jsonl"


def error_log_path() -> Path:
    return support_root() / "errors.jsonl"


def _rotate(path: Path) -> None:
    if not path.exists() or path.stat().st_size < MAX_LOG_BYTES:
        return
    for index in range(MAX_BACKUPS - 1, 0, -1):
        src = path.with_name(path.name + f".{index}")
        dst = path.with_name(path.name + f".{index + 1}")
        if src.exists():
            if index + 1 >= MAX_BACKUPS and dst.exists():
                dst.unlink(missing_ok=True)
            src.replace(dst)
    path.replace(path.with_name(path.name + ".1"))


_SENSITIVE_KEY = re.compile(r"(?i)(token|secret|password|passwd|authorization|cookie|credential|api[_-]?key)")
_BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:code|token|access_token|refresh_token|client_secret|state|password)=)[^&#\s]+")
_LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{56,}(?![A-Za-z0-9])")


def redact_text(value: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _QUERY_SECRET.sub(r"\1[REDACTED]", value)
    value = _LONG_TOKEN.sub("[REDACTED]", value)
    return value


def _redact(value):
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def log_event(event: str, *, level: str = "INFO", **fields) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": SESSION_ID,
        "level": level,
        "event": event,
        **_redact(fields),
    }
    target = error_log_path() if level in {"ERROR", "CRITICAL"} else log_path()
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _LOCK:
        _rotate(target)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def health_snapshot() -> dict:
    root = support_root()
    total, used, free = shutil.disk_usage(root)
    return {
        "app": APP_NAME,
        "session_id": SESSION_ID,
        "started_at": STARTED_AT,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "admin": _is_admin(),
        "free_disk_bytes": free,
        "pygame_available": _module_available("pygame"),
        "hid_available": _module_available("hid"),
        "report_directory": str((Path(__file__).resolve().parent / "reports").resolve()),
        "upload_configured": bool(os.environ.get("RCM_SUPPORT_UPLOAD_URL", "").strip()),
        "repository": REPOSITORY_URL,
    }


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def create_support_bundle() -> Path:
    destination = Path(tempfile.gettempdir()) / f"RCMTool-Support-{SESSION_ID}.zip"
    manifest = health_snapshot()
    manifest["created_at"] = datetime.now(timezone.utc).isoformat()
    manifest["repository"] = REPOSITORY_URL
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics/manifest.json", json.dumps(manifest, indent=2))
        archive.writestr(
            "README.txt",
            "RCM Tool support bundle. Created locally after explicit user action. "
            "Review before sharing if desired. The bundle intentionally avoids controller telemetry reports and raw HID captures.\n",
        )
        for path in support_root().glob("*"):
            if not path.is_file():
                continue
            if not (path.name.startswith("rcm-tool.jsonl") or path.name.startswith("errors.jsonl") or path.suffix.lower() == ".log"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            archive.writestr(f"logs/{path.name}", redact_text(text))
    log_event("support_bundle_created", path=str(destination), size_bytes=destination.stat().st_size)
    return destination


def upload_support_bundle(url: str | None = None, token: str | None = None) -> dict:
    endpoint = (url or os.environ.get("RCM_SUPPORT_UPLOAD_URL", "")).strip()
    if not endpoint:
        raise RuntimeError("RCM support upload endpoint is not configured.")
    bundle = create_support_bundle()
    request = urllib.request.Request(endpoint, data=bundle.read_bytes(), method="POST")
    request.add_header("Content-Type", "application/zip")
    request.add_header("X-RCM-Session", SESSION_ID)
    request.add_header("X-RCM-Filename", bundle.name)
    bearer = (token or os.environ.get("RCM_SUPPORT_UPLOAD_TOKEN", "")).strip()
    if bearer:
        request.add_header("Authorization", "Bearer " + bearer)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            log_event("support_bundle_uploaded", status=response.status)
            return {"status": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        log_event("support_upload_failed", level="ERROR", status=exc.code, error=str(exc))
        raise


def open_logs_folder() -> None:
    path = support_root()
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def open_repository() -> None:
    webbrowser.open(REPOSITORY_URL)


def report_issue() -> None:
    title = urllib.parse.quote("RCM Tool support issue")
    body = urllib.parse.quote(f"Session ID: {SESSION_ID}\nStarted: {STARTED_AT}\n\nDescribe the issue here.")
    webbrowser.open(f"{ISSUES_URL}?title={title}&body={body}")


def install_exception_hooks() -> None:
    original_sys = sys.excepthook

    def sys_hook(exc_type, exc_value, exc_tb):
        try:
            log_event(
                "uncaught_exception",
                level="ERROR",
                exception_type=getattr(exc_type, "__name__", str(exc_type)),
                error=str(exc_value),
            )
        finally:
            original_sys(exc_type, exc_value, exc_tb)

    sys.excepthook = sys_hook

    original_thread = getattr(threading, "excepthook", None)
    if original_thread is not None:
        def thread_hook(args):
            try:
                log_event(
                    "thread_uncaught_exception",
                    level="ERROR",
                    thread=getattr(getattr(args, "thread", None), "name", None),
                    exception_type=getattr(getattr(args, "exc_type", None), "__name__", "unknown"),
                    error=str(getattr(args, "exc_value", "")),
                )
            finally:
                original_thread(args)
        threading.excepthook = thread_hook


def start_heartbeat(interval: float = 1.0) -> None:
    global _HEARTBEAT_THREAD
    if _HEARTBEAT_THREAD and _HEARTBEAT_THREAD.is_alive():
        return

    def worker() -> None:
        while True:
            try:
                log_event("heartbeat")
            except Exception:
                pass
            time.sleep(max(1.0, interval))

    _HEARTBEAT_THREAD = threading.Thread(target=worker, name="RCMSupportHeartbeat", daemon=True)
    _HEARTBEAT_THREAD.start()


def _log_shutdown() -> None:
    try:
        log_event("app_stop")
    except Exception:
        pass


install_exception_hooks()
atexit.register(_log_shutdown)
log_event("app_support_initialized", health=health_snapshot())
