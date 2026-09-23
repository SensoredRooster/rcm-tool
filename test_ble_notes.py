import tempfile
import unittest
from pathlib import Path

from ble_notes import empty_note, load_note, save_note


class BleNotesTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ble_session.json"
            saved = save_note(
                {
                    "phase": "After",
                    "presence": "present",
                    "follow": "followed",
                    "encryption": "encrypted",
                    "adapter_mac": "AA:BB:CC:DD:EE:FF",
                    "pcap_path": "captures/after.pcap",
                    "notes": "New advertiser after DUT in path",
                },
                path,
            )
            loaded = load_note(path)
            self.assertEqual(saved["presence"], "present")
            self.assertEqual(loaded["encryption"], "encrypted")
            self.assertFalse(loaded["write_to_controller"])

    def test_rejects_bad_presence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ble_session.json"
            with self.assertRaises(ValueError):
                save_note({"presence": "hacked"}, path)

    def test_missing_file(self):
        note = load_note(Path("/tmp/does-not-exist-rcm-ble.json"))
        self.assertEqual(note["presence"], empty_note()["presence"])


if __name__ == "__main__":
    unittest.main()
