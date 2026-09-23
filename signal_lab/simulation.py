"""Deterministic simulation sources used by the UI and automated tests."""
from __future__ import annotations

from dataclasses import dataclass
import math
import random


@dataclass
class SimulatedGamepadConfig:
    rate_hz: float = 1000.0
    jitter_ms: float = 0.05
    periodic_jitter_ms: float = 0.0
    periodic_hz: float = 60.0
    spike_every: int = 0
    spike_ms: float = 1.0
    drop_every: int = 0
    seed: int = 1337


class GamepadSimulator:
    def __init__(self, config: SimulatedGamepadConfig | None = None) -> None:
        self.config = config or SimulatedGamepadConfig()
        self.random = random.Random(self.config.seed)
        self.index = 0
        self.timestamp_ns = 0

    def reset(self) -> None:
        self.random = random.Random(self.config.seed)
        self.index = 0
        self.timestamp_ns = 0

    def next_timestamp_ns(self) -> int:
        self.index += 1
        base_ms = 1000.0 / max(self.config.rate_hz, 1e-9)
        random_jitter = self.random.uniform(-self.config.jitter_ms, self.config.jitter_ms)
        periodic = self.config.periodic_jitter_ms * math.sin(
            2 * math.pi * self.config.periodic_hz * (self.index / max(self.config.rate_hz, 1.0))
        )
        spike = self.config.spike_ms if self.config.spike_every and self.index % self.config.spike_every == 0 else 0.0
        multiplier = 2 if self.config.drop_every and self.index % self.config.drop_every == 0 else 1
        interval_ms = max(0.001, base_ms * multiplier + random_jitter + periodic + spike)
        self.timestamp_ns += int(interval_ms * 1_000_000)
        return self.timestamp_ns

    def sample(self) -> dict:
        t = self.timestamp_ns / 1_000_000_000.0
        slow = 0.03 * math.sin(t * 2.2)
        def noise() -> float:
            return self.random.uniform(-0.0025, 0.0025)
        return {
            "lx": slow + noise(),
            "ly": -slow + noise(),
            "rx": noise(),
            "ry": noise(),
            "lt": 0.0,
            "rt": 0.0,
        }


@dataclass
class SimulatedOscillatorConfig:
    nominal_frequency_hz: float = 12_000_000.0
    ppm_offset: float = -1.5
    random_jitter_ppm: float = 0.25
    periodic_jitter_ppm: float = 0.15
    periodic_hz: float = 2.0
    drift_ppm_per_second: float = 0.01
    seed: int = 7331


class OscillatorSimulator:
    def __init__(self, config: SimulatedOscillatorConfig | None = None) -> None:
        self.config = config or SimulatedOscillatorConfig()
        self.random = random.Random(self.config.seed)
        self.index = 0

    def reset(self) -> None:
        self.random = random.Random(self.config.seed)
        self.index = 0

    def next_frequency_hz(self, sample_period_s: float = 0.05) -> float:
        self.index += 1
        elapsed = self.index * sample_period_s
        periodic = self.config.periodic_jitter_ppm * math.sin(2 * math.pi * self.config.periodic_hz * elapsed)
        random_ppm = self.random.uniform(-self.config.random_jitter_ppm, self.config.random_jitter_ppm)
        total_ppm = self.config.ppm_offset + periodic + random_ppm + self.config.drift_ppm_per_second * elapsed
        return self.config.nominal_frequency_hz * (1.0 + total_ppm / 1_000_000.0)


@dataclass
class StimulusState:
    enabled: bool = False
    frequency_hz: float = 1000.0
    amplitude_vpp: float = 0.10
    offset_v: float = 0.0
    waveform: str = "SINE"


@dataclass(frozen=True)
class SimulatedStimulusResponse:
    gamepad_extra_jitter_ms: float
    oscillator_extra_ppm: float
    analog_noise_extra: float


def stimulus_response(frequency_hz: float, amplitude_vpp: float) -> SimulatedStimulusResponse:
    """Deterministic synthetic DUT response used only in simulation mode.

    The shape intentionally includes two broad resonances so automated sweeps
    produce a known, testable response surface. It is not a physical model of a
    particular controller.
    """
    if frequency_hz <= 0 or amplitude_vpp <= 0:
        return SimulatedStimulusResponse(0.0, 0.0, 0.0)

    def resonance(center_hz: float, width_octaves: float) -> float:
        distance = math.log2(max(frequency_hz, 1e-12) / center_hz)
        return math.exp(-0.5 * (distance / width_octaves) ** 2)

    response = 0.15 + 1.8 * resonance(1000.0, 0.55) + 1.0 * resonance(5200.0, 0.42)
    scale = max(0.0, amplitude_vpp)
    return SimulatedStimulusResponse(
        gamepad_extra_jitter_ms=0.12 * scale * response,
        oscillator_extra_ppm=4.0 * scale * response,
        analog_noise_extra=0.004 * scale * response,
    )
