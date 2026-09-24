"""Gamepad Signal Lab core package."""

__version__ = "0.6.0"

from .analysis import TimingMetrics, OscillatorMetrics, timing_metrics, oscillator_metrics

__all__ = [
    "TimingMetrics",
    "OscillatorMetrics",
    "timing_metrics",
    "oscillator_metrics",
    "__version__",
]
