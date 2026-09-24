import math
import unittest

import controller_integrity as app
import rc_filter


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

    def test_dualsense_hid_report_parses_centered_sticks(self):
        reader = app.HIDGamepad(info={"vendor_id": 0x054C, "product_string": "Wireless Controller"})
        sample = reader._parse_report([0x01, 128, 128, 128, 128, 0, 0])
        self.assertIsNotNone(sample)
        self.assertAlmostEqual(sample["lx"], 0.0, places=2)
        self.assertAlmostEqual(sample["ly"], 0.0, places=2)
        self.assertAlmostEqual(sample["rx"], 0.0, places=2)
        self.assertAlmostEqual(sample["ry"], 0.0, places=2)

    def test_hid_reader_does_not_replay_cached_sample_when_no_report_arrives(self):
        class EmptyDevice:
            @staticmethod
            def read(_size):
                return []

        reader = app.HIDGamepad()
        reader.device = EmptyDevice()
        reader.last_sample = {"lx": 0.25, "ly": 0.0, "rx": 0.0, "ry": 0.0}
        self.assertIsNone(reader.read())

    def test_rc_alpha_is_frame_rate_independent(self):
        tau = 0.05
        alpha_4ms = rc_filter.RCLowPassFilter.alpha(0.004, tau)
        expected = 1.0 - math.exp(-0.004 / tau)
        self.assertAlmostEqual(alpha_4ms, expected)
        self.assertGreater(rc_filter.RCLowPassFilter.alpha(0.008, tau), alpha_4ms)
        self.assertEqual(rc_filter.RCLowPassFilter.alpha(0.0, tau), 0.0)

    def test_rc_filter_tracks_dc_and_rejects_step_instantly(self):
        values = [0.0] * 10 + [1.0] * 10
        timestamps = [index * 0.004 for index in range(len(values))]
        filtered = rc_filter.rc_filter_series(values, timestamps, tau_seconds=0.05)
        self.assertAlmostEqual(filtered[0], 0.0)
        self.assertLess(filtered[10], 0.2)
        self.assertGreater(filtered[-1], 0.5)

    def test_high_freq_rms_is_residual_after_rc_trend(self):
        timestamps = [index * 0.004 for index in range(250)]
        raw = [
            0.6 * math.sin(2.0 * math.pi * 0.5 * t) + 0.05 * math.sin(2.0 * math.pi * 40.0 * t)
            for t in timestamps
        ]
        filtered = rc_filter.rc_filter_series(raw, timestamps, tau_seconds=0.05)
        rms = rc_filter.residual_rms(raw, filtered)
        self.assertGreater(rms, 0.01)
        self.assertLess(rms, 0.10)

    def test_rc_filter_metrics_json_keys(self):
        samples = [
            {"t_ms": 0.0, "lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0},
            {"t_ms": 4.0, "lx": 0.02, "ly": -0.01, "rx": 0.0, "ry": 0.0},
            {"t_ms": 8.0, "lx": -0.02, "ly": 0.01, "rx": 0.0, "ry": 0.0},
        ]
        metrics = rc_filter.build_rc_filter_metrics(samples, tau_seconds=0.05)
        self.assertEqual(metrics["tau_seconds"], 0.05)
        self.assertIn("lx_high_freq_rms", metrics)
        self.assertIn("ly_high_freq_rms", metrics)
        self.assertIn("rx_high_freq_rms", metrics)
        self.assertIn("ry_high_freq_rms", metrics)
        self.assertGreater(metrics["lx_high_freq_rms"], 0.0)


if __name__ == "__main__":
    unittest.main()
