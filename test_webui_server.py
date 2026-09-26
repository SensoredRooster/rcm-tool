import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

import ble_observe
import webui_server


def _handler(**headers):
    handler = webui_server.Handler.__new__(webui_server.Handler)
    message = Message()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    handler.headers = message
    return handler


class WebUiServerTests(unittest.TestCase):
    def test_post_accepts_only_same_origin_json(self):
        page = {"host": "127.0.0.1:8765", "content_type": "application/json"}
        self.assertTrue(_handler(**page, origin="http://127.0.0.1:8765")._trusted_post())
        self.assertTrue(_handler(**page)._trusted_post())
        self.assertFalse(_handler(**page, origin="https://evil.example")._trusted_post())
        self.assertFalse(_handler(host="127.0.0.1:8765", content_type="text/plain")._trusted_post())
        self.assertFalse(
            _handler(host="rebound.example:8765", content_type="application/json")._trusted_post()
        )

    def test_ble_note_phase_cannot_leave_reports_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            reports = Path(folder) / "reports"
            with mock.patch.object(ble_observe, "REPORTS", reports):
                path = ble_observe.save({"phase": "../../../outside/settings"})
            self.assertEqual(path.parent, reports)
            self.assertTrue(path.name.endswith("_BLE_outside_settings.json"))
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
