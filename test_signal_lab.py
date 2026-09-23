import tempfile
from pathlib import Path
import unittest

from signal_lab.analysis import align_nearest, oscillator_metrics, pearson_correlation, timing_metrics
from signal_lab.instruments import SafetyLimits, SimulatedInstrument, VisaScpiMeasurementInstrument
from signal_lab.simulation import GamepadSimulator, SimulatedGamepadConfig, OscillatorSimulator, SimulatedOscillatorConfig, stimulus_response
from signal_lab.storage import LabDatabase
from signal_lab.reporting import write_html_report
from signal_lab.sweep import make_sweep


class SignalLabTests(unittest.TestCase):
    def test_timing_metrics_1000hz(self):
        stamps = [i * 1_000_000 for i in range(1001)]
        m = timing_metrics(stamps, expected_interval_ms=1.0)
        self.assertAlmostEqual(m.effective_rate_hz, 1000.0, places=6)
        self.assertAlmostEqual(m.mean_interval_ms, 1.0, places=9)
        self.assertAlmostEqual(m.rms_deviation_ms, 0.0, places=9)
        self.assertEqual(m.late_reports, 0)

    def test_simulator_is_deterministic(self):
        cfg = SimulatedGamepadConfig(rate_hz=1000, jitter_ms=0.05, seed=9)
        a, b = GamepadSimulator(cfg), GamepadSimulator(cfg)
        self.assertEqual([a.next_timestamp_ns() for _ in range(50)], [b.next_timestamp_ns() for _ in range(50)])

    def test_oscillator_ppm(self):
        m = oscillator_metrics([11_999_988.0] * 20, 12_000_000.0)
        self.assertAlmostEqual(m.frequency_error_hz, -12.0, places=6)
        self.assertAlmostEqual(m.frequency_error_ppm, -1.0, places=6)

    def test_oscillator_simulator(self):
        sim = OscillatorSimulator(SimulatedOscillatorConfig(nominal_frequency_hz=10_000_000, ppm_offset=2.0, random_jitter_ppm=0, periodic_jitter_ppm=0, drift_ppm_per_second=0))
        self.assertAlmostEqual(sim.next_frequency_hz(), 10_000_020.0, places=3)

    def test_correlation(self):
        self.assertAlmostEqual(pearson_correlation([1,2,3,4],[2,4,6,8]), 1.0, places=9)

    def test_timestamp_alignment(self):
        left, right = align_nearest(
            [100, 200, 300], [1.0, 2.0, 3.0],
            [110, 290], [10.0, 30.0], max_delta_ns=25,
        )
        self.assertEqual(left, [1.0, 3.0])
        self.assertEqual(right, [10.0, 30.0])

    def test_safety_limits_reject_excess(self):
        limits = SafetyLimits(max_frequency_hz=1000, max_amplitude_vpp=1, max_abs_offset_v=.5)
        with self.assertRaises(ValueError):
            limits.validate(frequency_hz=2000, amplitude_vpp=.1, offset_v=0)
        instrument = SimulatedInstrument()
        self.assertFalse(instrument.output_enabled())

    def test_measurement_role_cannot_enable_output(self):
        measurement = VisaScpiMeasurementInstrument.__new__(VisaScpiMeasurementInstrument)
        with self.assertRaises(RuntimeError):
            measurement.set_output(True)

    def test_sweep_log(self):
        plan = make_sweep(100, 10000, 3, logarithmic=True)
        self.assertEqual(len(plan), 3)
        self.assertAlmostEqual(plan[1].frequency_hz, 1000, places=6)

    def test_amplitude_grid_sweep(self):
        plan = make_sweep(
            100, 1000, 2,
            amplitude_start_vpp=0.1,
            amplitude_stop_vpp=0.3,
            amplitude_steps=3,
        )
        self.assertEqual(len(plan), 6)
        self.assertEqual(sorted({round(x.amplitude_vpp, 3) for x in plan}), [0.1, 0.2, 0.3])

    def test_simulated_stimulus_has_known_response(self):
        quiet = stimulus_response(1000, 0.0)
        driven = stimulus_response(1000, 0.5)
        off_resonance = stimulus_response(50, 0.5)
        self.assertEqual(quiet.gamepad_extra_jitter_ms, 0.0)
        self.assertGreater(driven.gamepad_extra_jitter_ms, off_resonance.gamepad_extra_jitter_ms)
        self.assertGreater(driven.oscillator_extra_ppm, 0.0)

    def test_engineering_report_contains_plots_sweep_and_timeline(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "report.html"
            write_html_report(
                target,
                title="Lab Report",
                controller_metrics={"rate_hz": 1000.0},
                oscillator_metrics={"frequency_hz": 12_000_000.0},
                metadata={"mode": "simulation"},
                limitations=["host timing is not bus timing"],
                plots={"Timing": [1.0, 1.1, 0.9]},
                sweep_points=[(100.0, 0.1, 0.02, 1.5)],
                timeline=[{"timestamp_ns": 1, "event_type": "capture_started", "payload": {}}],
            )
            html = target.read_text(encoding="utf-8")
            self.assertIn("Sweep response", html)
            self.assertIn("Experiment timeline", html)
            self.assertIn("<svg", html)

    def test_sqlite_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            db = LabDatabase(Path(td)/"lab.sqlite3")
            sid = db.create_session("test","simulation","x")
            db.add_controller_sample(
                sid,1,
                {"lx":0,"ly":0,"rx":0,"ry":0,"lt":0,"rt":0,"buttons":5,"dpad_x":1.0,"dpad_y":0.0},
                source="sim",
            )
            db.add_oscillator_sample(sid,1,12_000_000,source="sim",quality="simulated")
            db.add_event(sid,1,"baseline_started")
            db.flush()
            s = db.session_summary(sid)
            self.assertEqual(s["controller_samples"],1)
            self.assertEqual(s["oscillator_samples"],1)
            self.assertEqual(s["events"],1)
            sessions = db.list_sessions()
            self.assertEqual(sessions[0]["id"], sid)
            self.assertEqual(sessions[0]["events"], 1)
            events = db.list_events(sid)
            self.assertEqual(events[0]["event_type"], "baseline_started")
            json_path = db.export_json(sid, Path(td) / "export.json")
            csv_path = db.export_controller_csv(sid, Path(td) / "export.csv")
            exported = json_path.read_text(encoding="utf-8")
            self.assertIn('"buttons": 5', exported)
            self.assertIn("extra_json", csv_path.read_text(encoding="utf-8"))
            db.close()


if __name__ == "__main__":
    unittest.main()
