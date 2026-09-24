"""Small, host-side dual-stick cleaner used by the calibration page.

This module never writes controller firmware and never fabricates samples.  It
only transforms samples that RcmTool has already received from the selected
physical controller.  Preset values are starting points; the rest calibration
is intentionally device- and session-specific.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


PRESETS: dict[str, dict[str, float | str]] = {
    "marius_midas": {"label": "Marius MIDAS", "sample_hz": 8000.0, "inner_deadzone": 0.02},
    "marius_tmr": {"label": "Marius TMR", "sample_hz": 8000.0, "inner_deadzone": 0.02},
    "marius_alps": {"label": "Marius ALPS", "sample_hz": 8000.0, "inner_deadzone": 0.025},
    "tarantula_8k": {"label": "Tarantula 8K", "sample_hz": 8000.0, "inner_deadzone": 0.02},
    "gamesir_8k": {"label": "GameSir 8K", "sample_hz": 8000.0, "inner_deadzone": 0.02},
}

_ALIASES = {
    "marius": "marius_midas",
    "midas": "marius_midas",
    "tmr": "marius_tmr",
    "alps": "marius_alps",
    "tarantula": "tarantula_8k",
    "gamesir": "gamesir_8k",
}


@dataclass
class JoystickFilter:
    """Rate-aware, calibrated filter for one stick."""

    preset_name: str
    inner_deadzone: float = 0.02
    hysteresis: float = 0.001
    center_x: float = 0.0
    center_y: float = 0.0
    apply_power_curve: bool = False
    power: float = 1.0
    _filtered_x: float = 0.0
    _filtered_y: float = 0.0

    @classmethod
    def from_preset(cls, name: str) -> "JoystickFilter":
        key = str(name).strip().lower().replace(" ", "_")
        key = _ALIASES.get(key, key)
        if key not in PRESETS:
            raise ValueError(f"Unknown stick preset: {name}")
        preset = PRESETS[key]
        return cls(
            preset_name=key,
            inner_deadzone=float(preset.get("inner_deadzone", 0.02)),
        )

    def calibrate_from_rest(self, xs: Sequence[float], ys: Sequence[float]) -> dict[str, float | int]:
        if not xs or not ys:
            raise ValueError("A physical rest capture is required before calibration.")
        n = min(len(xs), len(ys))
        x_values = [float(value) for value in xs[:n]]
        y_values = [float(value) for value in ys[:n]]
        self.center_x = sum(x_values) / n
        self.center_y = sum(y_values) / n
        x_excursion = max(x_values) - min(x_values)
        y_excursion = max(y_values) - min(y_values)
        radius = max(
            math.hypot(x - self.center_x, y - self.center_y)
            for x, y in zip(x_values, y_values)
        )
        self.hysteresis = min(0.012, max(0.0004, 3.0 * max(radius, x_excursion, y_excursion)))
        self._filtered_x = 0.0
        self._filtered_y = 0.0
        return {
            "sample_count": n,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "peak_to_peak_x": x_excursion,
            "peak_to_peak_y": y_excursion,
            "hysteresis": self.hysteresis,
        }

    def process(self, x: float, y: float, *, dt: float = 1.0 / 8000.0) -> tuple[float, float]:
        x_value = float(x) - self.center_x
        y_value = float(y) - self.center_y
        radius = math.hypot(x_value, y_value)
        deadzone = max(0.0, self.inner_deadzone + self.hysteresis)
        if radius <= deadzone:
            target_x, target_y = 0.0, 0.0
        else:
            scale = (radius - deadzone) / max(1e-9, 1.0 - deadzone)
            scale /= radius
            target_x, target_y = x_value * scale, y_value * scale
            if self.apply_power_curve and self.power != 1.0:
                target_radius = min(1.0, math.hypot(target_x, target_y))
                curved_radius = target_radius ** self.power
                factor = curved_radius / target_radius if target_radius else 0.0
                target_x, target_y = target_x * factor, target_y * factor

        # The time constant is expressed in seconds, so the same setting does
        # not silently become eight times stronger at 8 kHz.
        dt_value = min(1.0, max(1e-6, float(dt)))
        alpha = 1.0 - math.exp(-dt_value / 0.003)
        self._filtered_x += alpha * (target_x - self._filtered_x)
        self._filtered_y += alpha * (target_y - self._filtered_y)
        return self._filtered_x, self._filtered_y


class DualStick:
    """Apply two independent calibrated filters to four normalized axes."""

    def __init__(self, preset: str = "marius_midas") -> None:
        self.left = JoystickFilter.from_preset(preset)
        self.right = JoystickFilter.from_preset(preset)

    def process(
        self,
        lx: float,
        ly: float,
        rx: float,
        ry: float,
        *,
        dt: float = 1.0 / 8000.0,
    ) -> tuple[float, float, float, float]:
        left = self.left.process(lx, ly, dt=dt)
        right = self.right.process(rx, ry, dt=dt)
        return (*left, *right)
