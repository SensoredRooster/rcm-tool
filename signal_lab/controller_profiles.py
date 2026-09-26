"""Controller profile matching and user-learned Raw HID button maps."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Iterable


POSITION_PRESETS: dict[str, tuple[float, float, str]] = {
    "Face • top": (0.76, 0.29, "face"),
    "Face • right": (0.82, 0.39, "face"),
    "Face • bottom": (0.76, 0.49, "face"),
    "Face • left": (0.70, 0.39, "face"),
    "Center • left": (0.45, 0.39, "center"),
    "Center • right": (0.55, 0.39, "center"),
    "Shoulder • left": (0.24, 0.13, "shoulder"),
    "Shoulder • right": (0.76, 0.13, "shoulder"),
    "Rear • upper left": (0.36, 0.76, "rear"),
    "Rear • upper right": (0.64, 0.76, "rear"),
    "Rear • lower left": (0.36, 0.89, "rear"),
    "Rear • lower right": (0.64, 0.89, "rear"),
    "Extra • left": (0.15, 0.55, "extra"),
    "Extra • right": (0.85, 0.55, "extra"),
}


@dataclass
class ButtonMapping:
    name: str
    byte_index: int
    bit_mask: int
    x: float
    y: float
    kind: str = "extra"

    def active(self, raw: bytes) -> bool:
        return 0 <= self.byte_index < len(raw) and bool(raw[self.byte_index] & self.bit_mask)


@dataclass
class ControllerProfile:
    key: str
    name: str
    vendor_id: int | None
    product_id: int | None
    product_contains: str
    layout: str
    buttons: list[ButtonMapping]

    def matches(self, metadata: dict | None) -> bool:
        meta = metadata or {}
        vid = _coerce_int(meta.get("vid", meta.get("vendor_id")))
        pid = _coerce_int(meta.get("pid", meta.get("product_id")))
        product = " ".join(
            str(meta.get(k) or "")
            for k in ("controller_name", "product_string", "manufacturer", "manufacturer_string")
        ).casefold()
        if self.vendor_id is not None and vid != self.vendor_id:
            return False
        if self.product_id is not None and pid != self.product_id:
            return False
        return not self.product_contains or self.product_contains.casefold() in product


def _coerce_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None


def profile_key(metadata: dict | None) -> str:
    meta = metadata or {}
    vid = _coerce_int(meta.get("vid", meta.get("vendor_id")))
    pid = _coerce_int(meta.get("pid", meta.get("product_id")))
    product = str(meta.get("controller_name") or meta.get("product_string") or "unknown").strip()
    return f"{vid if vid is not None else -1:04X}:{pid if pid is not None else -1:04X}:{product.casefold()}"


def _decode_hex(raw_hex: str | None) -> bytes:
    if not raw_hex:
        return b""
    try:
        return bytes.fromhex(raw_hex)
    except ValueError:
        return b""


def stable_bit_changes(released_reports: Iterable[str], pressed_reports: Iterable[str]) -> list[tuple[int, int]]:
    """Return bits that are stable inside each phase and invert between phases.

    This rejects sequence counters, gyro/axis noise, and other changing packet
    fields instead of guessing that every changed Raw HID bit is a button.
    """
    released = [_decode_hex(item) for item in released_reports if item]
    pressed = [_decode_hex(item) for item in pressed_reports if item]
    released = [item for item in released if item]
    pressed = [item for item in pressed if item]
    if len(released) < 3 or len(pressed) < 3:
        return []
    width = min(min(map(len, released)), min(map(len, pressed)))
    changed: list[tuple[int, int]] = []
    for byte_index in range(width):
        for bit_index in range(8):
            mask = 1 << bit_index
            released_states = {bool(report[byte_index] & mask) for report in released}
            pressed_states = {bool(report[byte_index] & mask) for report in pressed}
            if len(released_states) == 1 and len(pressed_states) == 1 and released_states != pressed_states:
                changed.append((byte_index, mask))
    return changed


class ControllerProfileStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.profiles: list[ControllerProfile] = []
        self.load()

    def load(self) -> None:
        self.profiles = []
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in payload.get("profiles", []):
            try:
                buttons = [ButtonMapping(**entry) for entry in item.get("buttons", [])]
                self.profiles.append(
                    ControllerProfile(
                        key=str(item["key"]),
                        name=str(item.get("name") or "Custom controller"),
                        vendor_id=_coerce_int(item.get("vendor_id")),
                        product_id=_coerce_int(item.get("product_id")),
                        product_contains=str(item.get("product_contains") or ""),
                        layout=str(item.get("layout") or "generic"),
                        buttons=buttons,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "profiles": []}
        for profile in self.profiles:
            data = asdict(profile)
            payload["profiles"].append(data)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def find(self, metadata: dict | None) -> ControllerProfile | None:
        key = profile_key(metadata)
        for profile in self.profiles:
            if profile.key == key:
                return profile
        return None

    def ensure(self, metadata: dict | None, *, layout: str = "generic") -> ControllerProfile:
        existing = self.find(metadata)
        if existing is not None:
            return existing
        meta = metadata or {}
        name = str(meta.get("controller_name") or meta.get("product_string") or "Custom controller")
        profile = ControllerProfile(
            key=profile_key(meta),
            name=name,
            vendor_id=_coerce_int(meta.get("vid", meta.get("vendor_id"))),
            product_id=_coerce_int(meta.get("pid", meta.get("product_id"))),
            product_contains=name,
            layout=layout,
            buttons=[],
        )
        self.profiles.append(profile)
        return profile

    def upsert_button(
        self,
        metadata: dict | None,
        mapping: ButtonMapping,
        *,
        layout: str = "generic",
    ) -> ControllerProfile:
        profile = self.ensure(metadata, layout=layout)
        profile.buttons = [
            item for item in profile.buttons
            if item.name.casefold() != mapping.name.casefold()
            and not (item.byte_index == mapping.byte_index and item.bit_mask == mapping.bit_mask)
        ]
        profile.buttons.append(mapping)
        self.save()
        return profile

    def remove(self, metadata: dict | None) -> bool:
        key = profile_key(metadata)
        before = len(self.profiles)
        self.profiles = [profile for profile in self.profiles if profile.key != key]
        if len(self.profiles) != before:
            self.save()
            return True
        return False

    def markers(self, metadata: dict | None, raw_hex: str | None) -> list[dict]:
        profile = self.find(metadata)
        if profile is None:
            return []
        raw = _decode_hex(raw_hex)
        return [
            {
                "name": item.name,
                "x": item.x,
                "y": item.y,
                "kind": item.kind,
                "active": item.active(raw),
                "byte_index": item.byte_index,
                "bit_mask": item.bit_mask,
            }
            for item in profile.buttons
        ]
