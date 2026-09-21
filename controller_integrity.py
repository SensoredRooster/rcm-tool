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
import statistics
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Protocol


REPORT_VERSION = "0.1"
TEST_DURATION_SECONDS = 10.0
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

    def __init__(self, user_index: int = 0) -> None:
        self.user_index = user_index
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
        if self.get_state(self.user_index, ctypes.byref(state)) != 0:
            return None
        pad = state.gamepad
        return {
            "lx": self._normalize(pad.thumb_lx, 32768.0),
            "ly": self._normalize(pad.thumb_ly, 32768.0),
            "rx": self._normalize(pad.thumb_rx, 32768.0),
            "ry": self._normalize(pad.thumb_ry, 32768.0),
            "lt": pad.left_trigger / 255.0,
            "rt": pad.right_trigger / 255.0,
        }


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
    return AxisMetrics(
        samples=len(values),
        mean=mean,
        rms=rms,
        peak_to_peak=max(values) - min(values),
        minimum=min(values),
        maximum=max(values),
        deadzone_crossings=crossings,
        nonzero_stationary_percent=nonzero,
    )


def classify(result: TestResult) -> tuple[str, list[str]]:
    """Conservative screening only; this does not establish misconduct."""
    reasons: list[str] = []
    if any(result.axes[axis].samples == 0 for axis in ("lx", "ly", "rx", "ry")):
        return "unsupported", ["No complete controller sample was captured"]
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


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RCM Tool")
        self.geometry("900x620")
        self.minsize(760, 520)
        self.backend: ControllerBackend = XInputGamepad()
        self.test_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.latest_result: Optional[TestResult] = None
        self.current_values = {axis: 0.0 for axis in ("lx", "ly", "rx", "ry")}
        self.value_labels: dict[str, ttk.Label] = {}
        self.metric_labels: dict[str, ttk.Label] = {}
        self._build_ui()
        self.after(100, self._refresh_live_values)

    def _build_ui(self) -> None:
        padding = {"padx": 12, "pady": 8}
        header = ttk.Frame(self)
        header.pack(fill="x", **padding)
        ttk.Label(header, text="RCM Tool", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="Neutral-stick certification prototype • capture only, no game input injection",
        ).pack(anchor="w")

        controls = ttk.LabelFrame(self, text="Certification test")
        controls.pack(fill="x", **padding)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(controls, text="Backend:").grid(row=0, column=0, sticky="w", **padding)
        ttk.Label(controls, text=self.backend.name).grid(row=0, column=1, sticky="w", **padding)
        ttk.Label(controls, text="Duration:").grid(row=0, column=2, sticky="w", **padding)
        ttk.Label(controls, text=f"{TEST_DURATION_SECONDS:g} seconds").grid(row=0, column=3, sticky="w", **padding)
        self.start_button = ttk.Button(controls, text="Start neutral test", command=self.start_test)
        self.start_button.grid(row=0, column=4, **padding)
        self.export_button = ttk.Button(controls, text="Export report", command=self.export_report, state="disabled")
        self.export_button.grid(row=0, column=5, **padding)
        ttk.Label(controls, textvariable=self.status_var).grid(row=1, column=0, columnspan=6, sticky="w", **padding)

        live = ttk.LabelFrame(self, text="Live normalized input")
        live.pack(fill="x", **padding)
        for index, axis in enumerate(("lx", "ly", "rx", "ry")):
            ttk.Label(live, text=axis.upper()).grid(row=0, column=index, **padding)
            label = ttk.Label(live, text="0.0000", width=10, anchor="center", font=("Consolas", 13))
            label.grid(row=1, column=index, **padding)
            self.value_labels[axis] = label

        results = ttk.LabelFrame(self, text="Latest test metrics")
        results.pack(fill="both", expand=True, **padding)
        columns = ("axis", "rms", "peak", "crossings", "active")
        self.tree = ttk.Treeview(results, columns=columns, show="headings", height=8)
        headings = {"axis": "Axis", "rms": "RMS", "peak": "Peak-to-peak", "crossings": "Threshold crossings", "active": "Active while stationary"}
        widths = {"axis": 80, "rms": 120, "peak": 140, "crossings": 170, "active": 180}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="center")
        self.tree.pack(fill="x", padx=8, pady=8)
        self.result_var = tk.StringVar(value="No test has been run.")
        ttk.Label(results, textvariable=self.result_var, wraplength=780).pack(anchor="w", padx=8, pady=8)

        footer = ttk.Label(self, text="Reports are screening evidence only; review false positives and controller-specific baselines.", foreground="#555555")
        footer.pack(fill="x", padx=12, pady=(0, 10))

    def _refresh_live_values(self) -> None:
        sample = self.backend.read()
        if sample:
            self.current_values.update(sample)
            for axis, label in self.value_labels.items():
                label.configure(text=f"{self.current_values[axis]:+.4f}")
        self.after(100, self._refresh_live_values)

    def start_test(self) -> None:
        if self.test_thread and self.test_thread.is_alive():
            return
        self.latest_result = None
        self.export_button.configure(state="disabled")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.status_var.set("Testing… keep both sticks untouched")
        self.test_thread = threading.Thread(target=self._run_test, daemon=True)
        self.test_thread.start()

    def _run_test(self) -> None:
        axes = {axis: [] for axis in ("lx", "ly", "rx", "ry")}
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        samples = 0
        while time.monotonic() - started < TEST_DURATION_SECONDS and not self.stop_event.is_set():
            sample = self.backend.read()
            if sample:
                for axis in axes:
                    axes[axis].append(sample[axis])
                samples += 1
            time.sleep(POLL_INTERVAL_SECONDS)
        elapsed = max(time.monotonic() - started, 0.001)
        result = TestResult(
            test_name="neutral-stick-10s",
            started_at_utc=started_at,
            duration_seconds=elapsed,
            sample_rate_hz=samples / elapsed,
            backend=self.backend.name,
            axes={axis: axis_metrics(values) for axis, values in axes.items()},
            classification="pending",
            review_reasons=[],
        )
        result.classification, result.review_reasons = classify(result)
        self.after(0, lambda: self._finish_test(result))

    def _finish_test(self, result: TestResult) -> None:
        self.latest_result = result
        self.start_button.configure(state="normal")
        self.export_button.configure(state="normal")
        for axis, metrics in result.axes.items():
            self.tree.insert("", "end", values=(
                axis.upper(),
                f"{metrics.rms:.5f}",
                f"{metrics.peak_to_peak:.5f}",
                metrics.deadzone_crossings,
                f"{metrics.nonzero_stationary_percent:.2f}%",
            ))
        reason_text = "; ".join(result.review_reasons) if result.review_reasons else "No neutral-input anomalies exceeded the initial review thresholds."
        self.result_var.set(f"Classification: {result.classification.upper()} — {reason_text}")
        self.status_var.set("Test complete")

    def export_report(self) -> None:
        if not self.latest_result:
            return
        destination = filedialog.asksaveasfilename(
            title="Export RCM Tool report",
            defaultextension=".json",
            filetypes=[("JSON report", "*.json")],
            initialfile="controller-integrity-report.json",
        )
        if not destination:
            return
        result_data = asdict(self.latest_result)
        report = {
            "reportVersion": REPORT_VERSION,
            "tool": "RCM Tool",
            "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "environment": {"os": platform.platform(), "python": platform.python_version()},
            "result": result_data,
        }
        report["sha256"] = sha256_payload(report)
        Path(destination).write_text(json.dumps(report, indent=2), encoding="utf-8")
        messagebox.showinfo("Report exported", f"Saved report to:\n{destination}\n\nSHA-256:\n{report['sha256']}")


def main() -> None:
    if os.name != "nt":
        print("Warning: this prototype's first backend is Windows XInput.")
    App().mainloop()


if __name__ == "__main__":
    main()
