"""Apply RC-filter wiring to controller_integrity.py. Safe to re-run."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "controller_integrity.py"

IMPORT_BLOCK = """from typing import Optional, Protocol

from rc_filter import (
    DEFAULT_TAU_SECONDS as RC_FILTER_TAU_SECONDS,
    RCLowPassFilter,
    STICK_AXES,
    build_rc_filter_metrics,
    rc_filter_series,
    residual_rms,
)
"""

OLD_IMPORT = "from typing import Optional, Protocol\n"

OLD_VERSION = "REPORT_VERSION = \"0.1\"\n"
NEW_VERSION = "REPORT_VERSION = \"0.2\"\n"

OLD_RESULT_END = "    protocol: str = \"neutral-stick\"\n"
NEW_RESULT_END = """    protocol: str = \"neutral-stick\"
    rc_filter_metrics: dict[str, float] = field(default_factory=dict)
"""

OLD_AXIS_DEF = "def axis_metrics(values: list[float], threshold: float = 0.02) -> AxisMetrics:\n"
NEW_AXIS_DEF = """def axis_metrics(
    values: list[float],
    threshold: float = 0.02,
    timestamps_s: Optional[list[float]] = None,
    tau_seconds: float = RC_FILTER_TAU_SECONDS,
) -> AxisMetrics:
"""

OLD_EMA = """        # Estimate high-frequency jitter as the residual from a slow EMA.
        # This is a measurement heuristic, not a claim about the controller's firmware filter.
        smooth = values[0]
        residuals: list[float] = []
        velocities: list[float] = []
        for previous, current in zip(values, values[1:]):
            smooth = 0.12 * current + 0.88 * smooth
            residuals.append(current - smooth)
            velocities.append(current - previous)
        jitter_rms = math.sqrt(statistics.fmean([x * x for x in residuals])) if residuals else 0.0
        jitter_peak_to_peak = max(residuals) - min(residuals) if residuals else 0.0
        velocity_rms = math.sqrt(statistics.fmean([x * x for x in velocities])) if velocities else 0.0
"""

NEW_EMA = """        # High-frequency jitter is the residual after subtracting the RC slow trend.
        # This is a measurement of the captured stream, not a claim about firmware.
        if timestamps_s is None:
            timestamps_s = [index * POLL_INTERVAL_SECONDS for index in range(len(values))]
        filtered = rc_filter_series(values, timestamps_s, tau_seconds)
        residuals = [raw - trend for raw, trend in zip(values, filtered)]
        velocities = [current - previous for previous, current in zip(values, values[1:])]
        jitter_rms = residual_rms(values, filtered)
        jitter_peak_to_peak = max(residuals) - min(residuals) if residuals else 0.0
        velocity_rms = math.sqrt(statistics.fmean([x * x for x in velocities])) if velocities else 0.0
"""

OLD_CAPTURE = """        samples = 0
        while time.monotonic() - started < self.current_duration and not self.stop_event.is_set():
            sample = self.backend.read()
            if sample:
                for axis in axes:
                    axes[axis].append(sample[axis])
                captured_samples.append({
                    \"t_ms\": round((time.monotonic() - started) * 1000.0, 3),
                    **{axis: round(float(sample[axis]), 6) for axis in axes},
                })
                samples += 1
"""

NEW_CAPTURE = """        samples = 0
        trend_filter = RCLowPassFilter(RC_FILTER_TAU_SECONDS)
        last_sample_time: Optional[float] = None
        while time.monotonic() - started < self.current_duration and not self.stop_event.is_set():
            sample = self.backend.read()
            if sample:
                now = time.monotonic()
                dt = 0.0 if last_sample_time is None else now - last_sample_time
                last_sample_time = now
                filtered = trend_filter.update(sample, dt)
                for axis in axes:
                    axes[axis].append(sample[axis])
                captured_samples.append({
                    \"t_ms\": round((now - started) * 1000.0, 3),
                    **{axis: round(float(sample[axis]), 6) for axis in axes},
                    **{f\"{axis}_filtered\": round(float(filtered[axis]), 6) for axis in axes},
                })
                samples += 1
"""

OLD_RESULT = """            axes={axis: axis_metrics(values) for axis, values in axes.items()},
            classification=\"pending\",
            review_reasons=[],
            source_status=source_status,
            samples=captured_samples,
            hid_reports=captured_hid_reports,
            phase=self.current_test_phase,
            protocol=self.current_protocol,
        )
