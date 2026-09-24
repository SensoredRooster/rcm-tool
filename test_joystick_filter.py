import unittest

from joystick_filter import DualStick, JoystickFilter, PRESETS
from signal_lab.analysis import timing_metrics, window_rate_hz


class JoystickFilterTests(unittest.TestCase):
    def test_presets_exist_for_user_pads(self):
        for key in ("marius_midas", "marius_tmr", "marius_alps", "tarantula_8k", "gamesir_8k"):
            self.assertIn(key, PRESETS)
            self.assertEqual(PRESETS[key]["sample_hz"], 8000.0)

    def test_aliases(self):
        self.assertEqual(JoystickFilter.from_preset("marius").preset_name, "marius_midas")
        self.assertEqual(JoystickFilter.from_preset("tarantula").preset_name, "tarantula_8k")
        self.assertEqual(JoystickFilter.from_preset("gamesir").preset_name, "gamesir_8k")

    def test_rest_inside_deadzone_snaps_to_zero(self):
        filt = JoystickFilter.from_preset("marius_midas")
        x, y = filt.process(0.004, -0.003, dt=1.0 / 8000.0)
        self.assertEqual((x, y), (0.0, 0.0))

    def test_rate_aware_alpha_uses_seconds_not_samples(self):
        slow = JoystickFilter.from_preset("marius_midas")
        fast = JoystickFilter.from_preset("marius_midas")
        sx, _ = slow.process(0.5, 0.0, dt=0.001)
        fx, _ = fast.process(0.5, 0.0, dt=0.000125)
        self.assertGreater(sx, 0.0)
        self.assertGreater(fx, 0.0)

    def test_rest_calibration_sets_center_and_hysteresis(self):
        filt = JoystickFilter.from_preset("marius_midas")
        xs = [0.010 + ((i % 3) - 1) * 0.0004 for i in range(64)]
        ys = [-0.006 + ((i % 3) - 1) * 0.0003 for i in range(64)]
        report = filt.calibrate_from_rest(xs, ys)
        self.assertAlmostEqual(report["center_x"], sum(xs) / len(xs), places=6)
        self.assertGreater(filt.hysteresis, 0.0004)
        self.assertLessEqual(filt.hysteresis, 0.012)

    def test_dual_stick_processes_four_axes(self):
        pads = DualStick("tarantula")
        out = pads.process(0.0, 0.0, 0.0, 0.0)
        self.assertEqual(out, (0.0, 0.0, 0.0, 0.0))

    def test_window_hz_is_measured_from_timestamps(self):
        interval_ns = int(1e9 / 8000)
        stamps = [i * interval_ns for i in range(2001)]
        hz = window_rate_hz(stamps, window_s=0.25)
        self.assertIsNotNone(hz)
        self.assertAlmostEqual(hz, 8000.0, places=3)
        session = timing_metrics(stamps)
        self.assertAlmostEqual(session.effective_rate_hz, 8000.0, places=3)

    def test_window_hz_unavailable_without_samples(self):
        self.assertIsNone(window_rate_hz([]))
        self.assertIsNone(window_rate_hz([0]))

    def test_advertised_8k_is_not_used_as_measured_rate(self):
        interval_ns = int(1e9 / 1000)
        stamps = [i * interval_ns for i in range(251)]
        self.assertAlmostEqual(window_rate_hz(stamps, 0.25), 1000.0, places=3)
        self.assertAlmostEqual(timing_metrics(stamps).effective_rate_hz, 1000.0, places=3)


if __name__ == "__main__":
    unittest.main()
