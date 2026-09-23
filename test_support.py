import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import support


class SupportTests(unittest.TestCase):
    def test_redacts_secret_keys(self):
        redacted = support._redact({"api_key": "abc123", "nested": {"password": "secret"}, "safe": "ok"})
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["password"], "[REDACTED]")
        self.assertEqual(redacted["safe"], "ok")

    def test_support_bundle_contains_manifest_and_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temp}):
                support.log_event("unit_test")
                bundle = support.create_support_bundle()
                self.assertTrue(bundle.is_file())
                with zipfile.ZipFile(bundle) as archive:
                    names = set(archive.namelist())
                    self.assertIn("diagnostics/manifest.json", names)
                    self.assertIn("README.txt", names)
                    manifest = json.loads(archive.read("diagnostics/manifest.json"))
                    self.assertEqual(manifest["app"], "RCMTool")
                    self.assertEqual(manifest["session_id"], support.SESSION_ID)
                    self.assertTrue(any(name.startswith("logs/") for name in names))


if __name__ == "__main__":
    unittest.main()
