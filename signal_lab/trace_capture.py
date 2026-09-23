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

    def command(self) -> list[str]:
        command = [self.executable]
        if self.driver.strip():
            command.extend(("--driver", self.driver.strip()))
        command.extend(("--config", f"samplerate={_rate_text(self.samplerate_hz)}"))
        if self.channels.strip():
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
        return {"ok": False, "message": "sigrok-cli was not found", "output": ""}
    try:
        completed = subprocess.run(
            [resolved, "--scan"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as exc:
        return {"ok": False, "message": str(exc), "output": ""}
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return {"ok": completed.returncode == 0, "message": "scan complete" if completed.returncode == 0 else "scan failed", "output": output}


def run_sigrok_capture(config: SigrokCaptureConfig) -> dict:
    """Run one finite capture and return process metadata plus the raw path."""
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            config.command(),
            capture_output=True,
            text=True,
            timeout=max(15.0, config.duration_s + 15.0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": str(exc), "command": config.command(), "output_path": str(config.output_path)}
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return {
        "ok": completed.returncode == 0 and config.output_path.exists(),
        "return_code": completed.returncode,
        "message": "capture complete" if completed.returncode == 0 else "capture failed",
        "command": config.command(),
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
        return {"samples": 0, "sample_rate_hz": 0.0, "mean": 0.0, "noise_rms": 0.0, "peak_to_peak": 0.0, "adjacent_delta_rms": 0.0}
    duration = timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0.0
    mean = statistics.fmean(values)
    deltas = [after - before for before, after in zip(values, values[1:])]
    return {
        "samples": len(values),
        "sample_rate_hz": (len(values) - 1) / duration if duration > 0 else 0.0,
        "mean": mean,
        "noise_rms": math.sqrt(statistics.fmean((value - mean) ** 2 for value in values)),
        "peak_to_peak": max(values) - min(values),
        "adjacent_delta_rms": math.sqrt(statistics.fmean(value * value for value in deltas)) if deltas else 0.0,
    }


def analyze_sigrok_csv(path: Path) -> dict:
    timestamps, channels, headers = load_sigrok_csv(path)
    return {
        "evidence_class": "instrument-measured-electrical-trace",
        "source": "sigrok-cli/libsigrok",
        "raw_capture_path": str(path),
        "sample_count": len(timestamps),
        "channel_names": headers,
        "duration_s": timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0.0,
        "channels": {
            name: _channel_metrics(timestamps, values)
            for name, values in zip(headers, channels)
        },
        "timestamps_s": timestamps,
        "values": {name: values for name, values in zip(headers, channels)},
    }
