"""Hardware-only noise evidence metrics for Raw HID captures.

These metrics describe the signal received by the host. They intentionally do
not classify a result as firmware smoothing: that attribution requires a
physical trace upstream of the controller's USB report.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import statistics


AXES = ("lx", "ly", "rx", "ry")
DEFAULT_TAU_SECONDS = 0.05


def _low_pass(values: list[float], timestamps_s: list[float], tau_seconds: float) -> list[float]:
    if not values:
        return []
    output = [float(values[0])]
    for value, previous_t, current_t in zip(values[1:], timestamps_s, timestamps_s[1:]):
        dt = max(0.0, current_t - previous_t)
        alpha = 1.0 - math.exp(-dt / tau_seconds) if dt else 0.0
        output.append(alpha * float(value) + (1.0 - alpha) * output[-1])
    return output


def _axis_metrics(values: list[float], timestamps_s: list[float]) -> dict[str, float | int]:
    if not values:
        return {
            "samples": 0,
            "mean": 0.0,
            "noise_rms": 0.0,
            "peak_to_peak": 0.0,
            "unique_levels": 0,
            "adjacent_delta_rms": 0.0,
            "slow_trend_residual_rms": 0.0,
        }
    mean = statistics.fmean(values)
    residuals = [value - mean for value in values]
    deltas = [after - before for before, after in zip(values, values[1:])]
    filtered = _low_pass(values, timestamps_s, DEFAULT_TAU_SECONDS)
    trend_residuals = [value - trend for value, trend in zip(values, filtered)]
    return {
        "samples": len(values),
        "mean": mean,
        "noise_rms": math.sqrt(statistics.fmean(value * value for value in residuals)),
        "peak_to_peak": max(values) - min(values),
        "unique_levels": len({round(value, 6) for value in values}),
        "adjacent_delta_rms": math.sqrt(statistics.fmean(value * value for value in deltas)) if deltas else 0.0,
        "slow_trend_residual_rms": math.sqrt(statistics.fmean(value * value for value in trend_residuals)) if trend_residuals else 0.0,
    }


def analyze_noise_capture(
    *,
    timestamps_ns: list[int],
    samples: list[dict],
    raw_report_hex: list[str | None],
    capture_kind: str,
    source_label: str = "Raw HID",
    device_metadata: dict | None = None,
) -> dict:
    """Analyze a real Raw HID window without claiming firmware attribution."""
    if len(timestamps_ns) != len(samples):
        raise ValueError("timestamps_ns and samples must have the same length")
    if len(raw_report_hex) != len(samples):
        raise ValueError("raw_report_hex and samples must have the same length")
    if any(not math.isfinite(float(timestamp)) for timestamp in timestamps_ns):
        raise ValueError("timestamps_ns must contain finite values")
    timestamps_s = [timestamp / 1_000_000_000.0 for timestamp in timestamps_ns]
    duration_s = (timestamps_s[-1] - timestamps_s[0]) if len(timestamps_s) >= 2 else 0.0
    intervals = [after - before for before, after in zip(timestamps_s, timestamps_s[1:]) if after > before]
    duplicate_reports = sum(
        1
        for before, after in zip(raw_report_hex, raw_report_hex[1:])
        if before and after and before == after
    )
    axes = {
        axis: _axis_metrics([float(sample.get(axis, 0.0)) for sample in samples], timestamps_s)
        for axis in AXES
    }
    return {
        "evidence_class": "measured-host-observed-raw-hid",
        "capture_kind": capture_kind,
        "source": source_label,
        "device_metadata": device_metadata or {},
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(samples),
        "raw_hid_report_count": sum(bool(report) for report in raw_report_hex),
        "consecutive_duplicate_raw_reports": duplicate_reports,
        "duration_s": duration_s,
        "sample_rate_hz": (len(samples) - 1) / duration_s if duration_s > 0 else 0.0,
        "interval_mean_s": statistics.fmean(intervals) if intervals else 0.0,
        "interval_stdev_s": statistics.pstdev(intervals) if len(intervals) > 1 else 0.0,
        "axes": axes,
        "records": [
            {
                "timestamp_ns": timestamp,
                "sample": sample,
                "raw_report_hex": raw_report,
            }
            for timestamp, sample, raw_report in zip(timestamps_ns, samples, raw_report_hex)
        ],
        "display_smoothing": "disabled-for-test; metrics use stored acquisition samples",
        "attribution": "undetermined-from-raw-hid-alone",
        "limitation": (
            "Raw HID is measured after the controller firmware and USB stack. "
            "A smoothing signature in this stream cannot by itself distinguish firmware filtering "
            "from sensor, hardware, or transport behavior. Compare against an oscilloscope or "
            "logic-analyzer trace upstream of the controller USB report for attribution."
        ),
    }
