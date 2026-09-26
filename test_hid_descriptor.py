import random
import unittest
from unittest import mock

import controller_integrity as app
from hid_descriptor import decode_report, gamepad_layouts
from signal_lab.controller import ControllerAcquisition

# Descriptor read from a connected Vader 5 Pro. Windows presents every
# Xbox-compatible controller with this layout: five 16-bit axes (X, Y, Rx, Ry,
# and a shared Z trigger axis), ten buttons, and a hat, with no report ID.
XINPUT_HID = bytes.fromhex(
    "05010905a1010900a10009300931150025ff350045ff751095028102c00900a1000933"
    "0934150025ff751095028102c00900a1000932150025ff751095018102c005091901290a"
    "150025017501950a4500810205010939150125083500463b10650e750495018142750295"
    "018103750895028103c0"
)

# Bluetooth-Xbox-style layout: report 1 holds X/Y/Z/Rz sticks, brake and
# accelerator triggers, a hat, 15 buttons, then six vendor-defined 16-bit
# motion values. Report 3 is a vendor blob that happens to use usage 0x30.
BLUETOOTH_STYLE = bytes.fromhex("".join((
    "05010905a1018501",                                          # game pad, report 1
    "0930093109320935150027ffff0000751095048102",                # X, Y, Z, Rz: 16-bit
    "050209c509c4150026ff03750a95028102",                        # brake, accelerator: 10-bit
    "750495018103",                                              # padding
    "0501093915012508750495018142",                              # hat 1..8, null state
    "750495018103",                                              # padding
    "05091901290f150025017501950f8102",                          # buttons 1..15
    "750195018103",                                              # padding
    "0600ff09200921092209230924092516008026ff7f751095068102",    # vendor motion x6
    "85030600ff0930150026ff00750895088102",                      # report 3: vendor blob
    "c0",
)))

# PlayStation-style USB layout: 8-bit X/Y/Z/Rz sticks, Rx/Ry triggers, a
# vendor counter byte where the old guesser looked for buttons, a hat, 14
# buttons, then six vendor-defined 16-bit motion values.
PLAYSTATION_STYLE = bytes.fromhex("".join((
    "05010905a1018501",                                          # game pad, report 1
    "0930093109320935150026ff00750895048102",                    # X, Y, Z, Rz: 8-bit
    "09330934750895028102",                                      # Rx, Ry triggers
    "0600ff0920750895018102",                                    # vendor counter byte
    "0501093915002507750495018142",                              # hat 0..7, null state
    "05091901290e150025017501950e8102",                          # buttons 1..14
    "750695018103",                                              # padding
    "0600ff09210922092309240925092616008026ff7f751095068102",    # vendor motion x6
    "c0",
)))


def pack(length, *fields):
    """Build a report from (bit_offset, bit_size, value) fields."""
    bits = 0
    for offset, size, value in fields:
        bits |= (value & ((1 << size) - 1)) << offset
    return list(bits.to_bytes(length, "little"))


def xinput_report(lx, ly, rx, ry, trigger=32768, buttons=0, hat=0):
    words = b"".join(value.to_bytes(2, "little") for value in (lx, ly, rx, ry, trigger))
    packed = (buttons & 0x03FF) | (hat << 10)
    return list(words) + [packed & 0xFF, packed >> 8, 0, 0]


