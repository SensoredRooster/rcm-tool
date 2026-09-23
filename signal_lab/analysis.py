"""Measurement math for Gamepad Signal Lab.

All functions operate on raw timestamps/measurements and make no assumptions about
advertised controller polling rates or instrument precision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from bisect import bisect_left
import math
import statistics
from typing import Iterable, Sequence


def _pct(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _rms(values: Iterable[float]) -> float:
    seq = [float(v) for v in values]
    return math.sqrt(statistics.fmean(v * v for v in seq)) if seq else 0.0


@dataclass(frozen=True)
class TimingMetrics:
    sample_count: int = 0
    duration_s: float = 0.0
    effective_rate_hz: float = 0.0
    mean_interval_ms: float = 0.0
    min_interval_ms: float = 0.0
    max_interval_ms: float = 0.0
    stdev_ms: float = 0.0
    rms_deviation_ms: float = 0.0
    peak_to_peak_jitter_ms: float = 0.0
    successive_interval_variation_ms: float = 0.0
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    p999_ms: float = 0.0
    late_reports: int = 0
    missing_reports_estimate: int = 0
    duplicate_intervals: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OscillatorMetrics:
    sample_count: int = 0
    mean_frequency_hz: float = 0.0
    min_frequency_hz: float = 0.0
    max_frequency_hz: float = 0.0
    frequency_stdev_hz: float = 0.0
    frequency_error_hz: float = 0.0
    frequency_error_ppm: float = 0.0
    mean_period_s: float = 0.0
    period_stdev_s: float = 0.0
    rms_period_jitter_s: float = 0.0
    peak_to_peak_period_jitter_s: float = 0.0
    cycle_to_cycle_rms_s: float = 0.0
    cycle_to_cycle_peak_s: float = 0.0
    duty_cycle_percent: float | None = None
    allan_deviation_tau1: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def timing_metrics(
    timestamps_ns: Sequence[int],
    *,
    expected_interval_ms: float | None = None,
    late_factor: float = 1.5,
) -> TimingMetrics:
    """Compute report timing metrics from monotonic nanosecond timestamps.

    expected_interval_ms is optional. When omitted the median interval is used
    for deviation/late/missing estimates. Raw timestamps remain the source of truth.
    """
    if len(timestamps_ns) < 2:
        return TimingMetrics(sample_count=len(timestamps_ns))
    intervals_ms = [
        (b - a) / 1_000_000.0
        for a, b in zip(timestamps_ns, timestamps_ns[1:])
        if b > a
    ]
    if not intervals_ms:
        return TimingMetrics(sample_count=len(timestamps_ns))
    duration_s = max(0.0, (timestamps_ns[-1] - timestamps_ns[0]) / 1_000_000_000.0)
    mean = statistics.fmean(intervals_ms)
    expected = float(expected_interval_ms) if expected_interval_ms and expected_interval_ms > 0 else _pct(intervals_ms, 50)
    deviations = [dt - expected for dt in intervals_ms]
    successive = [b - a for a, b in zip(intervals_ms, intervals_ms[1:])]
    late_limit = expected * max(1.0, late_factor)
    late = sum(dt > late_limit for dt in intervals_ms)
    missing = sum(max(0, round(dt / expected) - 1) for dt in intervals_ms) if expected > 0 else 0
    rounded = [round(v, 6) for v in intervals_ms]
    duplicates = max(0, len(rounded) - len(set(rounded)))
    return TimingMetrics(
        sample_count=len(timestamps_ns),
        duration_s=duration_s,
        effective_rate_hz=((len(timestamps_ns) - 1) / duration_s) if duration_s > 0 else 0.0,
        mean_interval_ms=mean,
        min_interval_ms=min(intervals_ms),
        max_interval_ms=max(intervals_ms),
        stdev_ms=statistics.pstdev(intervals_ms) if len(intervals_ms) > 1 else 0.0,
        rms_deviation_ms=_rms(deviations),
        peak_to_peak_jitter_ms=max(deviations) - min(deviations),
        successive_interval_variation_ms=_rms(successive),
        p50_ms=_pct(intervals_ms, 50),
        p90_ms=_pct(intervals_ms, 90),
        p95_ms=_pct(intervals_ms, 95),
        p99_ms=_pct(intervals_ms, 99),
        p999_ms=_pct(intervals_ms, 99.9),
        late_reports=late,
        missing_reports_estimate=missing,
        duplicate_intervals=duplicates,
    )


def allan_deviation(frequencies_hz: Sequence[float], nominal_frequency_hz: float) -> float | None:
    """Overlapping two-sample Allan deviation at one sample interval (tau = 1 sample)."""
    if nominal_frequency_hz <= 0 or len(frequencies_hz) < 3:
        return None
    y = [(float(f) - nominal_frequency_hz) / nominal_frequency_hz for f in frequencies_hz]
    diffs = [b - a for a, b in zip(y, y[1:])]
    return math.sqrt(0.5 * statistics.fmean(d * d for d in diffs)) if diffs else None


def oscillator_metrics(
    frequencies_hz: Sequence[float],
    nominal_frequency_hz: float,
    *,
    duty_cycles_percent: Sequence[float] | None = None,
) -> OscillatorMetrics:
    values = [float(v) for v in frequencies_hz if float(v) > 0]
    if not values:
        return OscillatorMetrics()
    mean_frequency = statistics.fmean(values)
    periods = [1.0 / v for v in values]
    mean_period = statistics.fmean(periods)
    period_deviation = [p - mean_period for p in periods]
    cycle = [b - a for a, b in zip(periods, periods[1:])]
    error_hz = mean_frequency - nominal_frequency_hz if nominal_frequency_hz > 0 else 0.0
    ppm = error_hz / nominal_frequency_hz * 1_000_000.0 if nominal_frequency_hz > 0 else 0.0
    duty = None
    if duty_cycles_percent:
        clean_duty = [float(x) for x in duty_cycles_percent]
        duty = statistics.fmean(clean_duty) if clean_duty else None
    return OscillatorMetrics(
        sample_count=len(values),
        mean_frequency_hz=mean_frequency,
        min_frequency_hz=min(values),
        max_frequency_hz=max(values),
        frequency_stdev_hz=statistics.pstdev(values) if len(values) > 1 else 0.0,
        frequency_error_hz=error_hz,
        frequency_error_ppm=ppm,
        mean_period_s=mean_period,
        period_stdev_s=statistics.pstdev(periods) if len(periods) > 1 else 0.0,
        rms_period_jitter_s=_rms(period_deviation),
        peak_to_peak_period_jitter_s=max(period_deviation) - min(period_deviation),
        cycle_to_cycle_rms_s=_rms(cycle),
        cycle_to_cycle_peak_s=max((abs(x) for x in cycle), default=0.0),
        duty_cycle_percent=duty,
        allan_deviation_tau1=allan_deviation(values, nominal_frequency_hz),
    )


def pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    x = [float(v) for v in xs[-n:]]
    y = [float(v) for v in ys[-n:]]
    mx, my = statistics.fmean(x), statistics.fmean(y)
    dx = [v - mx for v in x]
    dy = [v - my for v in y]
    sx = math.sqrt(sum(v * v for v in dx))
    sy = math.sqrt(sum(v * v for v in dy))
    if sx == 0 or sy == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / (sx * sy)


def align_nearest(
    left_times_ns: Sequence[int],
    left_values: Sequence[float],
    right_times_ns: Sequence[int],
    right_values: Sequence[float],
    *,
    max_delta_ns: int | None = None,
) -> tuple[list[float], list[float]]:
    """Align two sampled series by nearest timestamp.

    Each right-side sample is paired with the nearest left-side sample. A
    max_delta_ns window can reject pairs that are too far apart to be meaningful.
    """
    n_left = min(len(left_times_ns), len(left_values))
    n_right = min(len(right_times_ns), len(right_values))
    if n_left == 0 or n_right == 0:
        return [], []
    pairs = sorted(
        (int(t), float(v))
        for t, v in zip(left_times_ns[-n_left:], left_values[-n_left:])
    )
    times = [x[0] for x in pairs]
    values = [x[1] for x in pairs]
    out_left: list[float] = []
    out_right: list[float] = []
    for rt, rv in zip(right_times_ns[-n_right:], right_values[-n_right:]):
        rt = int(rt)
        idx = bisect_left(times, rt)
        candidates = []
        if idx < len(times):
            candidates.append(idx)
        if idx > 0:
            candidates.append(idx - 1)
        if not candidates:
            continue
        best = min(candidates, key=lambda i: abs(times[i] - rt))
        delta = abs(times[best] - rt)
        if max_delta_ns is not None and delta > max_delta_ns:
            continue
        out_left.append(values[best])
        out_right.append(float(rv))
    return out_left, out_right
