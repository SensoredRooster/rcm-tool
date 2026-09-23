"""Safe deterministic sweep plan generation."""
from __future__ import annotations

from dataclasses import dataclass
import math
import random


@dataclass(frozen=True)
class SweepStep:
    index: int
    frequency_hz: float
    repetition: int


def make_sweep(start_hz: float, stop_hz: float, steps: int, *, logarithmic: bool = False, repetitions: int = 1, randomized: bool = False, seed: int = 2026) -> list[SweepStep]:
    if start_hz <= 0 or stop_hz <= 0:
        raise ValueError("Sweep frequencies must be greater than zero")
    if stop_hz < start_hz:
        raise ValueError("Stop frequency must be >= start frequency")
    if steps < 1 or steps > 10000:
        raise ValueError("Steps must be between 1 and 10000")
    if repetitions < 1 or repetitions > 1000:
        raise ValueError("Repetitions must be between 1 and 1000")
    if steps == 1:
        freqs = [start_hz]
    elif logarithmic:
        a, b = math.log10(start_hz), math.log10(stop_hz)
        freqs = [10 ** (a + (b-a)*i/(steps-1)) for i in range(steps)]
    else:
        freqs = [start_hz + (stop_hz-start_hz)*i/(steps-1) for i in range(steps)]
    expanded = [(f, r+1) for r in range(repetitions) for f in freqs]
    if randomized:
        random.Random(seed).shuffle(expanded)
    return [SweepStep(i+1, f, r) for i, (f, r) in enumerate(expanded)]
