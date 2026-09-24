"""Optional sigrok capture and analysis for upstream electrical evidence.

The sigrok executable is intentionally external: it keeps the GPL-licensed
sigrok stack separate from RcmTool's package and lets users install the device
drivers appropriate for their hardware. RcmTool stores the raw CSV and the
derived, explicitly instrument-observed metrics.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess


def find_sigrok_cli(executable: str = "sigrok-cli") -> str | None:
    """Resolve sigrok-cli from an explicit path or PATH."""
    candidate = Path(executable)
    if candidate.parent != Path(".") or candidate.is_absolute():
        return str(candidate) if candidate.exists() else None
    return shutil.which(executable)


def _rate_text(rate_hz: int) -> str:
    if rate_hz % 1_000_000 == 0:
        return f"{rate_hz // 1_000_000}m"
    if rate_hz % 1_000 == 0:
        return f"{rate_hz // 1_000}k"
    return str(rate_hz)


@dataclass(frozen=True)
class SigrokCaptureConfig:
    executable: str
    driver: str
    samplerate_hz: int
    duration_s: float
    output_path: Path
    channels: str = ""
    triggers: str = ""
    wait_trigger: bool = False

    def validate(self) -> None:
        driver = self.driver.strip()
        if not driver:
            raise ValueError("Enter the real analyzer driver shown by Scan devices.")
        if driver.split(":", 1)[0].strip().casefold() == "demo":
            raise ValueError("sigrok's built-in demo is software-generated and is not accepted as real hardware evidence.")
        if not self.channels.strip():
            raise ValueError("Enter the channel names reported for your connected analyzer.")

    def command(self) -> list[str]:
        self.validate()
        command = [self.executable]
        command.extend(("--driver", self.driver.strip()))
        command.extend(("--config", f"samplerate={_rate_text(self.samplerate_hz)}"))
        command.extend(("--channels", self.channels.strip()))
        if self.wait_trigger:
            command.append("--wait-trigger")
        if self.triggers.strip():
            command.extend(("--triggers", self.triggers.strip()))
        command.extend(("--time", f"{self.duration_s:g}s"))
        command.extend(("--output-format", "csv:header=true", "--output-file", str(self.output_path)))
        return command


def check_sigrok_cli(executable: str = "sigrok-cli") -> dict:
    resolved = find_sigrok_cli(executable)
    if not resolved:
        return {
            "available": False,
            "executable": executable,
            "message": "sigrok-cli was not found. Install sigrok-cli/PulseView and add it to PATH, or choose its executable path.",
        }
    try:
        completed = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError as exc:
        return {"available": False, "executable": resolved, "message": str(exc)}
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return {
        "available": completed.returncode == 0,
        "executable": resolved,
        "message": output[0] if output else f"sigrok-cli exited with {completed.returncode}",
    }


def scan_sigrok(executable: str = "sigrok-cli") -> dict:
    resolved = find_sigrok_cli(executable)
    if not resolved:
        return {"ok": False, "message": "sigrok-cli was not found", "output": "", "available_drivers": []}
    try:
        completed = subprocess.run(
            [resolved, "--scan"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as exc:
        return {"ok": False, "message": str(exc), "output": "", "available_drivers": []}
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    device_entry = re.compile(r"^\s*([\w.-]+)(?::\S+)?\s+-\s+", re.IGNORECASE)
    entries = [
        (line, device_entry.match(line))
        for line in output.splitlines()
    ]
    demo_entries = [line for line, match in entries if match and match.group(1).casefold() == "demo"]
    other_device_entries = [line for line, match in entries if match and match.group(1).casefold() != "demo"]
    available_drivers = list(dict.fromkeys(
        match.group(1)
        for _line, match in entries
        if match and match.group(1).casefold() != "demo"
    ))
    software_demo_present = bool(demo_entries) or any(
        re.match(r"\s*demo(?=\s|:|-|\()", line, flags=re.IGNORECASE)
        for line in output.splitlines()
    )
    if software_demo_present:
        visible_lines = [line for line, match in entries if not (match and match.group(1).casefold() == "demo")]
        if not other_device_entries:
            output = "No physical analyzer entry was listed. The built-in software demo was suppressed."
        else:
            output = "\n".join(visible_lines).strip()
            output += "\n\nBuilt-in software demo entry suppressed; only the remaining listed drivers can be selected."
    message = "scan complete" if completed.returncode == 0 else "scan failed"
    if software_demo_present:
        message += "; software demo suppressed and never eligible for evidence capture"
    return {
        "ok": completed.returncode == 0,
        "message": message,
        "output": output,
        "software_demo_present": software_demo_present,
        "available_drivers": available_drivers,
        "executable": resolved,
    }


def run_sigrok_capture(config: SigrokCaptureConfig) -> dict:
    """Run one finite capture and return process metadata plus the raw path."""
    try:
        command = config.command()
    except ValueError as exc:
        return {"ok": False, "message": str(exc), "command": [], "output_path": str(config.output_path)}
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(15.0, config.duration_s + 15.0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": str(exc), "command": command, "output_path": str(config.output_path)}
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return {
        "ok": completed.returncode == 0 and config.output_path.exists(),
        "return_code": completed.returncode,
        "message": "capture complete" if completed.returncode == 0 else "capture failed",
        "command": command,
        "output": output,
        "output_path": str(config.output_path),
    }


def _numeric_row(row: list[str]) -> list[float] | None:
    try:
        values = [float(cell.strip()) for cell in row if cell.strip()]
    except ValueError:
        return None
    return values if len(values) >= 2 else None


def load_sigrok_csv(path: Path) -> tuple[list[float], list[list[float]], list[str]]:
    """Load common sigrok CSV output, tolerating metadata comments and headers."""
    timestamps: list[float] = []
    channels: list[list[float]] = []
    headers: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.reader(line for line in handle if not line.lstrip().startswith("#")):
            if not row:
                continue
            values = _numeric_row(row)
            if values is None:
                if not headers:
                    headers = [cell.strip() or f"CH{index}" for index, cell in enumerate(row[1:], 1)]
                continue
            if not channels:
                channels = [[] for _ in values[1:]]
            if len(values) != len(channels) + 1:
                continue
            timestamps.append(values[0])
            for index, value in enumerate(values[1:]):
                channels[index].append(value)
    if not headers:
        headers = [f"CH{index}" for index in range(1, len(channels) + 1)]
    return timestamps, channels, headers[: len(channels)]


def _channel_metrics(timestamps: list[float], values: list[float]) -> dict:
    if not values:
        return {
            "samples": 0,
            "sample_rate_hz": 0.0,
            "mean": 0.0,
            "minimum": 0.0,
            "maximum": 0.0,
            "noise_rms": 0.0,
            "peak_to_peak": 0.0,
            "adjacent_delta_rms": 0.0,
            "signal_kind": "unavailable",
            "edge_count": 0,
            "edge_rate_hz": "n/a",
            "duty_cycle_percent": "n/a",
        }
    duration = timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0.0
    mean = statistics.fmean(values)
    deltas = [after - before for before, after in zip(values, values[1:])]
    minimum, maximum = min(values), max(values)
    digital_like = all(
        math.isclose(value, round(value), abs_tol=1e-9) and round(value) in (0, 1)
        for value in values
    )
    edge_count = 0
    duty_cycle: float | str = "n/a"
    edge_rate: float | str = "n/a"
    signal_kind = "digital-like" if digital_like else "analog"
    if digital_like and len(values) >= 2:
        edge_count = sum(before != after for before, after in zip(values, values[1:]))
        edge_rate = edge_count / duration if duration > 0 else 0.0
        duty_cycle = 100.0 * statistics.fmean(values)
    return {
        "samples": len(values),
        "sample_rate_hz": (len(values) - 1) / duration if duration > 0 else 0.0,
        "mean": mean,
        "minimum": minimum,
        "maximum": maximum,
        "noise_rms": math.sqrt(statistics.fmean((value - mean) ** 2 for value in values)),
        "peak_to_peak": maximum - minimum,
        "adjacent_delta_rms": math.sqrt(statistics.fmean(value * value for value in deltas)) if deltas else 0.0,
        "signal_kind": signal_kind,
        "edge_count": edge_count,
        "edge_rate_hz": edge_rate,
        "duty_cycle_percent": duty_cycle,
    }


def analyze_sigrok_csv(path: Path) -> dict:
    timestamps, channels, headers = load_sigrok_csv(path)
    timestamp_intervals = [
        after - before for before, after in zip(timestamps, timestamps[1:]) if after > before
    ]
    timestamp_pairs = max(0, len(timestamps) - 1)
    return {
        "evidence_class": "instrument-measured-electrical-trace",
        "source": "sigrok-cli/libsigrok",
        "raw_capture_path": str(path),
        "sample_count": len(timestamps),
        "timestamp_monotonic_percent": 100.0 * len(timestamp_intervals) / timestamp_pairs if timestamp_pairs else 0.0,
        "sample_rate_hz": (len(timestamps) - 1) / (timestamps[-1] - timestamps[0])
        if len(timestamps) >= 2 and timestamps[-1] > timestamps[0] else 0.0,
        "channel_names": headers,
        "duration_s": timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0.0,
        "channels": {
            name: _channel_metrics(timestamps, values)
            for name, values in zip(headers, channels)
        },
        "timestamps_s": timestamps,
        "values": {name: values for name, values in zip(headers, channels)},
    }
