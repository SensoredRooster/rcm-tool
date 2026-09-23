"""Timing and quantization metrics on the captured stream.

These describe what the PC actually received. They do not write to a pad
and they are not a cheat verdict.
"""

from __future__ import annotations

import math
import statistics

STICK_AXES = ("lx", "ly", "rx", "ry")
RATE_DELTA_HZ = 8.0
DT_STD_DELTA = 0.002
UNIQUE_RATIO = 1.35


def _dts(samples: list[dict]) -> list[float]:
    times = [float(sample.get("t_ms", 0.0)) / 1000.0 for sample in samples]
    return [later - earlier for earlier, later in zip(times, times[1:]) if later > earlier]


def _unique_levels(samples: list[dict], axis: str) -> int:
    values = {round(float(sample.get(axis, 0.0)), 5) for sample in samples}
    return len(values)


def build_hid_signal_metrics(
    samples: list[dict] | None,
    hid_reports: list | None = None,
) -> dict:
    samples = samples or []
    hid_reports = hid_reports or []
    dts = _dts(samples)
    duration_s = 0.0
    if len(samples) >= 2:
        duration_s = max(
            0.0,
            float(samples[-1].get("t_ms", 0.0) - samples[0].get("t_ms", 0.0)) / 1000.0,
        )
    sample_rate_hz = (len(samples) - 1) / duration_s if duration_s > 0 else 0.0
    axes = {}
    for axis in STICK_AXES:
        raw = [float(sample.get(axis, 0.0)) for sample in samples]
        axes[axis] = {
            "unique_levels": _unique_levels(samples, axis),
            "mean": statistics.fmean(raw) if raw else 0.0,
            "stdev": statistics.pstdev(raw) if len(raw) > 1 else 0.0,
            "peak_abs": max((abs(v) for v in raw), default=0.0),
        }
    return {
        "sample_count": len(samples),
        "hid_report_count": len(hid_reports),
        "duration_s": round(duration_s, 4),
        "sample_rate_hz": round(sample_rate_hz, 3),
        "dt_mean_s": round(statistics.fmean(dts), 6) if dts else 0.0,
        "dt_std_s": round(statistics.pstdev(dts), 6) if len(dts) > 1 else 0.0,
        "axes": axes,
        "note": "Computed from the captured stream after read(). Not written to the controller.",
    }


def compare_hid_signal(before: dict | None, after: dict | None) -> dict:
    before = before or {}
    after = after or {}
    rate_delta = float(after.get("sample_rate_hz", 0.0) or 0.0) - float(
        before.get("sample_rate_hz", 0.0) or 0.0
    )
    dt_std_delta = float(after.get("dt_std_s", 0.0) or 0.0) - float(
        before.get("dt_std_s", 0.0) or 0.0
    )
    unique = {}
    unique_changed = False
    for axis in STICK_AXES:
        b = int((before.get("axes") or {}).get(axis, {}).get("unique_levels", 0) or 0)
        a = int((after.get("axes") or {}).get(axis, {}).get("unique_levels", 0) or 0)
        unique[axis] = {"before": b, "after": a, "delta": a - b}
        if b > 0 and a >= max(8, math.ceil(b * UNIQUE_RATIO)):
            unique_changed = True
    rate_changed = abs(rate_delta) >= RATE_DELTA_HZ
    timing_changed = dt_std_delta >= DT_STD_DELTA
    reasons = []
    if rate_changed:
        reasons.append(f"sample rate changed by {rate_delta:+.1f} Hz")
    if timing_changed:
        reasons.append(f"inter-sample jitter rose by {dt_std_delta:.5f} s")
    if unique_changed:
        reasons.append("After used more distinct axis levels than Before")
    if not reasons:
        reasons.append("HID timing and quantization stayed in band")
    return {
        "rate_delta_hz": rate_delta,
        "dt_std_delta_s": dt_std_delta,
        "rate_changed": rate_changed,
        "timing_changed": timing_changed,
        "quantization_changed": unique_changed,
        "unique_levels": unique,
        "reasons": reasons,
        "not_a_verdict": "Stream change only. Idle boxes can look identical at rest.",
    }
