from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

import support


class SupportTests(unittest.TestCase):
    def test_redacts_secret_keys(self):
        redacted = support._redact({"api_key": "abc123", "nested": {"password": "secret"}, "safe": "ok"})
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["password"], "[REDACTED]")
        self.assertEqual(redacted["safe"], "ok")

    def test_redact_text_hides_common_secret_shapes(self):
        text = (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789 "
            "https://example.test/callback?access_token=super-secret&state=keep-private "
            + ("A" * 70)
        )
        redacted = support.redact_text(text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", redacted)
        self.assertNotIn("super-secret", redacted)
        self.assertNotIn("keep-private", redacted)
        self.assertNotIn("A" * 70, redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_support_bundle_contains_manifest_and_sanitized_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temp}, clear=False):
                support.log_event(
                    "unit_test",
                    authorization="Bearer should-never-leak",
                    password="plain-secret",
                )
                (support.support_root() / "manual.log").write_text(
                    "Bearer another-secret\nhttps://example.test/?token=query-secret",
                    encoding="utf-8",
                )
                bundle = support.create_support_bundle()
                self.assertTrue(bundle.is_file())
                with zipfile.ZipFile(bundle) as archive:
                    names = set(archive.namelist())
                    self.assertIn("diagnostics/manifest.json", names)
                    self.assertIn("README.txt", names)
                    manifest = json.loads(archive.read("diagnostics/manifest.json"))
                    self.assertEqual(manifest["app"], "RCMTool")
                    self.assertEqual(manifest["session_id"], support.SESSION_ID)
                    merged_logs = "\n".join(
                        archive.read(name).decode("utf-8", errors="replace")
                        for name in names
                        if name.startswith("logs/")
                    )
                    self.assertNotIn("should-never-leak", merged_logs)
                    self.assertNotIn("plain-secret", merged_logs)
                    self.assertNotIn("another-secret", merged_logs)
                    self.assertNotIn("query-secret", merged_logs)

    def test_upload_requires_configured_endpoint(self):
        with mock.patch.dict(os.environ, {"RCM_SUPPORT_UPLOAD_URL": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                support.upload_support_bundle()


if __name__ == "__main__":
    unittest.main()
