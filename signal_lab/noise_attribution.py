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
            "high_frequency_energy_percent": 0.0,
        }
    mean = statistics.fmean(values)
    residuals = [value - mean for value in values]
    deltas = [after - before for before, after in zip(values, values[1:])]
    filtered = _low_pass(values, timestamps_s, DEFAULT_TAU_SECONDS)
    trend_residuals = [value - trend for value, trend in zip(values, filtered)]
    total_ac_rms = math.sqrt(statistics.fmean(value * value for value in residuals)) if residuals else 0.0
    high_frequency_rms = math.sqrt(
        statistics.fmean(value * value for value in trend_residuals)
    ) if trend_residuals else 0.0
    high_frequency_energy_percent = (
        min(100.0, max(0.0, 100.0 * (high_frequency_rms / total_ac_rms) ** 2))
        if total_ac_rms > 0 else 0.0
    )
    return {
        "samples": len(values),
        "mean": mean,
        "noise_rms": math.sqrt(statistics.fmean(value * value for value in residuals)),
        "peak_to_peak": max(values) - min(values),
        "unique_levels": len({round(value, 6) for value in values}),
        "adjacent_delta_rms": math.sqrt(statistics.fmean(value * value for value in deltas)) if deltas else 0.0,
        "slow_trend_residual_rms": math.sqrt(statistics.fmean(value * value for value in trend_residuals)) if trend_residuals else 0.0,
        "high_frequency_energy_percent": high_frequency_energy_percent,
    }


def analyze_noise_capture(
    *,
    timestamps_ns: list[int],
    samples: list[dict],
    raw_report_hex: list[str | None],
    capture_kind: str,
    source_label: str = "Raw HID",
    device_metadata: dict | None = None,
    stationary_excursion: float = 0.02,
    reference_rate_hz: float | None = None,
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
    axis_spans = {axis: float(metrics["peak_to_peak"]) for axis, metrics in axes.items()}
    stationary = bool(samples) and bool(axis_spans) and max(axis_spans.values()) <= max(0.0001, float(stationary_excursion))
    timestamp_pairs = max(0, len(samples) - 1)
    monotonic_percent = 100.0 * len(intervals) / timestamp_pairs if timestamp_pairs else 0.0
    raw_report_count = sum(bool(report) for report in raw_report_hex)
    duplicate_percent = 100.0 * duplicate_reports / max(1, raw_report_count - 1)
    raw_report_coverage_percent = 100.0 * raw_report_count / max(1, len(samples))
    reference_rate = float(reference_rate_hz or 0.0)
    rate_vs_reference_percent = (
        100.0 * ((len(samples) - 1) / duration_s) / reference_rate
        if reference_rate > 0 and duration_s > 0 else None
    )
    high_frequency_energy = {
        axis: float(metrics["high_frequency_energy_percent"])
        for axis, metrics in axes.items()
    }
    quality_score = round(
        0.35 * min(100.0, duration_s / 10.0 * 100.0)
        + 0.25 * min(100.0, len(samples) / 1000.0 * 100.0)
        + 0.20 * monotonic_percent
        + 0.20 * raw_report_coverage_percent,
        1,
    )
    if not samples:
        interpretation = "No Raw HID samples were captured; no noise or smoothing conclusion is valid."
    elif capture_kind == "neutral" and not stationary:
        interpretation = (
            "The neutral capture contained movement larger than the stationary threshold. "
            "Do not treat its RMS or high-frequency percentages as a stationary noise floor."
        )
    elif capture_kind == "neutral":
        interpretation = (
            "The sticks stayed within the stationary threshold. The reported RMS, duplicate percentage, "
            "and high-frequency energy share describe the host-observed Raw HID output while untouched."
        )
    else:
        interpretation = (
            "The movement capture separates the stored output into a 50 ms slow trend and a residual. "
            "The residual percentage describes captured high-frequency energy; it is not a probability of "
            "firmware filtering."
        )
    if rate_vs_reference_percent is not None and rate_vs_reference_percent < 90.0:
        interpretation += (
            f" The observed rate is {rate_vs_reference_percent:.1f}% of the configured "
            f"{reference_rate:g} Hz reference; check the selected backend and USB path before interpreting noise."
        )
    return {
        "evidence_class": "measured-host-observed-raw-hid",
        "capture_kind": capture_kind,
        "source": source_label,
        "device_metadata": device_metadata or {},
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(samples),
        "raw_hid_report_count": raw_report_count,
        "consecutive_duplicate_raw_reports": duplicate_reports,
        "duplicate_report_percent": duplicate_percent,
        "raw_report_coverage_percent": raw_report_coverage_percent,
        "timestamp_monotonic_percent": monotonic_percent,
        "duration_s": duration_s,
        "sample_rate_hz": (len(samples) - 1) / duration_s if duration_s > 0 else 0.0,
        "reference_rate_hz": reference_rate if reference_rate > 0 else None,
        "rate_vs_reference_percent": rate_vs_reference_percent,
        "interval_mean_s": statistics.fmean(intervals) if intervals else 0.0,
        "interval_stdev_s": statistics.pstdev(intervals) if len(intervals) > 1 else 0.0,
        "axes": axes,
        "high_frequency_energy_percent_by_axis": high_frequency_energy,
        "capture_quality": {
            "score_percent": quality_score,
            "label": "usable" if quality_score >= 80 else "review required",
            "basis": "duration, sample count, monotonic timestamps, and Raw HID report coverage; not firmware confidence",
        },
        "interpretation": interpretation,
        "stationary_check": {
            "is_stationary": stationary,
            "threshold_peak_to_peak": float(stationary_excursion),
            "axis_peak_to_peak": axis_spans,
        },
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
