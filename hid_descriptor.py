"""Decode HID gamepad input reports from the device's own report descriptor.

A HID report descriptor states exactly which bits of an input report hold each
control. Decoding from it, instead of guessing a byte layout, keeps every other
field out of the sample: gyroscope and accelerometer data, counters, battery
bytes, and vendor data are never read as sticks, triggers, buttons, or D-pad.

Guessing is what made Xbox-format controllers look motion-sensitive: their
16-bit stick values were read one byte at a time, so a stick wobble of a few
hundredths of a percent swung the displayed stick across its whole range.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Optional

GENERIC_DESKTOP = 0x01
SIMULATION = 0x02
BUTTON = 0x09
X, Y, Z, RX, RY, RZ, HAT_SWITCH = 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x39
ACCELERATOR, BRAKE = 0xC4, 0xC5

# D-pad (dx, dy) for hat positions clockwise from north.
_HAT_DIRECTIONS = ((0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1))


@dataclass(frozen=True)
class InputField:
    """One control in an input report. Offsets include the report-ID byte."""

    report_id: int
    bit_offset: int
    bit_size: int
    usage_page: int
    usage: int
    logical_min: int
    logical_max: int

    @property
    def value_range(self) -> tuple[int, int]:
        low, high = self.logical_min, self.logical_max
        mask = (1 << self.bit_size) - 1
        if high < low:
            # A 0..0xFFFF range written as a signed maximum reads as -1;
            # Windows' reconstructed descriptors do this for 16-bit axes.
            high &= mask
        if high <= low:
            low, high = 0, mask
        return low, high

    def read(self, report_bits: int) -> int:
        raw = (report_bits >> self.bit_offset) & ((1 << self.bit_size) - 1)
        low, _high = self.value_range
        if low < 0 and raw & (1 << (self.bit_size - 1)):
            raw -= 1 << self.bit_size
        return raw

    @property
    def end_byte(self) -> int:
        return (self.bit_offset + self.bit_size + 7) // 8


@dataclass(frozen=True)
class ButtonArray:
    """Array-style input whose slots hold the numbers of pressed buttons."""

    report_id: int
    bit_offset: int
    bit_size: int
    count: int
    first_button: int
    logical_min: int

    @property
    def end_byte(self) -> int:
        return (self.bit_offset + self.bit_size * self.count + 7) // 8


def _item_value(data: bytes, signed: bool) -> int:
    return int.from_bytes(data, "little", signed=signed) if data else 0


def parse_input_fields(descriptor) -> Optional[tuple[list[InputField], list[ButtonArray]]]:
    """Parse the Input items of a HID report descriptor.

    Returns None when the descriptor is missing or malformed.
    """
    data = bytes(descriptor or b"")
    if not data:
        return None
    state = {"usage_page": 0, "logical_min": 0, "logical_max": 0, "report_size": 0, "report_count": 0, "report_id": 0}
    stack: list[dict[str, int]] = []
    usages: list[tuple[int, int]] = []  # (item size, value), resolved at the main item
    usage_min: Optional[tuple[int, int]] = None
    usage_max: Optional[tuple[int, int]] = None
    offsets: dict[int, int] = {}
    fields: list[InputField] = []
    arrays: list[ButtonArray] = []

    def full_usage(item: tuple[int, int]) -> tuple[int, int]:
        size, value = item
        if size == 4:  # Extended usage carries its own usage page.
            return value >> 16, value & 0xFFFF
        return state["usage_page"], value

    index = 0
    while index < len(data):
        prefix = data[index]
        if prefix == 0xFE:  # Long item: size byte, tag byte, then data.
            if index + 1 >= len(data):
                return None
            index += 3 + data[index + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        item_type = (prefix >> 2) & 0x03
        tag = prefix >> 4
        payload = data[index + 1:index + 1 + size]
        if len(payload) != size:
            return None
        index += 1 + size

        if item_type == 1:  # Global
            if tag == 0:
                state["usage_page"] = _item_value(payload, False)
            elif tag == 1:
                state["logical_min"] = _item_value(payload, True)
            elif tag == 2:
                state["logical_max"] = _item_value(payload, True)
            elif tag == 7:
                state["report_size"] = _item_value(payload, False)
            elif tag == 8:
                state["report_id"] = _item_value(payload, False)
            elif tag == 9:
                state["report_count"] = _item_value(payload, False)
            elif tag == 10:
                stack.append(dict(state))
            elif tag == 11 and stack:
                state = stack.pop()
        elif item_type == 2:  # Local
            value = _item_value(payload, False)
            if tag == 0:
                usages.append((size, value))
            elif tag == 1:
                usage_min = (size, value)
            elif tag == 2:
                usage_max = (size, value)
        elif item_type == 0:  # Main
            if tag == 8:  # Input
                flags = _item_value(payload, False)
                report_id = state["report_id"]
                bits, count = state["report_size"], state["report_count"]
                start = offsets.get(report_id, 8 if report_id else 0)
                if not flags & 0x01 and bits:  # Data, not constant padding.
                    listed = [full_usage(item) for item in usages]
                    if not listed and usage_min is not None and usage_max is not None:
                        page, first = full_usage(usage_min)
                        _page, last = full_usage(usage_max)
                        listed = [(page, usage) for usage in range(first, last + 1)]
                    if flags & 0x02:  # Variable: one value per usage.
                        for slot in range(count if listed else 0):
                            page, usage = listed[min(slot, len(listed) - 1)]
                            fields.append(InputField(
                                report_id, start + slot * bits, bits, page, usage,
                                state["logical_min"], state["logical_max"],
                            ))
                    elif listed and listed[0][0] == BUTTON:  # Array of pressed buttons.
                        arrays.append(ButtonArray(
                            report_id, start, bits, count, listed[0][1], state["logical_min"],
                        ))
                offsets[report_id] = start + bits * count
            # Output, Feature, and Collection items never move input offsets.
            usages, usage_min, usage_max = [], None, None
    if index != len(data):
        return None
    return fields, arrays



def _trigger(control: InputField, report_bits: int) -> float:
    low, high = control.value_range
    return max(0.0, min(1.0, (control.read(report_bits) - low) / float(high - low)))


@dataclass(frozen=True)
class GamepadLayout:
    """Where one report carries the gamepad's sticks, triggers, buttons, and hat."""

    report_id: int
    left_x: InputField
    left_y: InputField
    right_x: InputField
    right_y: InputField
    left_trigger: Optional[InputField] = None
    right_trigger: Optional[InputField] = None
    combined_trigger: Optional[InputField] = None
    hat: Optional[InputField] = None
    buttons: tuple[InputField, ...] = ()
    button_arrays: tuple[ButtonArray, ...] = ()
    min_bytes: int = 0

    @cached_property
    def _plan(self) -> tuple:
        """Per-control shifts, masks, and scaling, worked out once per device.

        8 kHz controllers need every report decoded in a few microseconds.
        """
        def axis(control: InputField, invert: bool) -> tuple:
            low, high = control.value_range
            return (
                control.bit_offset, (1 << control.bit_size) - 1,
                (1 << (control.bit_size - 1)) if low < 0 else 0, 1 << control.bit_size,
                (low + high + 1) / 2.0, (high - low + 1) / 2.0, -1.0 if invert else 1.0,
            )

        axes = (
            ("lx", axis(self.left_x, False)), ("ly", axis(self.left_y, True)),
            ("rx", axis(self.right_x, False)), ("ry", axis(self.right_y, True)),
        )
        single_bit_buttons = tuple(
            (button.bit_offset, 1 << (button.usage - 1))
            for button in self.buttons
            if 1 <= button.usage <= 32 and button.bit_size == 1
        )
        wide_buttons = tuple(
            button for button in self.buttons if 1 <= button.usage <= 32 and button.bit_size != 1
        )
        return axes, single_bit_buttons, wide_buttons

    def decode(self, report) -> Optional[dict]:
        data = bytes(report)
        if len(data) < self.min_bytes:
            return None
        if self.report_id and data[0] != self.report_id:
            return None
        bits = int.from_bytes(data, "little")
        axes, single_bit_buttons, wide_buttons = self._plan
        # HID Y axes grow downward; the plan flips them so up is positive.
        sample: dict = {}
        for name, (offset, mask, sign_bit, wrap, center, half, direction) in axes:
            raw = (bits >> offset) & mask
            if sign_bit and raw & sign_bit:
                raw -= wrap
            value = (raw - center) / half
            sample[name] = direction * (1.0 if value > 1.0 else -1.0 if value < -1.0 else value)
        if self.left_trigger is not None and self.right_trigger is not None:
            sample["lt"] = _trigger(self.left_trigger, bits)
            sample["rt"] = _trigger(self.right_trigger, bits)
        elif self.combined_trigger is not None:
            # One shared trigger axis cannot be split into LT and RT.
            sample["combined_trigger_raw"] = self.combined_trigger.read(bits)
        if self.buttons or self.button_arrays:
            mask = 0
            for offset, bit in single_bit_buttons:
                if (bits >> offset) & 1:
                    mask |= bit
            for button in wide_buttons:
                if button.read(bits):
                    mask |= 1 << (button.usage - 1)
            for array in self.button_arrays:
                for slot in range(array.count):
                    raw = (bits >> (array.bit_offset + slot * array.bit_size)) & ((1 << array.bit_size) - 1)
                    number = array.first_button + raw - array.logical_min
                    if raw and 1 <= number <= 32:
                        mask |= 1 << (number - 1)
            sample["buttons"] = mask
        if self.hat is not None:
            low, high = self.hat.value_range
            raw = self.hat.read(bits)
            step = {8: 1, 4: 2}.get(high - low + 1)
            if step is not None and low <= raw <= high:
                sample["dpad_x"], sample["dpad_y"] = _HAT_DIRECTIONS[(raw - low) * step]
            else:  # Null state: hat released.
                sample["dpad_x"], sample["dpad_y"] = 0, 0
        return sample


