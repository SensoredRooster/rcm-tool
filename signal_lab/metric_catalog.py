"""Definitions and provenance text shown throughout Gamepad Signal Lab.

These strings are intentionally short enough for hover help while documenting
what each displayed number actually represents.
"""
from __future__ import annotations

METRIC_HELP: dict[str, str] = {
    "rate": (
        "Report rate = reports that reached this PC in the 1 second ending at the newest report. "
        "Many controllers send their stick position on every USB poll even when untouched, so this stays near the polling rate at rest. "
        "Controllers that only send on change drop lower at rest. If nothing arrives for 0.5 s, the card waits instead of showing a rate."
    ),
    "interval": (
        "Average time between reports = 1000 / report rate, in milliseconds. "
        "It is an average over the last second, not a per-report USB timestamp."
    ),
    "jitter": (
        "Timing variation (jitter) is how much the gaps between reports differ from even spacing. "
        "Windows hands reports to apps in bunches, so gaps seen by an app measure the PC's reading schedule, not the controller. "
        "A USB capture (USBPcap + Wireshark, or a hardware USB analyzer) is needed to measure it honestly."
    ),
    "late": (
        "Repeats = consecutive reports in the last second whose raw bytes are identical. "
        "Stick sensor noise usually changes the position slightly in each report, so 0 repeats at rest is normal."
    ),
    "osc": (
        "Mean oscillator frequency from positive frequency samples returned by the active measurement source. "
        "Displayed digits do not imply accuracy beyond the connected instrument."
    ),
    "ppm": (
        "Frequency error in parts per million: (mean measured frequency - nominal frequency) / nominal frequency × 1,000,000."
    ),
    "clock_jitter": (
        "RMS period deviation calculated from reciprocal frequency samples. "
        "This is not direct edge-to-edge phase jitter unless the connected hardware supplies per-edge/per-cycle measurements."
    ),
    "stimulus": (
        "Current controlled stimulus state. Requested generator settings and instrument-reported readback are stored separately when readback is supported."
    ),
    "osc_mean": "Arithmetic mean of valid positive oscillator-frequency samples.",
    "osc_stdev": "Population standard deviation of oscillator-frequency samples.",
    "osc_error_hz": "Mean measured frequency minus the user-selected nominal frequency.",
    "osc_error_ppm": "Frequency error normalized to nominal frequency and expressed in parts per million.",
    "osc_drift": (
        "Difference between the mean of the first and last analysis windows. "
        "This is sample-window drift, not a calibrated long-term aging specification."
    ),
    "osc_outliers": "Samples farther than the configured sigma threshold from the sample mean.",
    "osc_period": "Mean reciprocal frequency, calculated as mean(1/f) across valid frequency samples.",
    "osc_rms": (
        "RMS deviation of reciprocal-frequency periods from their mean. "
        "Calculated from sampled frequency values."
    ),
    "osc_p2p": "Maximum minus minimum reciprocal-frequency period deviation across the analyzed samples.",
    "osc_ctc": (
        "RMS difference between successive reciprocal-frequency period samples. "
        "Only equivalent to true cycle-to-cycle jitter when each sample represents an individual cycle."
    ),
    "duty": (
        "Arithmetic mean of duty-cycle values returned by the connected measurement instrument. "
        "Unavailable when the instrument/SCPI dialect does not provide duty-cycle measurement."
    ),
    "allan": (
        "Two-sample Allan deviation using adjacent fractional-frequency samples at τ = one sample interval. "
        "τ is not one second unless the measurement cadence is exactly 1 Hz."
    ),
    "analog_noise": (
        "Average RMS deviation across LX/LY/RX/RY, reported only when every axis stays within the configured stationary-excursion threshold for the analysis window. "
        "If movement exceeds that threshold the noise-floor value is withheld. HID output alone cannot attribute filtering to the sensor, circuit, firmware, or host."
    ),
    "correlation": (
        "Pearson correlation after nearest-time alignment of gamepad interval deviation and oscillator frequency error. "
        "Correlation describes co-variation and does not establish causation."
    ),
}

CHART_HELP: dict[str, str] = {
    "report_interval": (
        "Host-arrival interval between selected Raw HID reports after USB. It is not a bus-level or firmware polling measurement."
    ),
    "gamepad_deviation": (
        "Controller report interval minus the selected reference interval. Positive values are later than reference."
    ),
    "osc_frequency": "Frequency samples returned by the active oscillator measurement source over elapsed capture time.",
    "osc_ppm": "Oscillator frequency error relative to the selected nominal frequency, expressed in ppm over elapsed time.",
    "interval_histogram": "Distribution of observed controller-report intervals. X is interval duration; Y is sample count.",
    "analog_stability": "Observed decoded stick-axis values over elapsed controller capture time.",
    "latency": (
        "Input latency requires a device-origin or physical-event timestamp paired with the host/USB observation. "
        "It remains unavailable when that timing reference is not present."
    ),
}
