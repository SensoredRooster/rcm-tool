"""RCM Tool prototype.

This is a tournament-certification prototype. It captures controller telemetry
without injecting input into a game, runs a neutral-stick test, and exports a
hash-linked JSON report for later review.

The first capture backend is Windows XInput. Additional backends can implement
the same ControllerBackend interface without changing the UI or report format.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import platform
import re
import statistics
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Protocol

from support import log_event, start_heartbeat
from support_dialog import open_support_center

try:
    import pygame
except ImportError:  # Optional until the SDL backend is installed.
    pygame = None

try:
    import hid
except ImportError:  # Optional until the Raw HID backend is installed.
    hid = None


REPORT_VERSION = "0.1"
TEST_DURATION_SECONDS = 10.0
MOTION_TEST_DURATION_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 0.004


class ControllerBackend(Protocol):
    name: str

    def read(self) -> Optional[dict[str, float]]:
        """Return normalized axes, or None when no controller is available."""


class _XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("buttons", ctypes.c_ushort),
        ("left_trigger", ctypes.c_ubyte),
        ("right_trigger", ctypes.c_ubyte),
        ("thumb_lx", ctypes.c_short),
        ("thumb_ly", ctypes.c_short),
        ("thumb_rx", ctypes.c_short),
        ("thumb_ry", ctypes.c_short),
    ]


class _XInputState(ctypes.Structure):
    _fields_ = [("packet_number", ctypes.c_ulong), ("gamepad", _XInputGamepad)]


class XInputGamepad:
    """Minimal standard-library XInput reader for Windows gamepads."""

    name = "Windows XInput"

    def __init__(self, user_index: int = 0, scan_all: bool = True) -> None:
        self.user_index = user_index
        self.scan_all = scan_all
        self.connected_user_index: Optional[int] = None
        self.dll = None
        self.get_state = None
        for dll_name in ("xinput1_4.dll", "xinput9_1_0.dll", "xinput1_3.dll"):
            try:
                self.dll = ctypes.WinDLL(dll_name)
                self.get_state = self.dll.XInputGetState
                self.get_state.argtypes = [ctypes.c_uint, ctypes.POINTER(_XInputState)]
                self.get_state.restype = ctypes.c_uint
                break
            except (AttributeError, OSError):
                continue

    @staticmethod
    def _normalize(value: int, denominator: float) -> float:
        # Keep the output bounded and preserve the sign of the raw report.
        return max(-1.0, min(1.0, value / denominator))

    def read(self) -> Optional[dict[str, float]]:
        if self.get_state is None:
            return None
        state = _XInputState()
        connected_index = self.connected_user_index
        if self.scan_all:
            indexes = [connected_index] if connected_index is not None else list(range(4))
        else:
            indexes = [self.user_index]
        for index in indexes:
            state = _XInputState()
            if self.get_state(index, ctypes.byref(state)) == 0:
                connected_index = index
                break
        else:
            self.connected_user_index = None
            return None
        self.connected_user_index = connected_index
        pad = state.gamepad
        return {
            "lx": self._normalize(pad.thumb_lx, 32768.0),
            "ly": self._normalize(pad.thumb_ly, 32768.0),
            "rx": self._normalize(pad.thumb_rx, 32768.0),
            "ry": self._normalize(pad.thumb_ry, 32768.0),
            "lt": pad.left_trigger / 255.0,
            "rt": pad.right_trigger / 255.0,
        }

    def status(self) -> str:
        if self.get_state is None:
            return "XInput is unavailable on this Windows installation"
        if self.connected_user_index is None:
            return "No XInput controller detected - try USB, Xbox mode, or another backend"
        return f"XInput slot {self.connected_user_index} is connected"


class _JoyInfoEx(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("x", ctypes.c_uint32),
        ("y", ctypes.c_uint32),
        ("z", ctypes.c_uint32),
        ("r", ctypes.c_uint32),
        ("u", ctypes.c_uint32),
        ("v", ctypes.c_uint32),
        ("buttons", ctypes.c_uint32),
        ("button_number", ctypes.c_uint32),
        ("pov", ctypes.c_uint32),
        ("reserved_1", ctypes.c_uint32),
        ("reserved_2", ctypes.c_uint32),
    ]


class WinMMJoystick:
    """Fallback for generic DirectInput-style Windows joystick devices."""

    name = "Windows Joystick (DirectInput fallback)"
    _JOY_RETURNALL = 0x000000FF

    def __init__(self, fixed_device: Optional[int] = None) -> None:
        self.fixed_device = fixed_device
        self.device_id: Optional[int] = None
        try:
            self.dll = ctypes.WinDLL("winmm.dll")
            self.get_num_devs = self.dll.joyGetNumDevs
            self.get_num_devs.restype = ctypes.c_uint32
            self.get_pos_ex = self.dll.joyGetPosEx
            self.get_pos_ex.argtypes = [ctypes.c_uint32, ctypes.POINTER(_JoyInfoEx)]
            self.get_pos_ex.restype = ctypes.c_uint32
        except OSError:
            self.dll = None
            self.get_num_devs = None
            self.get_pos_ex = None

    @staticmethod
    def _normalize(value: int) -> float:
        return max(-1.0, min(1.0, (value - 32767.5) / 32767.5))

    def read(self) -> Optional[dict[str, float]]:
        if self.get_pos_ex is None:
            return None
        if self.fixed_device is not None:
            candidate_ids = [self.fixed_device]
        elif self.device_id is not None:
            candidate_ids = [self.device_id]
        else:
            candidate_ids = range(self.get_num_devs())
        for device_id in candidate_ids:
            info = _JoyInfoEx(size=ctypes.sizeof(_JoyInfoEx), flags=self._JOY_RETURNALL)
            if self.get_pos_ex(device_id, ctypes.byref(info)) == 0:
                self.device_id = device_id
                return {
                    "lx": self._normalize(info.x),
                    "ly": -self._normalize(info.y),
                    "rx": self._normalize(info.r),
                    "ry": -self._normalize(info.u),
                    "lt": info.z / 65535.0,
                    "rt": info.v / 65535.0,
                }
        self.device_id = None
        return None

    def status(self) -> str:
        if self.get_pos_ex is None:
            return "DirectInput fallback is unavailable"
        if self.device_id is None:
            return "No DirectInput joystick detected"
        return f"DirectInput device {self.device_id} is connected"


class SDLJoystick:
    """Broad controller backend using SDL through pygame."""

    name = "SDL controller backend"

    def __init__(self, fixed_index: Optional[int] = None) -> None:
        self.fixed_index = fixed_index
        self.active_index: Optional[int] = None
        self.joystick = None
        if pygame is not None:
            try:
                pygame.init()
                pygame.joystick.init()
            except Exception:
                pass

    @staticmethod
    def available() -> bool:
        return pygame is not None

    @staticmethod
    def device_count() -> int:
        if pygame is None:
            return 0
        try:
            pygame.joystick.init()
            return pygame.joystick.get_count()
        except Exception:
            return 0

    def _open(self, index: int) -> bool:
        try:
            pygame.event.pump()
            joystick = pygame.joystick.Joystick(index)
            if not joystick.get_init():
                joystick.init()
            self.joystick = joystick
            self.active_index = index
            return True
        except Exception:
            self.joystick = None
            self.active_index = None
            return False

    @staticmethod
    def _axis(joystick, index: int) -> float:
        try:
            if index < joystick.get_numaxes():
                return max(-1.0, min(1.0, float(joystick.get_axis(index))))
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _trigger(value: float) -> float:
        # SDL devices commonly expose triggers as either [-1, 1] or [0, 1].
        return max(0.0, min(1.0, (value + 1.0) / 2.0 if value < 0.0 else value))

    def read(self) -> Optional[dict[str, float]]:
        if pygame is None:
            return None
        try:
            pygame.event.pump()
            count = pygame.joystick.get_count()
        except Exception:
            return None
        candidates = [self.fixed_index] if self.fixed_index is not None else (
            [self.active_index] if self.active_index is not None else range(count)
        )
        for index in candidates:
            if index is None or index < 0 or index >= count:
                continue
            if self.joystick is None or self.active_index != index:
                if not self._open(index):
                    continue
            lx = self._axis(self.joystick, 0)
            ly = -self._axis(self.joystick, 1)
            rx = self._axis(self.joystick, 2)
            ry = -self._axis(self.joystick, 3)
            return {
                "lx": lx,
                "ly": ly,
                "rx": rx,
                "ry": ry,
                "lt": self._trigger(self._axis(self.joystick, 4)),
                "rt": self._trigger(self._axis(self.joystick, 5)),
            }
        self.joystick = None
        self.active_index = None
        return None

    def status(self) -> str:
        if pygame is None:
            return "SDL backend is not installed - run: python -m pip install -r requirements.txt"
        if self.joystick is None or self.active_index is None:
            return "No SDL controller detected"
        try:
            device_name = self.joystick.get_name()
        except Exception:
            device_name = "unknown device"
        return f"SDL device {self.active_index} is connected: {device_name}"


class HIDGamepad:
    """Raw HID reader with a DualSense-compatible axis parser and generic fallback."""

    name = "Raw HID controller"
    _KEYWORDS = ("controller", "gamepad", "joystick", "dualsense", "dualshock", "wireless")

    def __init__(self, path=None, info: Optional[dict] = None) -> None:
        self.path = path
        self.info = info or {}
        self.device = None
        self.last_sample: Optional[dict[str, float]] = None
        self.raw_reports: list[list[int]] = []

    @staticmethod
    def available() -> bool:
        return hid is not None

    @classmethod
    def enumerate_devices(cls) -> list[dict]:
        if hid is None:
            return []
        try:
            devices = hid.enumerate()
        except Exception:
            return []
        result = []
        for info in devices:
            usage_page = info.get("usage_page")
            usage = info.get("usage")
            product = (info.get("product_string") or "").strip()
            searchable = " ".join(
                str(info.get(key) or "") for key in ("product_string", "manufacturer_string", "serial_number")
            ).lower()
            is_joystick_usage = usage_page == 0x01 and usage in (0x04, 0x05)
            has_controller_name = any(keyword in searchable for keyword in cls._KEYWORDS)
            if is_joystick_usage or has_controller_name:
                result.append(info)
        return result

    def _open(self) -> bool:
        if hid is None:
            return False
        if self.path is None:
            devices = self.enumerate_devices()
            if not devices:
                return False
            self.info = devices[0]
            self.path = self.info.get("path")
        if not self.path:
            return False
        try:
            self.device = hid.device()
            self.device.open_path(self.path)
            self.device.set_nonblocking(True)
            return True
        except Exception:
            self.device = None
            return False

    @staticmethod
    def _axis(value: int, invert: bool = False) -> float:
        normalized = max(-1.0, min(1.0, (value - 128.0) / 127.5))
        return -normalized if invert else normalized

    def _parse_report(self, report: list[int]) -> Optional[dict[str, float]]:
        if len(report) < 5:
            return None
        product = (self.info.get("product_string") or "").lower()
        vendor_id = self.info.get("vendor_id")
        sony = vendor_id == 0x054C or "dualsense" in product or "dualshock" in product or "wireless controller" in product
        report_id = report[0]
        offset = 2 if sony and report_id in (0x31, 0x11) else 1 if report_id != 0 else 1
        if len(report) < offset + 4:
            return None
        sample = {
            "lx": self._axis(report[offset]),
            "ly": self._axis(report[offset + 1], invert=True),
            "rx": self._axis(report[offset + 2]),
            "ry": self._axis(report[offset + 3], invert=True),
            "lt": report[offset + 4] / 255.0 if len(report) > offset + 4 else 0.0,
            "rt": report[offset + 5] / 255.0 if len(report) > offset + 5 else 0.0,
        }
        self.last_sample = sample
        return sample

    def read(self) -> Optional[dict[str, float]]:
        if self.device is None and not self._open():
            return None
        try:
            report = self.device.read(128)
        except Exception:
            self.device = None
            return None
        if report:
            self.raw_reports.append(list(report))
            parsed = self._parse_report(report)
            if parsed is not None:
                return parsed
        return self.last_sample

    def drain_raw_reports(self) -> list[list[int]]:
        reports = self.raw_reports
        self.raw_reports = []
        return reports

    def status(self) -> str:
        if hid is None:
            return "Raw HID backend is not installed - run: python -m pip install -r requirements.txt"
        product = self.info.get("product_string") or "unnamed HID controller"
        vendor_id = self.info.get("vendor_id")
        product_id = self.info.get("product_id")
        if vendor_id is not None and product_id is not None:
            return f"Raw HID connected: {product} (VID {vendor_id:04X}, PID {product_id:04X})"
        return f"Raw HID connected: {product}"


class AutomaticControllerBackend:
    """Prefer XInput, then SDL, Raw HID, and DirectInput for broad coverage."""

    name = "Automatic (XInput + SDL + Raw HID + DirectInput)"

    def __init__(self) -> None:
        self.xinput = XInputGamepad()
        self.winmm = WinMMJoystick()
        self.sdl = SDLJoystick()
        self.hid = HIDGamepad()
        self.active: Optional[ControllerBackend] = None

    def read(self) -> Optional[dict[str, float]]:
        sample = self.xinput.read()
        if sample is not None:
            self.active = self.xinput
            return sample
        sample = self.sdl.read()
        if sample is not None:
            self.active = self.sdl
            return sample
        sample = self.hid.read()
        if sample is not None:
            self.active = self.hid
            return sample
        sample = self.winmm.read()
        if sample is not None:
            self.active = self.winmm
            return sample
        self.active = None
        return None

    def status(self) -> str:
        if self.active is not None:
            return self.active.status()
        if self.xinput.get_state is None and self.winmm.get_pos_ex is None and not SDLJoystick.available() and not HIDGamepad.available():
            return "No supported controller backend is available"
        return "No controller detected - connect it in XInput, DirectInput, SDL, or Raw HID mode"


@dataclass
class AxisMetrics:
    samples: int
    mean: float
    rms: float
    peak_to_peak: float
    minimum: float
    maximum: float
    deadzone_crossings: int
    nonzero_stationary_percent: float
    jitter_rms: float = 0.0
    jitter_peak_to_peak: float = 0.0
    velocity_rms: float = 0.0
    direction_reversals: int = 0


@dataclass
class TestResult:
    test_name: str
    started_at_utc: str
    duration_seconds: float
    sample_rate_hz: float
    backend: str
    axes: dict[str, AxisMetrics]
    classification: str
    review_reasons: list[str]
    source_status: str = ""
    samples: list[dict[str, float]] = field(default_factory=list)
    hid_reports: list[dict[str, object]] = field(default_factory=list)
    phase: str = "unspecified"
    protocol: str = "neutral-stick"


def axis_metrics(values: list[float], threshold: float = 0.02) -> AxisMetrics:
    if not values:
        return AxisMetrics(0, 0, 0, 0, 0, 0, 0, 0)
    mean = statistics.fmean(values)
    rms = math.sqrt(statistics.fmean([x * x for x in values]))
    crossings = sum(
        1
        for before, after in zip(values, values[1:])
        if abs(before) <= threshold < abs(after)
        or abs(before) > threshold >= abs(after)
    )
    nonzero = sum(abs(x) > threshold for x in values) / len(values) * 100.0
    if len(values) < 2:
        jitter_rms = 0.0
        jitter_peak_to_peak = 0.0
        velocity_rms = 0.0
        reversals = 0
    else:
        # Estimate high-frequency jitter as the residual from a slow EMA.
        # This is a measurement heuristic, not a claim about the controller's firmware filter.
        smooth = values[0]
        residuals: list[float] = []
        velocities: list[float] = []
        for previous, current in zip(values, values[1:]):
            smooth = 0.12 * current + 0.88 * smooth
            residuals.append(current - smooth)
            velocities.append(current - previous)
        jitter_rms = math.sqrt(statistics.fmean([x * x for x in residuals])) if residuals else 0.0
        jitter_peak_to_peak = max(residuals) - min(residuals) if residuals else 0.0
        velocity_rms = math.sqrt(statistics.fmean([x * x for x in velocities])) if velocities else 0.0
        reversals = sum(
            1
            for before, after in zip(velocities, velocities[1:])
            if abs(before) >= 0.002 and abs(after) >= 0.002 and before * after < 0
        )
    return AxisMetrics(
        samples=len(values),
        mean=mean,
        rms=rms,
        peak_to_peak=max(values) - min(values),
        minimum=min(values),
        maximum=max(values),
        deadzone_crossings=crossings,
        nonzero_stationary_percent=nonzero,
        jitter_rms=jitter_rms,
        jitter_peak_to_peak=jitter_peak_to_peak,
        velocity_rms=velocity_rms,
        direction_reversals=reversals,
    )


def classify(result: TestResult) -> tuple[str, list[str]]:
    """Conservative screening only; this does not establish misconduct."""
    reasons: list[str] = []
    if any(result.axes[axis].samples == 0 for axis in ("lx", "ly", "rx", "ry")):
        return "unsupported", ["No complete controller sample was captured"]
    if result.sample_rate_hz < 20.0:
        return "unsupported", [f"Capture rate is too low ({result.sample_rate_hz:.1f} samples/sec)"]
    if result.protocol == "guided-movement":
        for axis_name in ("lx", "ly", "rx", "ry"):
            metrics = result.axes[axis_name]
            if metrics.jitter_rms >= 0.015:
                reasons.append(f"{axis_name} has elevated movement jitter RMS")
            if metrics.jitter_peak_to_peak >= 0.06:
                reasons.append(f"{axis_name} has a large high-frequency jitter range")
        return ("review", reasons) if reasons else ("pass", [])
    for axis_name in ("lx", "ly", "rx", "ry"):
        metrics = result.axes[axis_name]
        if metrics.nonzero_stationary_percent >= 5.0:
            reasons.append(f"{axis_name} exceeds neutral activity threshold")
        if metrics.rms >= 0.015:
            reasons.append(f"{axis_name} has elevated stationary RMS noise")
        if metrics.deadzone_crossings >= 10:
            reasons.append(f"{axis_name} repeatedly crosses the review threshold")
    return ("review", reasons) if reasons else ("pass", [])


def sha256_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def reports_directory() -> Path:
    directory = Path(__file__).resolve().parent / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_filename(value: str, fallback: str = "controller") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return (cleaned[:80] or fallback)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RCM Tool")
        self.geometry("900x620")
        self.minsize(760, 520)
        self.backend: ControllerBackend = AutomaticControllerBackend()
        self.test_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.latest_result: Optional[TestResult] = None
        self.pair_mode = False
        self.pair_results: dict[str, TestResult] = {}
        self.pair_report_paths: dict[str, Path] = {}
        self.current_test_phase = "Before - default"
        self.current_protocol = "neutral-stick"
        self.current_duration = TEST_DURATION_SECONDS
        self.source_choices: list[str] = []
        self.source_backends: dict[str, ControllerBackend] = {}
        self.current_values = {axis: 0.0 for axis in ("lx", "ly", "rx", "ry")}
        self.axes_seen: set[str] = set()
        self.value_labels: dict[str, ttk.Label] = {}
        self.axis_bars: dict[str, ttk.Progressbar] = {}
        self.metric_labels: dict[str, ttk.Label] = {}
        reports_directory()
        start_heartbeat()
        log_event("app_start")
        self._build_ui()
        self.after(100, self._refresh_live_values)

    def _build_ui(self) -> None:
        padding = {"padx": 12, "pady": 8}
        header = ttk.Frame(self)
        header.pack(fill="x", **padding)
        ttk.Label(header, text="RCM Tool", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="Controller certification • reads input only; never injects input into a game",
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Quick start: connect controller -> Detect devices -> choose Connected -> move sticks -> run test -> export report",
            foreground="#1f5f8b",
        ).pack(side="left", anchor="w", pady=(6, 0))
        ttk.Button(header, text="Support & Diagnostics", command=lambda: open_support_center(self)).pack(
            side="right", anchor="e", padx=(12, 0)
        )

        source = ttk.LabelFrame(self, text="1. Select where RCM Tool should read controller input")
        source.pack(fill="x", **padding)
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(source, textvariable=self.source_var, state="readonly", width=58)
        self.source_combo.grid(row=0, column=0, sticky="w", **padding)
        self.source_combo.bind("<<ComboboxSelected>>", self._source_changed)
        self.refresh_button = ttk.Button(source, text="Detect devices", command=self.refresh_devices)
        self.refresh_button.grid(row=0, column=1, **padding)
        self.backend_var = tk.StringVar(value=self.backend.name)
        ttk.Label(source, textvariable=self.backend_var).grid(row=1, column=0, sticky="w", **padding)
        self.input_status_var = tk.StringVar(value="Detecting devices...")
        ttk.Label(source, textvariable=self.input_status_var, wraplength=700).grid(row=2, column=0, columnspan=2, sticky="w", **padding)

        controls = ttk.LabelFrame(self, text="2. Run the certification test")
        controls.pack(fill="x", **padding)
        self.status_var = tk.StringVar(value="Ready")
        self.duration_var = tk.StringVar(value=f"Duration: {TEST_DURATION_SECONDS:g} seconds")
        ttk.Label(controls, textvariable=self.duration_var).grid(row=0, column=0, sticky="w", **padding)
        ttk.Label(controls, text="Phase:").grid(row=0, column=1, sticky="e", **padding)
        self.phase_var = tk.StringVar(value="Before - default")
        self.phase_combo = ttk.Combobox(
            controls,
            textvariable=self.phase_var,
            values=("Before - default", "After - modified"),
            state="readonly",
            width=18,
        )
        self.phase_combo.grid(row=0, column=2, **padding)
        ttk.Label(controls, text="Protocol:").grid(row=0, column=3, sticky="e", **padding)
        self.protocol_var = tk.StringVar(value="Neutral hold")
        self.protocol_combo = ttk.Combobox(
            controls,
            textvariable=self.protocol_var,
            values=("Neutral hold", "Guided movement"),
            state="readonly",
            width=18,
        )
        self.protocol_combo.grid(row=0, column=4, **padding)
        self.protocol_combo.bind("<<ComboboxSelected>>", self._protocol_changed)
        self.start_button = ttk.Button(controls, text="Start single test", command=self.start_test)
        self.start_button.grid(row=1, column=0, **padding)
        self.pair_button = ttk.Button(controls, text="Run Before + After pair", command=self.start_pair_test)
        self.pair_button.grid(row=1, column=1, columnspan=2, sticky="w", **padding)
        self.export_button = ttk.Button(controls, text="Export to reports", command=self.export_report, state="disabled")
        self.export_button.grid(row=1, column=3, **padding)
        self.open_reports_button = ttk.Button(controls, text="Open reports folder", command=self.open_reports_folder)
        self.open_reports_button.grid(row=1, column=4, **padding)
        ttk.Label(controls, textvariable=self.status_var).grid(row=2, column=0, columnspan=6, sticky="w", **padding)

        live = ttk.LabelFrame(self, text="3. Verify live input before testing")
        live.pack(fill="x", **padding)
        for index, axis in enumerate(("lx", "ly", "rx", "ry")):
            ttk.Label(live, text=axis.upper()).grid(row=0, column=index, **padding)
            label = ttk.Label(live, text="0.0000", width=10, anchor="center", font=("Consolas", 13))
            label.grid(row=1, column=index, **padding)
            self.value_labels[axis] = label
            bar = ttk.Progressbar(live, orient="horizontal", length=145, mode="determinate", maximum=100)
            bar["value"] = 50
            bar.grid(row=2, column=index, padx=12, pady=(0, 8))
            self.axis_bars[axis] = bar
        self.live_help_var = tk.StringVar(value="Move each stick. The number and bar must change before you start a test.")
        ttk.Label(live, textvariable=self.live_help_var, foreground="#555555").grid(
            row=3, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8)
        )
        self.axes_seen_var = tk.StringVar(value="Axes seen: none")
        ttk.Label(live, textvariable=self.axes_seen_var, foreground="#555555").grid(
            row=4, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8)
        )

        results = ttk.LabelFrame(self, text="4. Read the result")
        results.pack(fill="both", expand=True, **padding)
        columns = ("axis", "rms", "jitter", "peak", "crossings", "active")
        self.tree = ttk.Treeview(results, columns=columns, show="headings", height=8)
        headings = {"axis": "Axis", "rms": "Signal RMS", "jitter": "Jitter RMS", "peak": "Peak-to-peak", "crossings": "Threshold crossings", "active": "Active while stationary"}
        widths = {"axis": 70, "rms": 105, "jitter": 105, "peak": 125, "crossings": 160, "active": 175}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="center")
        self.tree.pack(fill="x", padx=8, pady=8)
        self.result_var = tk.StringVar(value="No test has been run.")
        ttk.Label(results, textvariable=self.result_var, wraplength=780, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=8, pady=(4, 2))
        self.result_help_var = tk.StringVar(value="Run a neutral test to see whether the controller produces movement while untouched.")
        ttk.Label(results, textvariable=self.result_help_var, wraplength=780).pack(anchor="w", padx=8, pady=(0, 6))
        ttk.Label(
            results,
            text="Signal RMS = total signal energy. Jitter RMS = estimated high-frequency movement left after a slow trend is removed. Peak-to-peak = total range. Threshold crossings = review-boundary crossings. Active while stationary = time input looked moved while untouched.",
            wraplength=780,
            foreground="#555555",
        ).pack(anchor="w", padx=8, pady=(0, 8))

        self.report_location_var = tk.StringVar(value=f"Reports save automatically to: {reports_directory()}")
        footer = ttk.Label(self, textvariable=self.report_location_var, foreground="#555555")
        footer.pack(fill="x", padx=12, pady=(0, 10))
        self.refresh_devices()

    def refresh_devices(self) -> None:
        """Probe the available Windows input locations and populate the selector."""
        if self.test_thread and self.test_thread.is_alive():
            return
        choices = ["Automatic - scan XInput, SDL, Raw HID, then DirectInput"]
        backends: dict[str, ControllerBackend] = {choices[0]: AutomaticControllerBackend()}

        for slot in range(4):
            backend = XInputGamepad(user_index=slot, scan_all=False)
            connected = backend.read() is not None
            label = f"XInput slot {slot} - {'connected' if connected else 'not connected'}"
            choices.append(label)
            backends[label] = backend

        probe = WinMMJoystick()
        if probe.get_num_devs is not None:
            for device_id in range(probe.get_num_devs()):
                backend = WinMMJoystick(fixed_device=device_id)
                if backend.read() is not None:
                    label = f"DirectInput device {device_id} - connected"
                    choices.append(label)
                    backends[label] = backend

        if SDLJoystick.available():
            for device_id in range(SDLJoystick.device_count()):
                backend = SDLJoystick(fixed_index=device_id)
                if backend.read() is not None:
                    try:
                        device_name = backend.joystick.get_name()
                    except Exception:
                        device_name = "unknown device"
                    label = f"SDL device {device_id} - {device_name}"
                    choices.append(label)
                    backends[label] = backend

        for info in HIDGamepad.enumerate_devices():
            path = info.get("path")
            if not path:
                continue
            product = info.get("product_string") or "unnamed HID controller"
            vendor_id = info.get("vendor_id") or 0
            product_id = info.get("product_id") or 0
            label = f"Raw HID - {product} (VID {vendor_id:04X}, PID {product_id:04X})"
            choices.append(label)
            backends[label] = HIDGamepad(path=path, info=info)

        self.source_choices = choices
        self.source_backends = backends
        self.source_combo["values"] = choices
        if self.source_var.get() not in choices:
            self.source_var.set(choices[0])
        self._source_changed()

    def _protocol_changed(self, _event=None) -> None:
        if self.protocol_var.get() == "Guided movement":
            self.duration_var.set(f"Duration: {MOTION_TEST_DURATION_SECONDS:g} seconds")
        else:
            self.duration_var.set(f"Duration: {TEST_DURATION_SECONDS:g} seconds")

    def _select_protocol(self) -> None:
        if self.protocol_var.get() == "Guided movement":
            self.current_protocol = "guided-movement"
            self.current_duration = MOTION_TEST_DURATION_SECONDS
        else:
            self.current_protocol = "neutral-stick"
            self.current_duration = TEST_DURATION_SECONDS

    def _source_changed(self, _event=None) -> None:
        selected = self.source_var.get()
        backend = self.source_backends.get(selected)
        if backend is None:
            return
        self.backend = backend
        self.axes_seen.clear()
        self.axes_seen_var.set("Axes seen: none")
        self.backend_var.set(f"Reading through: {backend.name}")
        self.input_status_var.set(backend.status())

    def _refresh_live_values(self) -> None:
        sample = self.backend.read()
        if sample:
            self.current_values.update(sample)
            for axis, label in self.value_labels.items():
                label.configure(text=f"{self.current_values[axis]:+.4f}")
                self.axis_bars[axis]["value"] = (self.current_values[axis] + 1.0) * 50.0
                if abs(self.current_values[axis]) >= 0.05:
                    self.axes_seen.add(axis.upper())
        status = getattr(self.backend, "status", None)
        if callable(status):
            self.input_status_var.set(status())
            has_input = sample is not None or (
                isinstance(self.backend, AutomaticControllerBackend) and self.backend.active is not None
            )
            if has_input:
                if self.axes_seen:
                    seen = ", ".join(sorted(self.axes_seen))
                    self.axes_seen_var.set(f"Axes seen: {seen}")
                    self.live_help_var.set("Input detected. Move every stick direction once. If an axis never appears, select SDL or Raw HID instead of DirectInput.")
                else:
                    self.live_help_var.set("Input connection found. Move each stick to verify its axes.")
        self.after(100, self._refresh_live_values)

    def start_test(self) -> None:
        if self.test_thread and self.test_thread.is_alive():
            return
        log_event("test_requested", mode="single", protocol=self.protocol_var.get(), phase=self.phase_var.get())
        self.pair_mode = False
        self.pair_results = {}
        self.pair_report_paths = {}
        self._select_protocol()
        self.current_test_phase = self.phase_var.get()
        if not self._confirm_protocol_start():
            return
        if not self._prepare_test("single test"):
            return
        self._launch_test_thread()

    def start_pair_test(self) -> None:
        if self.test_thread and self.test_thread.is_alive():
            return
        log_event("test_requested", mode="pair", protocol=self.protocol_var.get())
        self.pair_mode = True
        self.pair_results = {}
        self.pair_report_paths = {}
        self._select_protocol()
        self.phase_var.set("Before - default")
        self.current_test_phase = "Before - default"
        if not self._confirm_protocol_start():
            self.pair_mode = False
            return
        if not self._prepare_test("before phase"):
            self.pair_mode = False
            return
        self._launch_test_thread()

    def _prepare_test(self, label: str) -> bool:
        if self.backend.read() is None:
            self.status_var.set(f"Cannot start {label}: the selected input source is not connected")
            self.input_status_var.set(self.backend.status())
            messagebox.showwarning(
                "No controller input",
                "RCM Tool cannot start because the selected input source is not returning controller data.\n\n"
                "Choose a connected source above, then click Detect devices.",
            )
            return False
        source = self.backend.active if isinstance(self.backend, AutomaticControllerBackend) else self.backend
        drain_raw = getattr(source, "drain_raw_reports", None)
        if callable(drain_raw):
            drain_raw()
        self.latest_result = None
        self.export_button.configure(state="disabled")
        self.result_var.set(f"{self.current_test_phase} test running...")
        if self.current_protocol == "guided-movement":
            self.result_help_var.set("Follow the guided movement instructions until the test completes.")
        else:
            self.result_help_var.set("Keep both sticks untouched until the test completes.")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.pair_button.configure(state="disabled")
        self.phase_combo.configure(state="disabled")
        self.source_combo.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        if self.current_protocol == "guided-movement":
            self.status_var.set(f"{self.current_test_phase} running... follow the guided movement instructions")
        else:
            self.status_var.set(f"{self.current_test_phase} running... keep both sticks untouched")
        return True

    def _confirm_protocol_start(self) -> bool:
        if self.current_protocol != "guided-movement":
            return True
        return messagebox.askokcancel(
            "Guided movement protocol",
            "During this 20-second capture, make smooth, deliberate stick movements:\n\n"
            "1. Move the left stick slowly left and right.\n"
            "2. Move the left stick slowly up and down.\n"
            "3. Repeat with the right stick.\n"
            "4. Avoid rapid shaking; the test is measuring high-frequency jitter riding on intended movement.\n\n"
            "Click OK to begin.",
        )

    def _launch_test_thread(self) -> None:
        self.test_thread = threading.Thread(target=self._run_test, daemon=True)
        self.test_thread.start()

    def _run_test(self) -> None:
        axes = {axis: [] for axis in ("lx", "ly", "rx", "ry")}
        captured_samples: list[dict[str, float]] = []
        captured_hid_reports: list[dict[str, object]] = []
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        source_status = self.backend.status()
        samples = 0
        while time.monotonic() - started < self.current_duration and not self.stop_event.is_set():
            sample = self.backend.read()
            if sample:
                for axis in axes:
                    axes[axis].append(sample[axis])
                captured_samples.append({
                    "t_ms": round((time.monotonic() - started) * 1000.0, 3),
                    **{axis: round(float(sample[axis]), 6) for axis in axes},
                })
                samples += 1
            source = self.backend.active if isinstance(self.backend, AutomaticControllerBackend) else self.backend
            drain_raw = getattr(source, "drain_raw_reports", None)
            if callable(drain_raw):
                for report in drain_raw():
                    captured_hid_reports.append({
                        "t_ms": round((time.monotonic() - started) * 1000.0, 3),
                        "length": len(report),
                        "hex": bytes(report).hex(),
                    })
            time.sleep(POLL_INTERVAL_SECONDS)
        elapsed = max(time.monotonic() - started, 0.001)
        result = TestResult(
            test_name=self.current_protocol,
            started_at_utc=started_at,
            duration_seconds=elapsed,
            sample_rate_hz=samples / elapsed,
            backend=self.backend.name,
            axes={axis: axis_metrics(values) for axis, values in axes.items()},
            classification="pending",
            review_reasons=[],
            source_status=source_status,
            samples=captured_samples,
            hid_reports=captured_hid_reports,
            phase=self.current_test_phase,
            protocol=self.current_protocol,
        )
        result.classification, result.review_reasons = classify(result)
        self.after(0, lambda: self._finish_test(result))

    def _finish_test(self, result: TestResult) -> None:
        self.latest_result = result
        if self.pair_mode:
            self.pair_results[result.phase] = result
        for axis, metrics in result.axes.items():
            self.tree.insert("", "end", values=(
                axis.upper(),
                f"{metrics.rms:.5f}",
                f"{metrics.jitter_rms:.5f}",
                f"{metrics.peak_to_peak:.5f}",
                metrics.deadzone_crossings,
                f"{metrics.nonzero_stationary_percent:.2f}%",
            ))
        reason_text = "; ".join(result.review_reasons) if result.review_reasons else "No neutral-input anomalies exceeded the initial review thresholds."
        self.result_var.set(
            f"Result: {result.classification.upper()} - {reason_text} "
            f"[{result.protocol}] "
            f"({len(result.samples)} samples at {result.sample_rate_hz:.1f}/sec; "
            f"{len(result.hid_reports)} raw HID reports)"
        )
        if result.classification == "pass":
            self.result_help_var.set("PASS means this sample stayed within the initial screening thresholds. It is not a guarantee that every controller behavior is compliant.")
        elif result.classification == "review":
            self.result_help_var.set("REVIEW means the measurements crossed an initial threshold. Save the report and compare it with a controller-specific baseline before making a decision.")
        else:
            self.result_help_var.set("UNSUPPORTED means RCM Tool did not receive a complete sample. Choose a connected input source and verify the live bars first.")
        if self.pair_mode and result.phase == "Before - default":
            before_path = self.write_report(result, show_message=False)
            self.pair_report_paths[result.phase] = before_path
            if result.classification == "unsupported":
                self.pair_mode = False
                self._enable_controls()
                self.status_var.set("Before phase did not produce usable data; After phase was not started")
                messagebox.showwarning(
                    "Before phase incomplete",
                    "The Before phase did not return a usable sample stream, so RCM Tool did not start the After phase.\n\n"
                    f"Saved report:\n{before_path}\n\nFix the input source and run the pair again.",
                )
                return
            self.status_var.set(f"Before complete and saved: {before_path.name}")
            self.after(100, self._continue_pair_after_phase)
            return

        if self.pair_mode and result.phase == "After - modified":
            after_path = self.write_report(result, show_message=False)
            self.pair_report_paths[result.phase] = after_path
            before = self.pair_results.get("Before - default")
            before_path = self.pair_report_paths.get("Before - default")
            if result.classification == "unsupported":
                self.pair_mode = False
                self._enable_controls()
                self.status_var.set("After phase did not produce usable data; pair is incomplete")
                messagebox.showwarning(
                    "After phase incomplete",
                    "The After phase did not return a usable sample stream. The Before and After reports were saved, but no comparison was finalized.",
                )
                return
            comparison_path = self.write_comparison_report(before, result, before_path, after_path) if before else None
            self.pair_mode = False
            self._enable_controls()
            self.status_var.set("Before + After pair complete")
            comparison_text = f"\nComparison saved to:\n{comparison_path}" if comparison_path else ""
            messagebox.showinfo(
                "Paired test complete",
                f"Both phases returned successfully.\n\nBefore report:\n{before_path}\n\nAfter report:\n{after_path}{comparison_text}\n\nUse RCM Tool - Submit Reports.bat to push the reports to GitHub.",
            )
            return

        self._enable_controls()
        self.status_var.set("Test complete")

    def _enable_controls(self) -> None:
        self.start_button.configure(state="normal")
        self.pair_button.configure(state="normal")
        self.phase_combo.configure(state="readonly")
        self.source_combo.configure(state="readonly")
        self.refresh_button.configure(state="normal")

    def _continue_pair_after_phase(self) -> None:
        if not self.pair_mode:
            return
        proceed = messagebox.askokcancel(
            "Before phase complete",
            "The Before phase was saved.\n\n"
            "Now apply the controller setting or hardware change you want to evaluate. "
            "RCM Tool will not add noise or modify the controller signal.\n\n"
            "Click OK when the After state is ready.",
        )
        if not proceed:
            self.pair_mode = False
            self._enable_controls()
            self.status_var.set("Paired test cancelled after Before phase")
            return
        self.phase_var.set("After - modified")
        self.current_test_phase = "After - modified"
        if not self._prepare_test("after phase"):
            self.pair_mode = False
            self._enable_controls()
            return
        self._launch_test_thread()

    def write_report(self, result: TestResult, show_message: bool = True) -> Path:
        result_data = asdict(result)
        controller_status = result.source_status or self.backend.status()
        report = {
            "reportVersion": REPORT_VERSION,
            "tool": "RCM Tool",
            "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "environment": {"os": platform.platform(), "python": platform.python_version()},
            "controller": {
                "backend": result.backend,
                "sourceStatus": controller_status,
            },
            "phase": result.phase,
            "protocol": {
                "testName": result.test_name,
                "durationSeconds": result.duration_seconds,
                "inputInjected": False,
                "noiseInjected": False,
                "jitterEstimator": "high-frequency residual from slow EMA (alpha=0.12)",
            },
            "result": result_data,
        }
        report["sha256"] = sha256_payload(report)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        controller_slug = safe_filename(controller_status)
        phase_slug = safe_filename(result.phase, fallback="phase")
        destination = reports_directory() / f"{timestamp}_{phase_slug}_{controller_slug}.json"
        suffix = 2
        while destination.exists():
            destination = reports_directory() / f"{timestamp}_{phase_slug}_{controller_slug}_{suffix}.json"
            suffix += 1
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")

        index_entry = {
            "createdAtUtc": report["createdAtUtc"],
            "file": destination.name,
            "sha256": report["sha256"],
            "classification": result.classification,
            "phase": result.phase,
            "backend": result.backend,
            "sourceStatus": controller_status,
        }
        with (reports_directory() / "index.jsonl").open("a", encoding="utf-8") as index_file:
            index_file.write(json.dumps(index_entry, separators=(",", ":")) + "\n")
        self.status_var.set(f"Report saved: reports\\{destination.name}")
        if show_message:
            messagebox.showinfo("Report exported", f"Saved to:\n{destination}\n\nSHA-256:\n{report['sha256']}")
        return destination

    def export_report(self) -> None:
        if self.latest_result:
            self.write_report(self.latest_result, show_message=True)

    def write_comparison_report(
        self,
        before: TestResult,
        after: TestResult,
        before_path: Optional[Path],
        after_path: Path,
    ) -> Path:
        deltas = {}
        for axis in ("lx", "ly", "rx", "ry"):
            before_metrics = before.axes[axis]
            after_metrics = after.axes[axis]
            deltas[axis] = {
                "rmsDelta": after_metrics.rms - before_metrics.rms,
                "jitterRmsDelta": after_metrics.jitter_rms - before_metrics.jitter_rms,
                "jitterPeakToPeakDelta": after_metrics.jitter_peak_to_peak - before_metrics.jitter_peak_to_peak,
                "peakToPeakDelta": after_metrics.peak_to_peak - before_metrics.peak_to_peak,
                "activeStationaryPercentDelta": after_metrics.nonzero_stationary_percent - before_metrics.nonzero_stationary_percent,
                "thresholdCrossingDelta": after_metrics.deadzone_crossings - before_metrics.deadzone_crossings,
            }
        comparison = {
            "reportVersion": REPORT_VERSION,
            "tool": "RCM Tool",
            "reportType": "before-after-comparison",
            "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "protocol": {
                "sameTestDuration": before.duration_seconds,
                "inputInjected": False,
                "noiseInjected": False,
                "operatorChangeRequiredBetweenPhases": True,
                "jitterEstimator": "high-frequency residual from slow EMA (alpha=0.12)",
            },
            "beforeReport": before_path.name if before_path else None,
            "afterReport": after_path.name,
            "metrics": deltas,
        }
        comparison["sha256"] = sha256_payload(comparison)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = reports_directory() / f"{timestamp}_before_after_comparison.json"
        suffix = 2
        while destination.exists():
            destination = reports_directory() / f"{timestamp}_before_after_comparison_{suffix}.json"
            suffix += 1
        destination.write_text(json.dumps(comparison, indent=2), encoding="utf-8")
        index_entry = {
            "createdAtUtc": comparison["createdAtUtc"],
            "file": destination.name,
            "sha256": comparison["sha256"],
            "classification": "comparison",
            "phase": "Before + After",
            "backend": before.backend,
            "sourceStatus": before.source_status,
        }
        with (reports_directory() / "index.jsonl").open("a", encoding="utf-8") as index_file:
            index_file.write(json.dumps(index_entry, separators=(",", ":")) + "\n")
        return destination

    def open_reports_folder(self) -> None:
        directory = reports_directory()
        try:
            os.startfile(str(directory))
        except AttributeError:
            messagebox.showinfo("Reports folder", str(directory))


def main() -> None:
    if os.name != "nt":
        print("Warning: this prototype's first backend is Windows XInput.")
    App().mainloop()


if __name__ == "__main__":
    main()
