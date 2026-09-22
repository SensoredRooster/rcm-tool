import unittest

from injection import apply_injection, classify_injection, is_injected


class InjectionTests(unittest.TestCase):
    def test_hf_sine_moves_lx_only(self):
        raw = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0}
        out = apply_injection(raw, 0.008, "injected-hf-sine")
        self.assertNotAlmostEqual(out["lx"], 0.0)
        self.assertEqual(out["ry"], 0.0)
        self.assertTrue(is_injected("injected-hf-sine"))

    def test_slow_sine_period(self):
        a = apply_injection({"lx": 0.0}, 0.0, "injected-slow-sine")["lx"]
        b = apply_injection({"lx": 0.0}, 1.0, "injected-slow-sine")["lx"]
        self.assertAlmostEqual(a, 0.0)
        self.assertAlmostEqual(b, 0.0, places=3)

    def test_classify_hf_pass(self):
        label, reasons = classify_injection(
            "injected-hf-sine",
            {"lx_high_freq_rms": 0.04, "ry_high_freq_rms": 0.001},
        )
        self.assertEqual(label, "pass")
        self.assertTrue(reasons)

    def test_classify_slow_leak_is_review(self):
        label, _ = classify_injection(
            "injected-slow-sine",
            {"lx_high_freq_rms": 0.04, "ry_high_freq_rms": 0.001},
        )
        self.assertEqual(label, "review")


if __name__ == "__main__":
    unittest.main()
