"""Before/After pair-delta screen.

This is a change detector, not an organic vs non-organic verdict.
"""

from __future__ import annotations

STICK_AXES = ("lx", "ly", "rx", "ry")

NEUTRAL_HF_DELTA = 0.003
GUIDED_HF_DELTA = 0.005


def _hf(metrics: dict, axis: str) -> float:
    if not metrics:
        return 0.0
    key = f"{axis}_high_freq_rms"
    if key in metrics:
        return float(metrics[key])
    return float(metrics.get("jitter_rms", 0.0) or 0.0)


def classify_pair_delta(
    before_protocol: str,
    after_protocol: str,
    before_classification: str,
    after_classification: str,
    before_metrics: dict | None,
    after_metrics: dict | None,
    *,
    neutral_hf_delta: float = NEUTRAL_HF_DELTA,
    guided_hf_delta: float = GUIDED_HF_DELTA,
) -> dict:
    before_metrics = before_metrics or {}
    after_metrics = after_metrics or {}
    per_axis = {}
    for axis in STICK_AXES:
        before_val = _hf(before_metrics, axis)
        after_val = _hf(after_metrics, axis)
        per_axis[axis] = {
            "before_high_freq_rms": before_val,
            "after_high_freq_rms": after_val,
            "high_freq_rms_delta": after_val - before_val,
        }

    reasons: list[str] = []
    if before_classification == "unsupported" or after_classification == "unsupported":
        label = "unsupported"
        reasons.append("One or both phases did not produce a usable capture")
    elif before_protocol != after_protocol:
        label = "unsupported"
        reasons.append("Before and After used different protocols")
    else:
        deltas = [per_axis[axis]["high_freq_rms_delta"] for axis in STICK_AXES]
        max_delta = max(deltas) if deltas else 0.0
        protocol = before_protocol
        if protocol == "neutral-stick":
            if max_delta >= neutral_hf_delta:
                label = "increased_at_rest"
                reasons.append(
                    f"Neutral high-freq RMS rose by {max_delta:.5f} on at least one axis"
                )
            else:
                label = "unchanged"
                reasons.append("Neutral high-freq RMS stayed inside the pair-delta floor")
        elif protocol == "guided-movement":
            if max_delta >= guided_hf_delta:
                label = "increased_high_freq"
                reasons.append(
                    f"Guided high-freq RMS rose by {max_delta:.5f} on at least one axis"
                )
            else:
                label = "unchanged"
                reasons.append("Guided high-freq RMS stayed inside the pair-delta floor")
        else:
            label = "unsupported"
            reasons.append(f"Unknown protocol {protocol!r}")

    return {
        "pair_delta": label,
        "meaning": {
            "unchanged": "After stayed in the same HF-RMS band as Before",
            "increased_high_freq": "After added high-frequency energy during guided motion; Neutral was not part of this pair",
            "increased_at_rest": "After was louder with sticks untouched",
            "unsupported": "Pair cannot be scored",
        }.get(label, label),
        "not_a_verdict": "This is a change detector. It is not organic vs non-organic.",
        "before_protocol": before_protocol,
        "after_protocol": after_protocol,
        "neutral_hf_delta_floor": neutral_hf_delta,
        "guided_hf_delta_floor": guided_hf_delta,
        "axes": per_axis,
        "reasons": reasons,
    }