"""

NEW_RESULT = """            axes={
                axis: axis_metrics(
                    values,
                    timestamps_s=[sample[\"t_ms\"] / 1000.0 for sample in captured_samples],
                )
                for axis, values in axes.items()
            },
            classification=\"pending\",
            review_reasons=[],
            source_status=source_status,
            samples=captured_samples,
            hid_reports=captured_hid_reports,
            phase=self.current_test_phase,
            protocol=self.current_protocol,
            rc_filter_metrics=build_rc_filter_metrics(captured_samples, RC_FILTER_TAU_SECONDS),
        )
"""

OLD_PROTO = '''                \"jitterEstimator\": \"high-frequency residual from slow EMA (alpha=0.12)\",
            },
            \"result\": result_data,
'''

NEW_PROTO = '''                \"jitterEstimator\": \"high-frequency residual after first-order RC low-pass (tau=0.05s)\",
                \"rcFilterTauSeconds\": RC_FILTER_TAU_SECONDS,
            },
            \"rc_filter_metrics\": result.rc_filter_metrics,
            \"result\": result_data,
'''

OLD_DELTA = '''                \"jitterRmsDelta\": after_metrics.jitter_rms - before_metrics.jitter_rms,
                \"jitterPeakToPeakDelta\": after_metrics.jitter_peak_to_peak - before_metrics.jitter_peak_to_peak,
'''

NEW_DELTA = '''                \"jitterRmsDelta\": after_metrics.jitter_rms - before_metrics.jitter_rms,
                \"highFreqRmsDelta\": after.rc_filter_metrics.get(f\"{axis}_high_freq_rms\", 0.0)
                - before.rc_filter_metrics.get(f\"{axis}_high_freq_rms\", 0.0),
                \"jitterPeakToPeakDelta\": after_metrics.jitter_peak_to_peak - before_metrics.jitter_peak_to_peak,
'''

OLD_CMP_PROTO = '''                \"jitterEstimator\": \"high-frequency residual from slow EMA (alpha=0.12)\",
            },
            \"beforeReport\": before_path.name if before_path else None,
'''

NEW_CMP_PROTO = '''                \"jitterEstimator\": \"high-frequency residual after first-order RC low-pass (tau=0.05s)\",
                \"rcFilterTauSeconds\": RC_FILTER_TAU_SECONDS,
            },
            \"beforeRcFilterMetrics\": before.rc_filter_metrics,
            \"afterRcFilterMetrics\": after.rc_filter_metrics,
            \"beforeReport\": before_path.name if before_path else None,
'''


def apply_one(text: str, old: str, new: str, label: str) -> str:
    if new.strip() and new in text and old not in text:
        print(f\"skip {label}: already applied\")
        return text
    if old not in text:
        raise SystemExit(f\"could not find block: {label}\")
    print(f\"apply {label}\")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding=\"utf-8\")
    text = apply_one(text, OLD_IMPORT, IMPORT_BLOCK, \"import rc_filter\")
    text = apply_one(text, OLD_VERSION, NEW_VERSION, \"report version\")
    text = apply_one(text, OLD_RESULT_END, NEW_RESULT_END, \"TestResult field\")
    text = apply_one(text, OLD_AXIS_DEF, NEW_AXIS_DEF, \"axis_metrics signature\")
    text = apply_one(text, OLD_EMA, NEW_EMA, \"RC residual instead of EMA\")
    text = apply_one(text, OLD_CAPTURE, NEW_CAPTURE, \"capture loop dt + filtered samples\")
    text = apply_one(text, OLD_RESULT, NEW_RESULT, \"TestResult construction\")
    text = apply_one(text, OLD_PROTO, NEW_PROTO, \"report protocol + rc_filter_metrics\")
    text = apply_one(text, OLD_DELTA, NEW_DELTA, \"comparison highFreqRmsDelta\")
    text = apply_one(text, OLD_CMP_PROTO, NEW_CMP_PROTO, \"comparison protocol\")
    TARGET.write_text(text, encoding=\"utf-8\")
    print(f\"updated {TARGET}\")


if __name__ == \"__main__\":
    main()
