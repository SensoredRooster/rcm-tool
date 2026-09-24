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
DISCONNECT_TIMEOUT_S = 2.0
RAW_HID_PRESENCE_CHECK_S = 1.0
RAW_HID_IDLE_AFTER_S = 0.75
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


def detect_controller_layout(metadata: dict | None = None, source: str = "") -> str:
    """Choose a visual shell from an explicit model name, without guessing its button map."""
    meta = metadata or {}
    evidence = " ".join(
        str(meta.get(key) or "")
        for key in ("controller_name", "product_string", "manufacturer", "manufacturer_string")
    ) + " " + str(source or "")
    compact = "".join(character for character in evidence.casefold() if character.isalnum())
    if "vader5pro" in compact or ("flydigi" in compact and "vader5" in compact):
        return "vader5pro"
    return detect_controller_family(meta, source)


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
        source_kind: str = "automatic",
        hid_path=None,
        hid_info: dict | None = None,
    ) -> None:
        self.callback = callback
        self.event_callback = event_callback
        self.poll_sleep_s = max(0.00025, float(poll_sleep_s))
        self.source_kind = source_kind if source_kind in {"automatic", "raw_hid"} else "automatic"
        self.hid_path = hid_path
        self.hid_info = dict(hid_info or {})
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.backend = None
        self.error: str | None = None

    @staticmethod
    def enumerate_raw_hid_devices() -> list[dict]:
        """Return controller-like Raw HID devices for the modern UI selector."""
        try:
            from controller_integrity import HIDGamepad, hid
            devices = list(HIDGamepad.enumerate_devices())
            known_paths = {item.get("path") for item in devices}
            # Some Flydigi descriptors use generic usage/vendor metadata, so
            # retain these explicitly named gamepads even if the legacy filter
            # does not yet recognize their branding.
            if hid is not None:
                try:
                    all_devices = hid.enumerate()
                except Exception:
                    LOGGER.debug("Supplemental Raw HID enumeration failed", exc_info=True)
                    all_devices = []
                for info in all_devices:
                    product = " ".join(
                        str(info.get(key) or "")
                        for key in ("product_string", "manufacturer_string")
                    ).casefold()
                    path = info.get("path")
                    if path and path not in known_paths and ("flydigi" in product or "vader" in product):
                        devices.append(info)
                        known_paths.add(path)
            return devices
        except Exception:
            LOGGER.warning("Raw HID enumeration failed", exc_info=True)
            return []

    @staticmethod
    def raw_hid_backend_status() -> tuple[bool, str]:
        """Return an actionable status for the optional Raw HID dependency."""
        try:
            from controller_integrity import hid
        except Exception as exc:
            return False, f"Raw HID backend import failed: {exc}"
        if hid is None:
            return False, "Raw HID backend is not installed; run python -m pip install -r requirements.txt"
        return True, "Raw HID backend available"

    @staticmethod
    def raw_hid_path_present(path) -> bool | None:
        """Check device presence independently of whether it is sending input reports.

        None means enumeration was unavailable or failed; callers must not turn an
        inconclusive check into a disconnect.
        """
        try:
            from controller_integrity import hid
            if hid is None:
                return None
            return any(item.get("path") == path for item in hid.enumerate())
        except Exception:
            LOGGER.debug("Raw HID presence enumeration failed", exc_info=True)
            return None

    @staticmethod
    def backend_diagnostics() -> list[dict]:
        """Probe every read-only controller backend and return user-facing results."""
        try:
            from controller_integrity import HIDGamepad, SDLJoystick, WinMMJoystick, XInputGamepad
        except Exception as exc:
            return [{"backend": "backend import", "detected": False, "status": f"failed: {exc}"}]

        results: list[dict] = []
        try:
            xinput = XInputGamepad()
            sample = xinput.read()
            results.append({"backend": "XInput", "detected": sample is not None, "status": xinput.status()})
        except Exception as exc:
            results.append({"backend": "XInput", "detected": False, "status": f"failed: {exc}"})

        try:
            sdl_count = SDLJoystick.device_count()
            sdl = SDLJoystick()
            sample = sdl.read()
            results.append({
                "backend": "SDL",
                "detected": sample is not None,
                "status": sdl.status() if sample is not None else f"{sdl_count} SDL joystick(s) enumerated",
            })
        except Exception as exc:
            results.append({"backend": "SDL", "detected": False, "status": f"failed: {exc}"})

        try:
            winmm = WinMMJoystick()
            sample = winmm.read()
            results.append({"backend": "DirectInput/WinMM", "detected": sample is not None, "status": winmm.status()})
        except Exception as exc:
            results.append({"backend": "DirectInput/WinMM", "detected": False, "status": f"failed: {exc}"})

        try:
            raw_devices = HIDGamepad.enumerate_devices()
            results.append({
                "backend": "Raw HID",
                "detected": bool(raw_devices),
                "status": f"{len(raw_devices)} controller-like HID device(s) enumerated",
                "devices": [
                    {
                        "product": info.get("product_string"),
                        "manufacturer": info.get("manufacturer_string"),
                        "vendor_id": info.get("vendor_id"),
                        "product_id": info.get("product_id"),
                        "usage_page": info.get("usage_page"),
                        "usage": info.get("usage"),
                    }
                    for info in raw_devices
                ],
            })
        except Exception as exc:
            results.append({"backend": "Raw HID", "detected": False, "status": f"failed: {exc}"})
        return results

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
        # Stopping the Python thread does not close native HID/SDL handles.
        # Release them whenever a source is refreshed or replaced.
        self._close_backend()

    def _close_backend(self) -> None:
        backend = self.backend
        if backend is None:
            return

        candidates = [backend]
        for name in ("hid", "sdl", "active"):
            child = getattr(backend, name, None)
            if child is not None and child not in candidates:
                candidates.append(child)

        for candidate in candidates:
            for name in ("close", "quit"):
                close = getattr(candidate, name, None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        LOGGER.debug("Controller backend cleanup failed", exc_info=True)
            device = getattr(candidate, "device", None)
            close_device = getattr(device, "close", None)
            if callable(close_device):
                try:
                    close_device()
                except Exception:
                    LOGGER.debug("HID device cleanup failed", exc_info=True)
            joystick = getattr(candidate, "joystick", None)
            quit_joystick = getattr(joystick, "quit", None)
            if callable(quit_joystick):
                try:
                    quit_joystick()
                except Exception:
                    LOGGER.debug("SDL joystick cleanup failed", exc_info=True)

        self.backend = None

    @staticmethod
    def _raw_hid_reports(
        active,
        first_timestamp_ns: int,
        first_sample: dict | None,
    ) -> list[tuple[int, dict, list[int]]]:
        """Drain every available Raw HID report with a host-arrival timestamp.

        hidapi is configured non-blocking by the backend.  Reading only once
        per acquisition loop artificially caps high-rate devices at the loop
        cadence, and stamping a drained batch with one timestamp destroys the
        interval information needed for rate/jitter analysis.  The first
        report comes from ``HIDGamepad.read``; the remaining reports are
        drained directly from its non-blocking device handle.
        """
        reports: list[tuple[int, dict, list[int]]] = []
        last_timestamp_ns = first_timestamp_ns - 1
        parse_report = getattr(active, "_parse_report", None)
        drain = getattr(active, "drain_raw_reports", None)
        pending = drain() if callable(drain) else []
        if not isinstance(pending, list):
            pending = []

        for report in pending:
            raw_report = ControllerAcquisition._coerce_raw_report(report)
            if raw_report is None:
                continue
            sample = parse_report(raw_report) if callable(parse_report) else None
            sample_dict = ControllerAcquisition._coerce_sample(sample)
            if sample_dict is not None:
                timestamp_ns = max(
                    first_timestamp_ns if not reports else time.perf_counter_ns(),
                    last_timestamp_ns + 1,
                )
                last_timestamp_ns = timestamp_ns
                reports.append((timestamp_ns, sample_dict, raw_report))

        device = getattr(active, "device", None)
        read = getattr(device, "read", None)
        if not callable(read):
            return reports

        # Keep draining until hidapi says the non-blocking queue is empty.
        # The bound prevents a continuously misbehaving device from starving
        # the stop event indefinitely while still covering high-rate bursts.
        for _ in range(4096):
            report = read(128)
            if not report:
                break
            raw_report = ControllerAcquisition._coerce_raw_report(report)
            if raw_report is None:
                continue
            timestamp_ns = max(time.perf_counter_ns(), last_timestamp_ns + 1)
            sample = parse_report(raw_report) if callable(parse_report) else None
            sample_dict = ControllerAcquisition._coerce_sample(sample)
            if sample_dict is not None:
                last_timestamp_ns = timestamp_ns
                reports.append((timestamp_ns, sample_dict, raw_report))
        return reports

    @staticmethod
    def _coerce_raw_report(report) -> list[int] | None:
        if isinstance(report, (bytes, bytearray)):
            return list(report)
        if not isinstance(report, list):
            return None
        values: list[int] = []
        for value in report:
            if not isinstance(value, int):
                return None
            values.append(value)
        return values

    @staticmethod
    def _coerce_sample(sample) -> dict | None:
        return dict(sample) if isinstance(sample, dict) else None

    def _run(self) -> None:
        try:
            from controller_integrity import AutomaticControllerBackend, HIDGamepad
            if self.source_kind == "raw_hid":
                self.backend = HIDGamepad(path=self.hid_path, info=self.hid_info)
            else:
                self.backend = AutomaticControllerBackend()
        except Exception as exc:
            self.error = f"Controller backend initialization failed: {exc}"
            self._event("controller_backend_error", {"message": self.error})
            return

        last_host_sample_ns = 0
        last_seen = 0.0
        last_presence_check = 0.0
        connected = False
        device_present = True
        missing_presence_checks = 0
        idle_reported = False
        last_buttons: int | None = None
        last_raw_hex: str | None = None
        last_error: str | None = None

        while not self.stop_event.is_set():
            raw_report_count = 0
            if self.source_kind == "raw_hid":
                now_monotonic = time.monotonic()
                if now_monotonic - last_presence_check >= RAW_HID_PRESENCE_CHECK_S:
                    last_presence_check = now_monotonic
                    present = self.raw_hid_path_present(self.hid_path)
                    if present is False:
                        missing_presence_checks += 1
                        if device_present and missing_presence_checks >= 2:
                            device_present = False
                            connected = False
                            idle_reported = False
                            last_buttons = None
                            self._event("controller_disconnected", {"reason": "device_removed"})
                    elif present is True and not device_present:
                        missing_presence_checks = 0
                        device_present = True
                        self._event("controller_device_present", {})
                    elif present is True:
                        missing_presence_checks = 0
                    else:
                        missing_presence_checks = 0
                if self.source_kind == "raw_hid" and not device_present:
                    self.stop_event.wait(self.poll_sleep_s)
                    continue
            try:
                sample = self.backend.read()
                now = time.perf_counter_ns()
                active = getattr(self.backend, "active", None)
                if active is None and self.source_kind == "raw_hid":
                    active = self.backend
                source = (
                    getattr(active, "status", lambda: getattr(active, "name", "Controller"))()
                    if active is not None else "Controller"
                )

                metadata = self._backend_metadata(active)
                if active is not None and active.__class__.__name__ == "HIDGamepad":
                    metadata["evidence_class"] = "measured-host-observed-raw-hid"
                    metadata["capture_timestamp"] = "host-arrival-per-report"
                elif active is not None:
                    metadata["evidence_class"] = "host-poll-estimate"
                    metadata["capture_timestamp"] = "host-poll"

                if sample is not None:
                    last_seen = time.monotonic()
                    if idle_reported:
                        idle_reported = False
                        self._event("controller_input_active", {})
                    if not connected:
                        connected = True
                        self._event("controller_connected", {"source": source, "metadata": metadata})
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
                    reports = self._raw_hid_reports(active, now, sample)
                    raw_report_count = len(reports)
                    if raw_report_count:
                        last_seen = time.monotonic()
                        if idle_reported:
                            idle_reported = False
                            self._event("controller_input_active", {})
                        if not connected:
                            connected = True
                            self._event("controller_connected", {"source": source, "metadata": metadata})
                    for report_timestamp_ns, report_sample, report in reports:
                        raw_hex = bytes(report).hex()
                        duplicate = last_raw_hex == raw_hex
                        last_raw_hex = raw_hex
                        self.callback(ControllerMeasurement(
                            timestamp_ns=report_timestamp_ns,
                            sample=report_sample,
                            source=source,
                            timing_quality="measured-at-host-read",
                            raw_report_hex=raw_hex,
                            duplicate_raw_report=duplicate,
                            metadata=metadata,
                        ))
                elif sample is not None and now - last_host_sample_ns >= int(self.poll_sleep_s * 1e9):
                    last_host_sample_ns = now
                    self.callback(ControllerMeasurement(
                        timestamp_ns=now,
                        sample=dict(sample),
                        source=getattr(active, "name", "Host controller API") if active is not None else "Host controller API",
                        timing_quality="host-poll-estimate",
                        metadata=metadata,
                    ))

                if (
                    self.source_kind != "raw_hid"
                    and connected
                    and sample is None
                    and time.monotonic() - last_seen >= DISCONNECT_TIMEOUT_S
                ):
                    connected = False
                    last_buttons = None
                    self._event("controller_disconnected", {})

                if (
                    self.source_kind == "raw_hid"
                    and device_present
                    and connected
                    and last_seen
                    and not idle_reported
                    and time.monotonic() - last_seen >= RAW_HID_IDLE_AFTER_S
                ):
                    idle_reported = True
                    self._event("controller_input_idle", {})

                last_error = None
            except Exception as exc:
                self.error = str(exc)
                if self.error != last_error:
                    self._event("controller_backend_error", {"message": self.error})
                    last_error = self.error
                if (
                    self.source_kind != "raw_hid"
                    and connected
                    and time.monotonic() - last_seen >= DISCONNECT_TIMEOUT_S
                ):
                    connected = False
                    last_buttons = None
                    self._event(
                        "controller_disconnected",
                        {"reason": "read_error", "message": self.error},
                    )
            if raw_report_count:
                # A short yield prevents a hot loop from starving the rest of
                # the process without imposing a 1 ms ceiling on Raw HID.
                time.sleep(min(self.poll_sleep_s, 0.00005))
            else:
                self.stop_event.wait(self.poll_sleep_s)
        self._close_backend()
