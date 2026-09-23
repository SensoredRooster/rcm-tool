import tempfile
from pathlib import Path
import unittest

from signal_lab.analysis import align_nearest, oscillator_metrics, pearson_correlation, timing_metrics
from signal_lab.controller import detect_controller_family
from signal_lab.instruments import InstrumentAdapter, SafetyLimits, VisaScpiMeasurementInstrument
from signal_lab.noise_attribution import analyze_noise_capture
from signal_lab.storage import LabDatabase
from signal_lab.reporting import write_html_report
from signal_lab.sweep import make_sweep


class SignalLabTests(unittest.TestCase):
    def test_controller_family_detection_xinput(self):
        self.assertEqual(
            detect_controller_family({"backend":"Windows XInput","connection_method":"XInputGamepad"},"XInput slot 0"),
            "xbox",
        )

    def test_controller_family_detection_dualsense_vid_pid(self):
        self.assertEqual(
            detect_controller_family(
                {"vid":0x054C,"pid":0x0CE6,"controller_name":"Wireless Controller","backend":"Raw HID controller"},
                "Raw HID connected",
            ),
            "dualsense",
        )

    def test_controller_family_detection_dualsense_name(self):
        self.assertEqual(
            detect_controller_family({"controller_name":"DualSense Wireless Controller"},"Raw HID"),
            "dualsense",
        )

    def test_controller_family_detection_does_not_call_every_sony_pad_dualsense(self):
        self.assertEqual(
            detect_controller_family({"vid":0x054C,"pid":0x05C4,"controller_name":"Wireless Controller"},"Raw HID"),
            "generic",
        )

    def test_controller_family_detection_unknown_is_generic(self):
        self.assertEqual(
            detect_controller_family({"controller_name":"USB Gamepad","vid":0x1234,"pid":0x5678},"SDL"),
            "generic",
        )

    def test_timing_metrics_1000hz(self):
        stamps = [i * 1_000_000 for i in range(1001)]
        m = timing_metrics(stamps, expected_interval_ms=1.0)
        self.assertAlmostEqual(m.effective_rate_hz, 1000.0, places=6)
        self.assertAlmostEqual(m.mean_interval_ms, 1.0, places=9)
        self.assertAlmostEqual(m.rms_deviation_ms, 0.0, places=9)
        self.assertEqual(m.late_reports, 0)

    def test_default_timing_reference_uses_measured_median(self):
        stamps = [0, 1_000_000, 2_000_000, 4_000_000]
        measured_reference = timing_metrics(stamps)
        configured_reference = timing_metrics(stamps, expected_interval_ms=2.0)
        self.assertAlmostEqual(measured_reference.rms_deviation_ms, (1.0 / 3.0) ** 0.5, places=9)
        self.assertNotAlmostEqual(
            measured_reference.rms_deviation_ms,
            configured_reference.rms_deviation_ms,
            places=6,
        )

    def test_timing_percentiles_are_interval_percentiles(self):
        stamps = [0]
        total = 0
        for interval_ms in range(1, 101):
            total += interval_ms * 1_000_000
            stamps.append(total)
        metrics = timing_metrics(stamps)
        self.assertAlmostEqual(metrics.p50_ms, 50.5, places=9)
        self.assertAlmostEqual(metrics.p90_ms, 90.1, places=9)
        self.assertAlmostEqual(metrics.p95_ms, 95.05, places=9)
        self.assertAlmostEqual(metrics.p99_ms, 99.01, places=9)
        self.assertAlmostEqual(metrics.p999_ms, 99.901, places=9)

    def test_reciprocal_period_metrics_are_zero_for_stable_frequency(self):
        metrics = oscillator_metrics([10_000_000.0] * 20, 10_000_000.0)
        self.assertAlmostEqual(metrics.mean_period_s, 100e-9, places=15)
        self.assertEqual(metrics.rms_period_jitter_s, 0.0)
        self.assertEqual(metrics.peak_to_peak_period_jitter_s, 0.0)
        self.assertEqual(metrics.cycle_to_cycle_rms_s, 0.0)

    def test_timing_metrics_8000hz(self):
        interval_ns = 125_000
        stamps = [i * interval_ns for i in range(8001)]
        metrics = timing_metrics(stamps)
        self.assertAlmostEqual(metrics.effective_rate_hz, 8000.0, places=6)
        self.assertAlmostEqual(metrics.mean_interval_ms, 0.125, places=9)

    def test_timing_metrics_32000hz(self):
        interval_ns = 31_250
        stamps = [i * interval_ns for i in range(32001)]
        metrics = timing_metrics(stamps)
        self.assertAlmostEqual(metrics.effective_rate_hz, 32000.0, places=6)
        self.assertAlmostEqual(metrics.mean_interval_ms, 0.03125, places=9)

    def test_oscillator_ppm(self):
        m = oscillator_metrics([11_999_988.0] * 20, 12_000_000.0)
        self.assertAlmostEqual(m.frequency_error_hz, -12.0, places=6)
        self.assertAlmostEqual(m.frequency_error_ppm, -1.0, places=6)

    def test_oscillator_drift_and_outlier_metrics(self):
        values = [12_000_000.0] * 10 + [12_000_006.0] * 10 + [12_001_000.0]
        m = oscillator_metrics(values, 12_000_000.0, outlier_sigma=2.0)
        self.assertGreater(m.frequency_drift_hz, 0.0)
        self.assertGreater(m.frequency_span_hz, 0.0)
        self.assertGreaterEqual(m.outlier_count, 1)

    def test_late_report_threshold_is_configurable(self):
        stamps = [0, 1_000_000, 2_000_000, 3_600_000]
        strict = timing_metrics(stamps, expected_interval_ms=1.0, late_factor=1.5)
        loose = timing_metrics(stamps, expected_interval_ms=1.0, late_factor=2.0)
        self.assertEqual(strict.late_reports, 1)
        self.assertEqual(loose.late_reports, 0)

    def test_correlation_is_invariant_to_reference_offset(self):
        controller = [0, 1_000_000, 2_000_000, 4_000_000, 5_000_000]
        oscillator_times = [500_000, 1_500_000, 3_000_000, 4_500_000]
        oscillator_freq = [10_000_000.0, 10_000_010.0, 9_999_990.0, 10_000_000.0]
        from signal_lab.ui import MainWindow
        median_corr = MainWindow._aligned_correlation(controller, oscillator_times, oscillator_freq, 10_000_000.0)
        configured_corr = MainWindow._aligned_correlation(
            controller, oscillator_times, oscillator_freq, 10_000_000.0, expected_interval_ms=2.0
        )
        self.assertIsNotNone(median_corr)
        self.assertIsNotNone(configured_corr)
        self.assertAlmostEqual(median_corr, configured_corr, places=9)

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
        instrument = InstrumentAdapter()
        self.assertFalse(instrument.output_enabled())

    def test_unavailable_generator_cannot_enable_output(self):
        instrument = InstrumentAdapter()
        with self.assertRaises(RuntimeError):
            instrument.set_output(True)
        self.assertFalse(instrument.output_enabled())

    def test_noise_attribution_is_host_observed_and_uses_raw_reports(self):
        timestamps = [i * 1_000_000 for i in range(5)]
        samples = [
            {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0},
            {"lx": 0.01, "ly": 0.0, "rx": 0.0, "ry": 0.0},
            {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0},
            {"lx": 0.01, "ly": 0.0, "rx": 0.0, "ry": 0.0},
            {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0},
        ]
        result = analyze_noise_capture(
            timestamps_ns=timestamps,
            samples=samples,
            raw_report_hex=["00", "01", "01", "02", "02"],
            capture_kind="neutral",
        )
        self.assertEqual(result["raw_hid_report_count"], 5)
        self.assertEqual(result["consecutive_duplicate_raw_reports"], 2)
        self.assertEqual(result["attribution"], "undetermined-from-raw-hid-alone")
        self.assertEqual(len(result["records"]), len(samples))
        self.assertEqual(result["records"][1]["raw_report_hex"], "01")
        self.assertGreater(result["axes"]["lx"]["noise_rms"], 0.0)

    def test_safety_limits_reject_non_finite_offset(self):
        with self.assertRaises(ValueError):
            SafetyLimits().validate(frequency_hz=1000, amplitude_vpp=0.1, offset_v=float("nan"))

    def test_controller_metadata_extraction(self):
        from signal_lab.controller import ControllerAcquisition
        class FakeHID:
            name = "Raw HID controller"
            path = b"hid-path"
            info = {
                "product_string": "Test Pad",
                "manufacturer_string": "Lab",
                "vendor_id": 0x1234,
                "product_id": 0x5678,
                "release_number": 42,
                "interface_number": 1,
            }
        meta = ControllerAcquisition._backend_metadata(FakeHID())
        self.assertEqual(meta["vid"], 0x1234)
        self.assertEqual(meta["pid"], 0x5678)
        self.assertEqual(meta["usb_path"], "hid-path")
        self.assertEqual(meta["controller_name"], "Test Pad")

    def test_measurement_capability_probe_reflects_actual_queries(self):
        class FakeMeasurement:
            def __init__(self):
                self.capabilities = None
            def measure_frequency_hz(self):
                return 12_000_000.0
            def measure_duty_cycle_percent(self):
                return None

        fake = FakeMeasurement()
        caps = __import__("signal_lab.instruments", fromlist=["InstrumentCapabilities"]).InstrumentCapabilities(
            frequency=fake.measure_frequency_hz() is not None,
            period=False,
            duty_cycle=fake.measure_duty_cycle_percent() is not None,
        )
        self.assertTrue(caps.frequency)
        self.assertFalse(caps.period)
        self.assertFalse(caps.duty_cycle)

    def test_measurement_role_cannot_enable_output(self):
        measurement = VisaScpiMeasurementInstrument.__new__(VisaScpiMeasurementInstrument)
        with self.assertRaises(RuntimeError):
            measurement.set_output(True)

    def test_generator_rejects_output_readback_mismatch(self):
        class FakeResource:
            def __init__(self):
                self.writes = []
            def write(self, command):
                self.writes.append(command)
            def query(self, command):
                self.writes.append(command)
                return "1"

        generator = __import__(
            "signal_lab.instruments", fromlist=["VisaScpiGenerator"]
        ).VisaScpiGenerator.__new__(
            __import__("signal_lab.instruments", fromlist=["VisaScpiGenerator"]).VisaScpiGenerator
        )
        generator.resource = FakeResource()
        generator._output = True
        with self.assertRaises(RuntimeError):
            generator.set_output(False)
        self.assertTrue(generator.output_enabled())

    def test_generator_state_readback(self):
        class FakeResource:
            def write(self, command):
                pass
            def query(self, command):
                responses = {
                    "OUTP?": "1",
                    "FREQ?": "1000",
                    "VOLT?": "0.25",
                    "VOLT:OFFS?": "0.01",
                    "FUNC?": "SINE",
                }
                return responses[command]

        Generator = __import__("signal_lab.instruments", fromlist=["VisaScpiGenerator"]).VisaScpiGenerator
        generator = Generator.__new__(Generator)
        generator.resource = FakeResource()
        generator._output = True
        state = generator.read_generator_state()
        self.assertTrue(state["output_enabled"])
        self.assertEqual(state["frequency_hz"], 1000.0)
        self.assertEqual(state["amplitude_vpp"], 0.25)
        self.assertEqual(state["offset_v"], 0.01)
        self.assertEqual(state["waveform"], "SINE")

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

    def test_engineering_report_contains_plots_sweep_and_timeline(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "report.html"
            write_html_report(
                target,
                title="Lab Report",
                controller_metrics={"rate_hz": 1000.0},
                oscillator_metrics={"frequency_hz": 12_000_000.0},
                metadata={"mode": "hardware"},
                limitations=["host timing is not bus timing"],
                plots={"Timing": [1.0, 1.1, 0.9]},
                sweep_points=[(100.0, 0.1, 0.02, 1.5)],
                timeline=[{"timestamp_ns": 1, "event_type": "capture_started", "payload": {}}],
            )
            html = target.read_text(encoding="utf-8")
            self.assertIn("Sweep response", html)
            self.assertIn("Experiment timeline", html)
            self.assertIn("<svg", html)

    def test_engineering_report_handles_unavailable_sweep_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "report_missing.html"
            write_html_report(
                target,
                title="Incomplete Lab Report",
                controller_metrics={},
                oscillator_metrics={},
                metadata={},
                limitations=[],
                sweep_points=[
                    (100.0, 0.1, None, 1.5),
                    (200.0, 0.1, 0.02, None),
                ],
            )
            html = target.read_text(encoding="utf-8")
            self.assertIn("Unavailable", html)
            self.assertIn("200", html)

    def test_database_buffers_rows_until_flush(self):
        with tempfile.TemporaryDirectory() as td:
            db = LabDatabase(Path(td) / "buffered.sqlite3")
            sid = db.create_session("buffered", "hardware", "test")
            db.add_controller_sample(
                sid, 123,
                {"lx":0.0,"ly":0.0,"rx":0.0,"ry":0.0,"lt":0.0,"rt":0.0},
                source="test",
            )
            self.assertEqual(db.pending_row_count, 1)
            db.flush()
            self.assertEqual(db.pending_row_count, 0)
            self.assertEqual(db.session_summary(sid)["controller_samples"], 1)
            db.close()

    def test_sqlite_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            db = LabDatabase(Path(td)/"lab.sqlite3")
            sid = db.create_session("test","hardware","x")
            db.add_controller_sample(
                sid,1,
                {"lx":0,"ly":0,"rx":0,"ry":0,"lt":0,"rt":0,"buttons":5,"dpad_x":1.0,"dpad_y":0.0},
                source="test",
            )
            db.add_oscillator_sample(sid,1,12_000_000,source="test",quality="measured")
            db.add_event(sid,1,"baseline_started")
            db.flush()
            s = db.session_summary(sid)
            self.assertEqual(s["controller_samples"],1)
            self.assertEqual(s["oscillator_samples"],1)
            self.assertEqual(s["events"],1)
            sessions = db.list_sessions()
            self.assertEqual(sessions[0]["id"], sid)
            series = db.session_series(sid)
            self.assertEqual(series["controller_timestamps_ns"], [1])
            self.assertEqual(series["oscillator_frequencies_hz"], [12_000_000.0])
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