class HidDescriptorDecodingTests(unittest.TestCase):
    def test_xbox_reports_decode_as_16_bit_sticks(self):
        # Previously a genuine Xbox controller at rest read as both sticks
        # pushed fully up with phantom buttons, and a 0.06% stick wobble
        # swung the displayed sticks across most of their range.
        pad = app.HIDGamepad(info={
            "vendor_id": 0x045E, "product_id": 0x02FF,
            "product_string": "Controller (Xbox One For Windows)",
        })
        pad.report_layouts = gamepad_layouts(XINPUT_HID)
        centered = pad._parse_report(xinput_report(32768, 32768, 32768, 32768))
        self.assertEqual(
            {key: abs(centered[key]) for key in ("lx", "ly", "rx", "ry")},
            {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0},
        )
        self.assertEqual(centered["buttons"], 0)
        self.assertEqual((centered["dpad_x"], centered["dpad_y"]), (0, 0))
        self.assertNotIn("lt", centered)
        for wobble in (-40, 40):
            sample = pad._parse_report(xinput_report(*(32768 + wobble,) * 4))
            for axis in ("lx", "ly", "rx", "ry"):
                self.assertLess(abs(sample[axis]), 0.002)
            self.assertEqual(sample["buttons"], 0)
        pushed = pad._parse_report(xinput_report(65535, 0, 32768, 32768, buttons=0x001, hat=3))
        self.assertAlmostEqual(pushed["lx"], 1.0, places=4)
        self.assertAlmostEqual(pushed["ly"], 1.0, places=4)
        self.assertEqual(pushed["buttons"], 1)
        self.assertEqual((pushed["dpad_x"], pushed["dpad_y"]), (1, 0))

    def test_descriptor_decode_matches_known_vader_layout(self):
        vader = app.HIDGamepad(info={"vendor_id": 0x37D7, "product_id": 0x2401})
        layouts = gamepad_layouts(XINPUT_HID)
        for report in (
            [176, 126, 175, 127, 112, 129, 239, 124, 0, 128, 0, 0, 0, 0],
            [0, 0, 255, 255, 0, 128, 0, 128, 0, 128, 1, 9, 0, 0],
        ):
            expected = vader._parse_report(report)
            self.assertEqual(decode_report(layouts, report), expected)

    def test_motion_fields_never_reach_sticks_triggers_or_buttons(self):
        layouts = gamepad_layouts(BLUETOOTH_STYLE)
        self.assertEqual(sorted(layouts), [1])
        rng = random.Random(3)
        samples = []
        for _ in range(20):
            motion = [(120 + 16 * index, 16, rng.randrange(65536)) for index in range(6)]
            report = pack(
                27,
                (0, 8, 0x01),
                (8, 16, 32768), (24, 16, 32768), (40, 16, 49152), (56, 16, 32768),
                (72, 10, 1023), (82, 10, 0),
                (96, 4, 0),
                (104, 15, 0b100),
                *motion,
            )
            samples.append(decode_report(layouts, report))
        self.assertTrue(all(sample == samples[0] for sample in samples))
        self.assertAlmostEqual(samples[0]["rx"], 0.5)
        self.assertEqual((samples[0]["lt"], samples[0]["rt"]), (1.0, 0.0))
        self.assertEqual(samples[0]["buttons"], 0b100)
        self.assertEqual((samples[0]["dpad_x"], samples[0]["dpad_y"]), (0, 0))

    def test_reports_without_declared_sticks_are_not_guessed(self):
        pad = app.HIDGamepad(info={"vendor_id": 0x045E, "product_id": 0x0B13, "product_string": "Xbox Wireless Controller"})
        pad.report_layouts = gamepad_layouts(BLUETOOTH_STYLE)
        self.assertIsNone(pad._parse_report([0x03, 200, 10, 250, 5, 0, 0, 0, 0]))
        self.assertIsNone(pad._parse_report([0x01] + [128] * 9))  # shorter than report 1

    def test_playstation_buttons_come_from_button_bits_not_the_counter(self):
        pad = app.HIDGamepad(info={"vendor_id": 0x054C, "product_id": 0x0CE6, "product_string": "DualSense Wireless Controller"})
        pad.report_layouts = gamepad_layouts(PLAYSTATION_STYLE)
        report = pack(
            23,
            (0, 8, 0x01),
            (8, 8, 128), (16, 8, 128), (24, 8, 255), (32, 8, 128),
            (40, 8, 255), (48, 8, 0),
            (56, 8, 0x5A),
            (64, 4, 8),
            (68, 14, 0b10),
            (88, 16, 12345), (104, 16, 54321),
        )
        sample = pad._parse_report(report)
        self.assertEqual(sample["buttons"], 0b10)
        self.assertAlmostEqual(sample["rx"], 127 / 128)
        self.assertEqual((sample["lt"], sample["rt"]), (1.0, 0.0))
        self.assertEqual((sample["dpad_x"], sample["dpad_y"]), (0, 0))
        # Sony's extended Bluetooth report is a vendor blob in the descriptor
        # and keeps its fixed layout.
        self.assertIsNotNone(pad._parse_report([0x31, 0x00, 128, 128, 128, 128, 0, 0, 0, 0]))

    def test_unusable_descriptors_yield_no_layout(self):
        self.assertEqual(gamepad_layouts(b""), {})
        self.assertEqual(gamepad_layouts(XINPUT_HID + b"\x27\xff"), {})  # truncated item
        keyboard = bytes.fromhex("05010906a101050719e029e71500250175019508810295067508150025650507190029658100c0")
        self.assertEqual(gamepad_layouts(keyboard), {})

    def test_opening_a_device_selects_the_descriptor_decoder(self):
        class FakeDevice:
            descriptor: bytes | None = XINPUT_HID

            def open_path(self, _path):
                return None

            def set_nonblocking(self, _flag):
                return 0

            def get_report_descriptor(self, *_args):
                if self.descriptor is None:
                    raise OSError("descriptor unavailable")
                return list(self.descriptor)

        fake_hid = mock.Mock()
        fake_hid.device = FakeDevice
        info = {"vendor_id": 0x045E, "product_id": 0x02FF, "product_string": "Controller (Xbox One For Windows)"}
        with mock.patch.object(app, "hid", fake_hid):
            pad = app.HIDGamepad(path=b"xbox", info=info)
            self.assertTrue(pad._open())
            self.assertEqual(pad.decoder, "HID report descriptor")
            self.assertIs(pad.uses_report_ids, False)
            metadata = ControllerAcquisition._backend_metadata(pad)
            self.assertEqual(metadata["hid_decoder"], "HID report descriptor")

            FakeDevice.descriptor = None
            guessed = app.HIDGamepad(path=b"xbox", info=info)
            self.assertTrue(guessed._open())
            self.assertEqual(guessed.decoder, "guessed byte layout")
            self.assertEqual(guessed.report_layouts, {})


if __name__ == "__main__":
    unittest.main()
