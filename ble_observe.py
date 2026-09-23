"""Operator BLE observations. No radio driver. No injection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

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
        "phase": str(payload.get("phase", "unspecified")),
        "ble_present": present,
        "encryption": enc,
        "adapter_mac": str(payload.get("adapter_mac", ""))[:32],
        "pcap_path": str(payload.get("pcap_path", ""))[:260],
        "notes": str(payload.get("notes", ""))[:500],
        "recorded_at": stamp(),
    }


def save(payload: dict) -> Path:
    REPORTS.mkdir(exist_ok=True)
    data = normalize(payload)
    path = REPORTS / f"{data['recorded_at']}_BLE_{data['phase'].replace(' ', '_')}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
