"""Controller acquisition adapter over the original RCM Tool backends."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class ControllerMeasurement:
    timestamp_ns: int
    sample: dict
    source: str
    timing_quality: str
    raw_report_hex: str | None = None


class ControllerAcquisition:
    def __init__(self, callback: Callable[[ControllerMeasurement], None], poll_sleep_s: float = 0.001) -> None:
        self.callback = callback
        self.poll_sleep_s = max(0.00025, float(poll_sleep_s))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.backend = None
        self.error: str | None = None

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
            return
        last_host_sample_ns = 0
        while not self.stop_event.is_set():
            try:
                sample = self.backend.read()
                now = time.perf_counter_ns()
                active = getattr(self.backend, "active", None)
                if active is not None and active.__class__.__name__ == "HIDGamepad":
                    drain = getattr(active, "drain_raw_reports", None)
                    reports = drain() if callable(drain) else []
                    for report in reports:
                        self.callback(ControllerMeasurement(
                            timestamp_ns=now,
                            sample=dict(sample or {}),
                            source=getattr(active, "status", lambda: "Raw HID")(),
                            timing_quality="measured-at-host-read",
                            raw_report_hex=bytes(report).hex(),
                        ))
                elif sample is not None and now - last_host_sample_ns >= int(self.poll_sleep_s * 1e9):
                    last_host_sample_ns = now
                    self.callback(ControllerMeasurement(
                        timestamp_ns=now,
                        sample=dict(sample),
                        source=getattr(active, "name", "Host controller API") if active is not None else "Host controller API",
                        timing_quality="host-poll-estimate",
                    ))
            except Exception as exc:
                self.error = str(exc)
            time.sleep(self.poll_sleep_s)
