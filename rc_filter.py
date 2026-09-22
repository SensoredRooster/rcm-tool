"""Frame-rate independent first-order digital RC low-pass filter.

alpha = 1 - exp(-dt / tau)
smoothed[n] = alpha * raw[n] + (1 - alpha) * smoothed[n-1]

Default tau is 0.05 s (cutoff ≈ 3.18 Hz). High-frequency jitter RMS is the
RMS of (raw - filtered) over a capture.
"""

from __future__ import annotations

import math
import statistics
from typing import Iterable

DEFAULT_TAU_SECONDS = 0.05
STICK_AXES = ("lx", "ly", "rx", "ry")


class RCLowPassFilter:
    def __init__(self, tau_seconds: float = DEFAULT_TAU_SECONDS) -> None:
        if tau_seconds <= 0.0:
            raise ValueError("tau_seconds must be greater than zero")
        self.tau_seconds = float(tau_seconds)
        self._state: dict[str, float] = {}

    def reset(self) -> None:
        self._state.clear()

    @staticmethod
    def alpha(dt: float, tau_seconds: float) -> float:
        if dt <= 0.0:
            return 0.0
        return 1.0 - math.exp(-dt / tau_seconds)

    def update_axis(self, name: str, raw: float, dt: float) -> float:
        value = float(raw)
        if name not in self._state:
            self._state[name] = value
            return value
        a = self.alpha(dt, self.tau_seconds)
        self._state[name] = a * value + (1.0 - a) * self._state[name]
        return self._state[name]

    def update(
        self,
        raw: dict[str, float],
        dt: float,
        axes: Iterable[str] = STICK_AXES,
    ) -> dict[str, float]:
        return {axis: self.update_axis(axis, raw[axis], dt) for axis in axes}


def rc_filter_series(
    values: list[float],
    timestamps_s: list[float],
    tau_seconds: float = DEFAULT_TAU_SECONDS,
) -> list[float]:
    if not values:
        return []
    if len(values) != len(timestamps_s):
        raise ValueError("values and timestamps_s must be the same length")
    filt = RCLowPassFilter(tau_seconds)
    filtered = [filt.update_axis("axis", values[0], 0.0)]
    for value, previous_t, current_t in zip(values[1:], timestamps_s, timestamps_s[1:]):
        filtered.append(filt.update_axis("axis", value, current_t - previous_t))
    return filtered


def residual_rms(raw_values: list[float], filtered_values: list[float]) -> float:
    residuals = [raw - trend for raw, trend in zip(raw_values, filtered_values)]
    if not residuals:
        return 0.0
    return math.sqrt(statistics.fmean(x * x for x in residuals))


def build_rc_filter_metrics(
    samples: list[dict[str, float]],
    tau_seconds: float = DEFAULT_TAU_SECONDS,
) -> dict[str, float]:
    metrics: dict[str, float] = {
        "tau_seconds": tau_seconds,
        "cutoff_hz": 1.0 / (2.0 * math.pi * tau_seconds) if tau_seconds > 0 else 0.0,
    }
    if not samples:
        for axis in STICK_AXES:
            metrics[f"{axis}_high_freq_rms"] = 0.0
            metrics[f"{axis}_high_freq_peak_to_peak"] = 0.0
        return metrics
    timestamps_s = [float(sample["t_ms"]) / 1000.0 for sample in samples]
    for axis in STICK_AXES:
        raw_values = [float(sample[axis]) for sample in samples]
        filtered_values = rc_filter_series(raw_values, timestamps_s, tau_seconds)
        residuals = [raw - trend for raw, trend in zip(raw_values, filtered_values)]
        metrics[f"{axis}_high_freq_rms"] = residual_rms(raw_values, filtered_values)
        metrics[f"{axis}_high_freq_peak_to_peak"] = (
            max(residuals) - min(residuals) if residuals else 0.0
        )
    return metrics
