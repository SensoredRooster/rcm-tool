"""Controller acquisition adapter over the original RCM Tool backends."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable


SONY_VENDOR_ID = 0x054C
MICROSOFT_VENDOR_ID = 0x045E
DUALSENSE_PRODUCT_IDS = frozenset({0x0CE6, 0x0DF2})
LOGGER = logging.getLogger(__name__)


def _coerce_usb_id(value) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    except (TypeError, ValueError):
        return None


def detect_controller_family(metadata: dict | None = None, source: str = "") -> str:
    """Return xbox, dualsense, or generic from evidence supplied by the backend.

    This is deliberately conservative. Visual identity may be overridden in the
    UI, but named button mappings should rely on this detected family.
    """
    meta = metadata or {}
    backend = str(meta.get("backend") or "")
    connection = str(meta.get("connection_method") or "")
    name = str(meta.get("controller_name") or "")
    manufacturer = str(meta.get("manufacturer") or "")
    evidence = " ".join((source, backend, connection, name, manufacturer)).lower()
    vid = _coerce_usb_id(meta.get("vid"))
    pid = _coerce_usb_id(meta.get("pid"))

    if "xinput" in evidence or "xbox" in evidence:
        return "xbox"

    if "dualsense" in evidence or "dual sense" in evidence:
        return "dualsense"
    if vid == SONY_VENDOR_ID and pid in DUALSENSE_PRODUCT_IDS:
        return "dualsense"

    # Microsoft VID alone is not enough; require controller/gamepad evidence.
    if vid == MICROSOFT_VENDOR_ID and any(token in evidence for token in ("controller", "gamepad", "xbox")):
        return "xbox"

    return "generic"


@dataclass(frozen=True)
class ControllerMeasurement:
    timestamp_ns: int
    sample: dict
    source: str
    timing_quality: str
    raw_report_hex: str | None = None
    duplicate_raw_report: bool = False
    metadata: dict | None = None


class ControllerAcquisition:
    def __init__(
        self,
        callback: Callable[[ControllerMeasurement], None],
        event_callback: Callable[[str, dict], None] | None = None,
        poll_sleep_s: float = 0.001,
    ) -> None:
        self.callback = callback
        self.event_callback = event_callback
        self.poll_sleep_s = max(0.00025, float(poll_sleep_s))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.backend = None
        self.error: str | None = None

    def _event(self, name: str, payload: dict | None = None) -> None:
        if self.event_callback:
            self.event_callback(name, payload or {})

    @staticmethod
    def _backend_metadata(active) -> dict:
        if active is None:
            return {}
        meta = {
            "backend": getattr(active, "name", active.__class__.__name__),
            "connection_method": active.__class__.__name__,
        }
        info = getattr(active, "info", None)
        if isinstance(info, dict):
            for src, dst in (
                ("product_string", "controller_name"),
                ("manufacturer_string", "manufacturer"),
                ("vendor_id", "vid"),
                ("product_id", "pid"),
                ("serial_number", "serial_number"),
                ("release_number", "firmware_release"),
                ("interface_number", "hid_interface"),
                ("usage_page", "usage_page"),
                ("usage", "usage"),
            ):
                value = info.get(src)
                if value not in (None, ""):
                    meta[dst] = value
            path = info.get("path") or getattr(active, "path", None)
            if isinstance(path, bytes):
                path = path.decode(errors="replace")
            if path:
                meta["usb_path"] = str(path)
        if hasattr(active, "connected_user_index") and getattr(active, "connected_user_index") is not None:
            meta["xinput_slot"] = int(getattr(active, "connected_user_index"))
        if hasattr(active, "device_id") and getattr(active, "device_id") is not None:
            meta["device_id"] = int(getattr(active, "device_id"))
        joystick = getattr(active, "joystick", None)
        if joystick is not None:
            for method, key in (("get_name", "controller_name"), ("get_guid", "guid"), ("get_power_level", "battery_status")):
                fn = getattr(joystick, method, None)
                if callable(fn):
                    try:
                        value = fn()
                        if value not in (None, ""):
                            meta[key] = str(value)
                    except Exception:
                        LOGGER.warning("Controller metadata probe failed: %s", method, exc_info=True)
        return meta

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="controller-acquisition")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def _run(self) -> None:
        try:
            from controller_integrity import AutomaticControllerBackend
            self.backend = AutomaticControllerBackend()
        except Exception as exc:
            self.error = f"Controller backend initialization failed: {exc}"
            self._event("controller_backend_error", {"message": self.error})
            return

        last_host_sample_ns = 0
        last_seen = 0.0
        connected = False
        last_buttons: int | None = None
        last_raw_hex: str | None = None
        last_error: str | None = None

        while not self.stop_event.is_set():
            try:
                sample = self.backend.read()
                now = time.perf_counter_ns()
                active = getattr(self.backend, "active", None)
                source = (
                    getattr(active, "status", lambda: getattr(active, "name", "Controller"))()
                    if active is not None else "Controller"
                )

                if sample is not None:
                    last_seen = time.monotonic()
                    if not connected:
                        connected = True
                        self._event("controller_connected", {"source": source, "metadata": self._backend_metadata(active)})
                    if "buttons" in sample:
                        buttons = int(sample.get("buttons", 0))
                        if last_buttons is not None and buttons != last_buttons:
                            changed = buttons ^ last_buttons
                            self._event("button_transition", {
                                "previous_mask": last_buttons,
                                "current_mask": buttons,
                                "changed_mask": changed,
                            })
                        last_buttons = buttons

                if active is not None and active.__class__.__name__ == "HIDGamepad":
                    drain = getattr(active, "drain_raw_reports", None)
                    reports = drain() if callable(drain) else []
                    for report in reports:
                        raw_hex = bytes(report).hex()
                        duplicate = last_raw_hex == raw_hex
                        last_raw_hex = raw_hex
                        self.callback(ControllerMeasurement(
                            timestamp_ns=now,
                            sample=dict(sample or {}),
                            source=source,
                            timing_quality="measured-at-host-read",
                            raw_report_hex=raw_hex,
                            duplicate_raw_report=duplicate,
                            metadata=self._backend_metadata(active),
                        ))
                elif sample is not None and now - last_host_sample_ns >= int(self.poll_sleep_s * 1e9):
                    last_host_sample_ns = now
                    self.callback(ControllerMeasurement(
                        timestamp_ns=now,
                        sample=dict(sample),
                        source=getattr(active, "name", "Host controller API") if active is not None else "Host controller API",
                        timing_quality="host-poll-estimate",
                        metadata=self._backend_metadata(active),
                    ))

                if connected and sample is None and time.monotonic() - last_seen >= 0.5:
                    connected = False
                    last_buttons = None
                    self._event("controller_disconnected", {})

                last_error = None
            except Exception as exc:
                self.error = str(exc)
                if self.error != last_error:
                    self._event("controller_backend_error", {"message": self.error})
                    last_error = self.error
            time.sleep(self.poll_sleep_s)
