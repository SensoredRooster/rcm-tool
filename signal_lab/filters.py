"""Standard stick-smoothing filters, simulated on a copy of recorded data.

Controller firmware and games do not publish their filters. These are the
published algorithms such filters are built from:

- exponential: first-order low-pass, y += a * (x - y), a = 1 - e^(-dt/tau).
  What "smoothing: N ms" usually means.
- moving_average: the average of all readings in the last N ms.
- one_euro: Casiez, Roussel & Vogel (CHI 2012). Smooths strongly at rest and
  lightly during fast motion: cutoff = min_cutoff + beta * |speed|.

Each is applied to the recorded samples at their real timestamps. Nothing is
sent to the controller or changed in the recording.
"""
from __future__ import annotations

from collections import deque
import math
import statistics
from typing import Callable, Sequence

Filter = Callable[[Sequence[float], Sequence[float]], list[float]]


def exponential(timestamps_s: Sequence[float], values: Sequence[float], tau_s: float) -> list[float]:
    if not values:
        return []
    out = [float(values[0])]
    for index in range(1, len(values)):
        dt = max(0.0, timestamps_s[index] - timestamps_s[index - 1])
        a = 1.0 - math.exp(-dt / tau_s)
        out.append(out[-1] + a * (float(values[index]) - out[-1]))
    return out


def moving_average(timestamps_s: Sequence[float], values: Sequence[float], window_s: float) -> list[float]:
    out: list[float] = []
    window: deque[tuple[float, float]] = deque()
    total = 0.0
    for t, value in zip(timestamps_s, values):
        window.append((t, float(value)))
        total += float(value)
        while window and window[0][0] <= t - window_s:
            total -= window.popleft()[1]
        out.append(total / len(window))
    return out


def one_euro(
    timestamps_s: Sequence[float],
    values: Sequence[float],
    min_cutoff_hz: float,
    beta: float,
    d_cutoff_hz: float = 1.0,
) -> list[float]:
    if not values:
        return []

    def factor(dt: float, cutoff: float) -> float:
        r = 2.0 * math.pi * cutoff * dt
        return r / (r + 1.0)

    x_prev = float(values[0])
    dx_prev = 0.0
    out = [x_prev]
    for index in range(1, len(values)):
        dt = timestamps_s[index] - timestamps_s[index - 1]
        if dt <= 0:
            out.append(x_prev)
            continue
        dx = (float(values[index]) - x_prev) / dt
        dx_prev = dx_prev + factor(dt, d_cutoff_hz) * (dx - dx_prev)
        cutoff = min_cutoff_hz + beta * abs(dx_prev)
        x_prev = x_prev + factor(dt, cutoff) * (float(values[index]) - x_prev)
        out.append(x_prev)
    return out


# (family, setting shown to users, function)
PRESETS: tuple[tuple[str, str, Filter], ...] = (
    ("Exponential", "10 ms", lambda t, v: exponential(t, v, 0.010)),
    ("Exponential", "25 ms", lambda t, v: exponential(t, v, 0.025)),
    ("Exponential", "50 ms", lambda t, v: exponential(t, v, 0.050)),
    ("Moving average", "10 ms", lambda t, v: moving_average(t, v, 0.010)),
    ("Moving average", "25 ms", lambda t, v: moving_average(t, v, 0.025)),
    ("Moving average", "50 ms", lambda t, v: moving_average(t, v, 0.050)),
    ("One Euro", "light (3 Hz, beta 1.0)", lambda t, v: one_euro(t, v, 3.0, 1.0)),
    ("One Euro", "medium (1 Hz, beta 0.5)", lambda t, v: one_euro(t, v, 1.0, 0.5)),
    ("One Euro", "strong (0.5 Hz, beta 0.2)", lambda t, v: one_euro(t, v, 0.5, 0.2)),
)


def _ac_rms(values: Sequence[float]) -> float:
    mean = statistics.fmean(values)
    return math.sqrt(statistics.fmean((value - mean) ** 2 for value in values))


def step_delays_ms(apply: Filter, rate_hz: float) -> tuple[float | None, float | None]:
    """Time for a full stick flick (0 to 1) to reach 50% and 90% through the filter."""
    dt = 1.0 / max(1.0, rate_hz)
    count = int(1.0 / dt) + 1
    times = [index * dt for index in range(count)]
    step_at = 0.2
    output = apply(times, [1.0 if t >= step_at else 0.0 for t in times])

    def reached(level: float) -> float | None:
        for t, value in zip(times, output):
            if t >= step_at and value >= level - 1e-9:
                return round((t - step_at) * 1000.0, 2)
        return None

    return reached(0.5), reached(0.9)


def simulate_filters(timestamps_ns: Sequence[int], samples: Sequence[dict]) -> dict:
    """Noise cut and flick delay for each preset, on an untouched-stick recording.

    noise_cut_percent: median over the axes that had noise of
    (1 - RMS of filtered variation / RMS of recorded variation) x 100.
    Delays come from a full 0-to-1 step sampled at the recording's report rate.
    """
    if len(samples) < 100 or len(timestamps_ns) != len(samples):
        return {"available": False, "reason": "needs at least 100 untouched-stick reports"}
    times = [(int(value) - int(timestamps_ns[0])) / 1e9 for value in timestamps_ns]
    duration = times[-1] - times[0]
    rate_hz = (len(times) - 1) / duration if duration > 0 else 0.0
    axes = {}
    for axis in ("lx", "ly", "rx", "ry"):
        values = [float(sample.get(axis, 0.0)) for sample in samples]
        noise = _ac_rms(values)
        if noise > 1e-9:
            axes[axis] = (values, noise)
    rows = []
    for family, setting, apply in PRESETS:
        cuts = [
            100.0 * (1.0 - _ac_rms(apply(times, values)) / noise)
            for values, noise in axes.values()
        ]
        half, ninety = step_delays_ms(apply, rate_hz)
        rows.append({
            "filter": family,
            "setting": setting,
            "noise_cut_percent": round(statistics.median(cuts), 1) if cuts else None,
            "delay_to_50_percent_ms": half,
            "delay_to_90_percent_ms": ninety,
        })
    return {
        "available": True,
        "report_rate_hz": round(rate_hz, 1),
        "axes_with_noise": sorted(axes),
        "filters": rows,
        "note": "Calculated on a copy of the recording, not measured on the controller.",
    }
