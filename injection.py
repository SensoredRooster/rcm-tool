"""In-app noise recipes for injected self-test and After-phase pair noise. Does not write to the controller."""

from __future__ import annotations

import math
import random

RECIPES = {
    "injected-hf-sine": {
        "recipe": "hf_sine",
        "freq_hz": 30.0,
        "amplitude": 0.06,
        "axes": ("lx",),
        "label": "Injected HF sine",
    },
    "injected-slow-sine": {
        "recipe": "slow_sine",
        "freq_hz": 0.5,
        "amplitude": 0.06,
        "axes": ("lx",),
        "label": "Injected slow sine",
    },
    "injected-rest-tick": {
        "recipe": "rest_tick",
        "freq_hz": 25.0,
        "amplitude": 0.05,
        "axes": ("lx",),
        "label": "Injected rest tick",
    },
    "after-hf-sine": {
        "recipe": "hf_sine",
        "freq_hz": 30.0,
        "amplitude": 0.08,
        "axes": ("lx", "ly", "rx", "ry"),
        "label": "After HF sine",
    },
    "after-band-noise": {
        "recipe": "white",
        "freq_hz": 0.0,
        "amplitude": 0.05,
        "axes": ("lx", "ly", "rx", "ry"),
        "label": "After band noise",
    },
}

PROTOCOL_LABELS = {
    spec["label"]: protocol for protocol, spec in RECIPES.items()
}

AFTER_NOISE_LABELS = {
    "None": "",
    "HF sine (all axes)": "after-hf-sine",
    "Band noise (all axes)": "after-band-noise",
    "HF sine LX only": "injected-hf-sine",
}


def is_injected(protocol: str) -> bool:
    return protocol in RECIPES


def describe(protocol: str) -> dict:
    spec = RECIPES.get(protocol)
    if not spec:
        return {"recipe": "none", "freq_hz": 0.0, "amplitude": 0.0, "axes": []}
    return {
        "recipe": spec["recipe"],
        "freq_hz": spec["freq_hz"],
        "amplitude": spec["amplitude"],
        "axes": list(spec["axes"]),
    }


def apply_injection(sample: dict, t_seconds: float, protocol: str) -> dict:
    spec = RECIPES.get(protocol)
    if not spec or not sample:
        return sample
    if spec["recipe"] == "white":
        wave = spec["amplitude"] * (random.random() * 2.0 - 1.0)
    else:
        wave = spec["amplitude"] * math.sin(2.0 * math.pi * spec["freq_hz"] * t_seconds)
    out = dict(sample)
    for axis in spec["axes"]:
        if axis in out:
            value = float(out[axis]) + wave
            out[axis] = max(-1.0, min(1.0, value))
    return out


def classify_injection(protocol: str, rc_metrics: dict | None) -> tuple[str, list[str]]:
    rc_metrics = rc_metrics or {}
    spec = RECIPES.get(protocol)
    if not spec:
        return "unsupported", ["Not an injected self-test"]
    lx = float(rc_metrics.get("lx_high_freq_rms", 0.0) or 0.0)
    ry = float(rc_metrics.get("ry_high_freq_rms", 0.0) or 0.0)
    reasons = [f"self-test recipe {spec['recipe']}", f"lx_high_freq_rms={lx:.5f}"]
    if spec["recipe"] == "slow_sine":
        if lx < 0.02:
            return "pass", reasons + ["slow sine stayed in the RC trend"]
        return "review", reasons + ["slow sine leaked into HF RMS; check tau"]
    if spec["recipe"] == "white":
        if lx >= 0.015:
            return "pass", reasons + ["band noise raised HF RMS"]
        return "review", reasons + ["expected HF RMS rise from band noise"]
    if lx >= 0.02 and ry < 0.02:
        return "pass", reasons + ["HF energy landed on LX and not RY"]
    if lx < 0.02:
        return "review", reasons + ["expected LX HF RMS did not rise"]
    return "review", reasons + ["RY also rose; injection may be on the wrong axis"]