def gamepad_layouts(descriptor) -> dict[int, GamepadLayout]:
    """Return a decodable layout for each input report that carries two sticks."""
    parsed = parse_input_fields(descriptor)
    if parsed is None:
        return {}
    fields, arrays = parsed
    layouts: dict[int, GamepadLayout] = {}
    for report_id in sorted({item.report_id for item in fields}):
        controls: dict[tuple[int, int], InputField] = {}
        buttons: list[InputField] = []
        for item in fields:
            if item.report_id != report_id:
                continue
            if item.usage_page == BUTTON:
                buttons.append(item)
            else:
                controls.setdefault((item.usage_page, item.usage), item)

        def desktop(usage: int) -> Optional[InputField]:
            return controls.get((GENERIC_DESKTOP, usage))

        left_x, left_y = desktop(X), desktop(Y)
        z, rz, rx, ry = desktop(Z), desktop(RZ), desktop(RX), desktop(RY)
        brake, accelerator = controls.get((SIMULATION, BRAKE)), controls.get((SIMULATION, ACCELERATOR))
        combined = None
        if left_x is None or left_y is None:
            continue
        if z is not None and rz is not None:
            # DirectInput / PlayStation convention: Z and Rz are the right stick.
            right_x, right_y = z, rz
            left_trigger, right_trigger = rx or brake, ry or accelerator
        elif rx is not None and ry is not None:
            # Xbox convention: Rx and Ry are the right stick, Z the shared triggers.
            right_x, right_y = rx, ry
            left_trigger, right_trigger = brake, accelerator
            if left_trigger is None or right_trigger is None:
                combined = z
        else:
            continue
        if left_trigger is None or right_trigger is None:
            left_trigger = right_trigger = None
        hat = desktop(HAT_SWITCH)
        report_arrays = tuple(item for item in arrays if item.report_id == report_id)
        used = [left_x, left_y, right_x, right_y, *buttons]
        used += [item for item in (left_trigger, right_trigger, combined, hat) if item is not None]
        layouts[report_id] = GamepadLayout(
            report_id=report_id,
            left_x=left_x,
            left_y=left_y,
            right_x=right_x,
            right_y=right_y,
            left_trigger=left_trigger,
            right_trigger=right_trigger,
            combined_trigger=combined,
            hat=hat,
            buttons=tuple(buttons),
            button_arrays=report_arrays,
            min_bytes=max([item.end_byte for item in used] + [item.end_byte for item in report_arrays]),
        )
    return layouts


def decode_report(layouts: dict[int, GamepadLayout], report) -> Optional[dict]:
    """Decode one raw report, or return None if it carries no gamepad controls."""
    if not layouts or not report:
        return None
    layout = layouts[0] if 0 in layouts else layouts.get(report[0])
    return layout.decode(report) if layout is not None else None


def hid_descriptor_uses_report_ids(descriptor) -> Optional[bool]:
    """Return whether a HID report descriptor declares any Report ID item.

    Without numbered reports, hidapi returns input data starting at byte 0, so
    that byte must not be treated as a report ID. None means the descriptor was
    unavailable or malformed.
    """
    data = bytes(descriptor or b"")
    if not data:
        return None
    index = 0
    while index < len(data):
        prefix = data[index]
        if prefix == 0xFE:  # Long item: size byte, tag byte, then data.
            if index + 1 >= len(data):
                return None
            index += 3 + data[index + 1]
            continue
        if prefix & 0xFC == 0x84:  # Global item with the Report ID tag.
            return True
        index += 1 + (0, 1, 2, 4)[prefix & 0x03]
    return False if index == len(data) else None
