"""Operator BLE observations. No radio driver. No injection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"

PRESENT = ("absent", "present", "unknown")
ENCRYPT = ("n/a", "clear", "encrypted", "not_followed", "unknown")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize(payload: dict) -> dict:
    present = payload.get("ble_present", "unknown")
    if present not in PRESENT:
        present = "unknown"
    enc = payload.get("encryption", "unknown")
    if enc not in ENCRYPT:
        enc = "unknown"
    return {
        "kind": "ble_observation",
        "write_to_controller": False,
        "packet_injection": False,
        "phase": str(payload.get("phase", "unspecified"))[:40],
        "ble_present": present,
        "encryption": enc,
        "adapter_mac": str(payload.get("adapter_mac", ""))[:32],
        "pcap_path": str(payload.get("pcap_path", ""))[:260],
        "notes": str(payload.get("notes", ""))[:500],
        "recorded_at": stamp(),
    }


def filename_part(value: str) -> str:
    """Reduce free text to a single safe file-name component."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned[:40] or "unspecified"


def save(payload: dict) -> Path:
    REPORTS.mkdir(exist_ok=True)
    data = normalize(payload)
    # The phase arrives from an HTTP request; never let it add path segments.
    path = REPORTS / f"{data['recorded_at']}_BLE_{filename_part(data['phase'])}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
