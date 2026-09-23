"""Background oscillator/frequency acquisition for physical SCPI instruments."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

from .instruments import InstrumentAdapter


@dataclass(frozen=True)
class OscillatorMeasurement:
    timestamp_ns: int
    frequency_hz: float
    source: str
    duty_cycle_percent: float | None = None


class OscillatorAcquisition:
    def __init__(
        self,
        instrument: InstrumentAdapter,
        callback: Callable[[OscillatorMeasurement], None],
        sample_period_s: float = 0.10,
    ) -> None:
        self.instrument = instrument
        self.callback = callback
        self.sample_period_s = max(0.02, float(sample_period_s))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: str | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run, daemon=True, name="oscillator-acquisition"
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=max(1.0, self.sample_period_s * 3))

    def _run(self) -> None:
        source = self.instrument.identify()
        while not self.stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                frequency = self.instrument.measure_frequency_hz()
                if frequency is not None and frequency > 0:
                    duty = self.instrument.measure_duty_cycle_percent()
                    self.callback(OscillatorMeasurement(
                        timestamp_ns=time.perf_counter_ns(),
                        frequency_hz=float(frequency),
                        source=source,
                        duty_cycle_percent=duty,
                    ))
                    self.error = None
            except Exception as exc:
                self.error = str(exc)
            remaining = self.sample_period_s - (time.monotonic() - cycle_started)
            if remaining > 0:
                self.stop_event.wait(remaining)
