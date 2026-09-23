"""Operator BLE observations for a pair session. Passive notes only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NOTES_PATH = ROOT / "reports" / "ble_session.json"

PRESENCE = ("not_observed", "absent", "present")
FOLLOW = ("not_attempted", "not_followed", "followed")
ENCRYPTION = ("unknown", "not_encrypted", "encrypted")


def empty_note() -> dict:
    return {
        "schema": "rcm.ble.v1",
        "write_to_controller": False,
        "sniffer_in_process": False,
        "phase": "unspecified",
        "presence": "not_observed",
        "follow": "not_attempted",
        "encryption": "unknown",
        "adapter_mac": "",
        "pcap_path": "",
        "notes": "",
        "updated_at": None,
    }


def load_note(path: Path = NOTES_PATH) -> dict:
    if not path.exists():
        return empty_note()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_note()
    note = empty_note()
    note.update({key: data.get(key, note[key]) for key in note})
    return note


def save_note(payload: dict, path: Path = NOTES_PATH) -> dict:
    note = empty_note()
    presence = str(payload.get("presence") or "not_observed")
    follow = str(payload.get("follow") or "not_attempted")
    encryption = str(payload.get("encryption") or "unknown")
    if presence not in PRESENCE:
        raise ValueError("invalid presence")
    if follow not in FOLLOW:
        raise ValueError("invalid follow")
    if encryption not in ENCRYPTION:
        raise ValueError("invalid encryption")
    note["phase"] = str(payload.get("phase") or "unspecified")[:40]
    note["presence"] = presence
    note["follow"] = follow
    note["encryption"] = encryption
    note["adapter_mac"] = str(payload.get("adapter_mac") or "")[:32]
    note["pcap_path"] = str(payload.get("pcap_path") or "")[:260]
    note["notes"] = str(payload.get("notes") or "")[:500]
    note["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(note, indent=2) + "\n", encoding="utf-8")
    return note
