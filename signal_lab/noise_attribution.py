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


def _axis_metrics(values: list[float], timestamps_s: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "samples": 0,
            "mean": None,
            "noise_rms": None,
            "peak_to_peak": None,
            "unique_levels": 0,
            "adjacent_delta_rms": None,
            "slow_trend_residual_rms": None,
            "high_frequency_energy_percent": None,
            "variation_rms_after_smoothing": None,
            "variation_change_percent": None,
            "smoothing_delta_rms": None,
            "peak_to_peak_after_smoothing": None,
        }
    if len(values) < 2:
        return {
            "samples": len(values),
            "mean": float(values[0]),
            "noise_rms": None,
            "peak_to_peak": None,
            "unique_levels": len({round(value, 6) for value in values}),
            "adjacent_delta_rms": None,
            "slow_trend_residual_rms": None,
            "high_frequency_energy_percent": None,
            "variation_rms_after_smoothing": None,
            "variation_change_percent": None,
            "smoothing_delta_rms": None,
            "peak_to_peak_after_smoothing": None,
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
    smoothing_tau_seconds: float = DEFAULT_TAU_SECONDS,
) -> dict:
    """Analyze a real Raw HID window without claiming firmware attribution."""
    if len(timestamps_ns) != len(samples):
        raise ValueError("timestamps_ns and samples must have the same length")
    if len(raw_report_hex) != len(samples):
        raise ValueError("raw_report_hex and samples must have the same length")
    if any(not math.isfinite(float(timestamp)) for timestamp in timestamps_ns):
        raise ValueError("timestamps_ns must contain finite values")
    if not math.isfinite(float(smoothing_tau_seconds)) or not 0.001 <= smoothing_tau_seconds <= 1.0:
        raise ValueError("smoothing_tau_seconds must be between 0.001 and 1 second")
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
    for axis in AXES:
        values = [float(sample.get(axis, 0.0)) for sample in samples]
        if len(values) < 2:
            continue
        filtered = _low_pass(values, timestamps_s, smoothing_tau_seconds)
        raw_rms = axes[axis]["noise_rms"]
        filtered_mean = statistics.fmean(filtered)
        filtered_ac = [value - filtered_mean for value in filtered]
        filtered_rms = math.sqrt(statistics.fmean(value * value for value in filtered_ac))
        axes[axis]["variation_rms_after_smoothing"] = filtered_rms
        axes[axis]["variation_change_percent"] = (
            100.0 * (float(raw_rms) - filtered_rms) / float(raw_rms)
            if isinstance(raw_rms, (int, float)) and raw_rms > 0 else None
        )
        axes[axis]["smoothing_delta_rms"] = math.sqrt(
            statistics.fmean((raw - smooth) ** 2 for raw, smooth in zip(values, filtered))
        )
        axes[axis]["peak_to_peak_after_smoothing"] = max(filtered) - min(filtered)
    axis_spans = {
        axis: float(metrics["peak_to_peak"])
        for axis, metrics in axes.items()
        if metrics["peak_to_peak"] is not None
    }
    stationary = (
        max(axis_spans.values()) <= max(0.0001, float(stationary_excursion))
        if len(samples) >= 100 and len(axis_spans) == len(AXES)
        else None
    )
    timestamp_pairs = max(0, len(samples) - 1)
    monotonic_percent = 100.0 * len(intervals) / timestamp_pairs if timestamp_pairs else 0.0
    raw_report_count = sum(bool(report) for report in raw_report_hex)
    duplicate_percent = (
        100.0 * duplicate_reports / (raw_report_count - 1)
        if raw_report_count >= 2 else None
    )
    raw_report_coverage_percent = 100.0 * raw_report_count / max(1, len(samples))
    reference_rate = float(reference_rate_hz or 0.0)
    rate_vs_reference_percent = (
        100.0 * ((len(samples) - 1) / duration_s) / reference_rate
        if reference_rate > 0 and duration_s > 0 else None
    )
    high_frequency_energy = {
        axis: (
            float(metrics["high_frequency_energy_percent"])
            if metrics["high_frequency_energy_percent"] is not None else None
        )
        for axis, metrics in axes.items()
    }
    target_duration_s = {"neutral": 10.0, "movement": 20.0}.get(capture_kind)
    quality_checks = []
    if target_duration_s is not None:
        minimum_duration_s = target_duration_s * 0.9
        quality_checks.append({
            "name": "capture duration",
            "observed_s": duration_s,
            "required_s": minimum_duration_s,
            "status": "pass" if duration_s >= minimum_duration_s else "review",
        })
    quality_checks.extend([
        {
            "name": "sample count",
            "observed": len(samples),
            "required": 100,
            "status": "pass" if len(samples) >= 100 else "review",
        },
        {
            "name": "monotonic timestamps",
            "observed_percent": monotonic_percent,
            "required_percent": 100.0,
            "status": "pass" if monotonic_percent == 100.0 else "review",
        },
        {
            "name": "Raw HID report-byte coverage",
            "observed_percent": raw_report_coverage_percent,
            "required_percent": 99.0,
            "status": "pass" if raw_report_coverage_percent >= 99.0 else "review",
        },
    ])
    quality_label = (
        "checks passed" if all(check["status"] == "pass" for check in quality_checks)
        else "review required"
    )
    if capture_kind == "neutral" and stationary is True:
        smoothing_basis = "stationary-noise-RMS"
        smoothing_note = (
            "For this stationary capture, the comparison estimates how the selected software low-pass would reduce "
            "variation in a duplicate of the recorded Raw HID stream. It does not change the controller or game input."
        )
    elif capture_kind == "neutral" and stationary is False:
        smoothing_basis = "variation-includes-unwanted-movement"
        smoothing_note = (
            "The neutral capture failed its stationarity check, so the raw-to-filtered change includes stick movement; "
            "it is not labeled as noise reduction."
        )
    elif capture_kind == "neutral":
        smoothing_basis = "stationarity-unavailable"
        smoothing_note = (
            "The neutral capture did not meet the sample-count minimum for a stationarity decision, so the "
            "raw-to-filtered change is not labeled as noise reduction."
        )
    else:
        smoothing_basis = "movement-includes-intended-input"
        smoothing_note = (
            "During movement, raw-to-filtered change includes intended stick motion and the smoother's response lag; "
            "it is a tradeoff measurement, not a noise-reduction percentage."
        )
    if len(samples) < 100:
        interpretation = (
            f"Only {len(samples)} Raw HID sample(s) were captured, below the 100-sample screening minimum. "
            "Variation metrics are unavailable or preliminary; no noise-floor or smoothing conclusion is valid."
        )
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
        "smoothing_comparison": {
            "method": "first-order exponential low-pass applied offline to a duplicate of the captured samples",
            "time_constant_s": float(smoothing_tau_seconds),
            "time_constant_ms": float(smoothing_tau_seconds) * 1000.0,
            "basis": smoothing_basis,
            "note": smoothing_note,
            "does_not_modify_controller_or_game_input": True,
        },
        "high_frequency_energy_percent_by_axis": high_frequency_energy,
        "capture_quality": {
            "label": quality_label,
            "checks": quality_checks,
            "basis": "Each listed capture-integrity check is evaluated independently; there is no weighted score or firmware-confidence percentage.",
        },
        "high_frequency_energy_method": {
            "filter": "first-order exponential slow trend",
            "time_constant_s": DEFAULT_TAU_SECONDS,
            "energy_share": "100 * (RMS(signal - slow_trend) / RMS(signal - mean))^2",
            "meaning": "descriptive residual-energy share; not a firmware-filtering percentage",
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
        "display_smoothing": "offline comparison only; original Raw HID samples remain unchanged",
        "attribution": "undetermined-from-raw-hid-alone",
        "limitation": (
            "Raw HID is measured after the controller firmware and USB stack. "
            "A smoothing signature in this stream cannot by itself distinguish firmware filtering "
            "from sensor, hardware, or transport behavior. Compare against an oscilloscope or "
            "logic-analyzer trace upstream of the controller USB report for attribution."
        ),
    }
