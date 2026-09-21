import unittest

import controller_integrity as app


class ControllerIntegrityTests(unittest.TestCase):
    def test_axis_metrics_capture_noise_and_threshold_crossings(self):
        metrics = app.axis_metrics([0.0, 0.01, -0.01, 0.03, 0.0])
        self.assertEqual(metrics.samples, 5)
        self.assertAlmostEqual(metrics.peak_to_peak, 0.04)
        self.assertEqual(metrics.deadzone_crossings, 2)
        self.assertAlmostEqual(metrics.nonzero_stationary_percent, 20.0)

    def test_empty_capture_is_unsupported(self):
        empty = app.AxisMetrics(0, 0, 0, 0, 0, 0, 0, 0)
        result = app.TestResult(
            test_name="test",
            started_at_utc="now",
            duration_seconds=1,
            sample_rate_hz=0,
            backend="test",
            axes={axis: empty for axis in ("lx", "ly", "rx", "ry")},
            classification="pending",
            review_reasons=[],
        )
        classification, reasons = app.classify(result)
        self.assertEqual(classification, "unsupported")
        self.assertTrue(reasons)

    def test_hash_is_stable_for_same_payload(self):
        payload = {"b": 2, "a": [1, 2, 3]}
        self.assertEqual(app.sha256_payload(payload), app.sha256_payload(payload))


if __name__ == "__main__":
    unittest.main()
