import unittest

from hid_signal import build_hid_signal_metrics, compare_hid_signal


def samples(n=40, step=1.0, noise=0.0):
    out = []
    for i in range(n):
        out.append(
            {
                "t_ms": i * 8.0,
                "lx": step * 0.0 + (0.01 if noise and i % 2 else 0.0),
                "ly": 0.0,
                "rx": 0.0,
                "ry": 0.0,
            }
        )
    return out


class HidSignalTests(unittest.TestCase):
    def test_rate_near_125hz(self):
        metrics = build_hid_signal_metrics(samples())
        self.assertGreater(metrics["sample_rate_hz"], 100.0)
        self.assertEqual(metrics["sample_count"], 40)

    def test_compare_flags_unique_levels(self):
        before = build_hid_signal_metrics(samples(noise=0.0))
        after_samples = []
        for i in range(40):
            after_samples.append(
                {
                    "t_ms": i * 8.0,
                    "lx": (i % 20) * 0.01,
                    "ly": 0.0,
                    "rx": 0.0,
                    "ry": 0.0,
                }
            )
        after = build_hid_signal_metrics(after_samples)
        cmp = compare_hid_signal(before, after)
        self.assertTrue(cmp["quantization_changed"])


if __name__ == "__main__":
    unittest.main()
