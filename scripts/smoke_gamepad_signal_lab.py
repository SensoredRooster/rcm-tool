"""Headless integration smoke for the full no-hardware workflow."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from signal_lab.reporting import write_html_report
from signal_lab.ui import MainWindow


def pump(app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    QSettings("SensoredRooster", "GamepadSignalLab").setValue("welcomed", True)
    window = MainWindow()
    assert window.simulation_mode
    assert not window.instrument.output_enabled()

    window._start_capture()
    pump(app, 0.35)
    window._refresh_ui()
    assert len(window.controller_ts) > 20
    assert len(window.osc_freq) > 2
    assert window.current_timing.sample_count > 20
    assert window.current_osc.sample_count > 2

    window.baseline_seconds.setValue(5)
    window._start_baseline()
    pump(app, 0.15)
    window.baseline_deadline = time.monotonic() - 0.01
    window._sample_tick()
    window._refresh_ui()
    assert window.last_baseline is not None
    window._set_reference_baseline()
    assert window.reference_baseline is not None

    window.sweep_start.setValue(100.0)
    window.sweep_stop.setValue(1000.0)
    window.sweep_steps.setValue(2)
    window.amp_start.setValue(0.05)
    window.amp_stop.setValue(0.05)
    window.amp_steps.setValue(1)
    window.settle_ms.setValue(0)
    window.dwell_ms.setValue(100)
    window.sweep_reps.setValue(1)
    window.sweep_enable_output.setChecked(False)
    window._start_sweep()
    deadline = time.monotonic() + 3.0
    while window.sweep_active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    window._refresh_ui()
    assert not window.sweep_active
    assert len(window.sweep_results) == 2
    assert len(window.sweep_metric_rows) == 2

    window._add_user_marker()
    assert any(event == "user_marker" for _, event, _ in window.events)

    window.db.flush()
    assert window.session_id
    summary = window.db.session_summary(window.session_id)
    assert summary["controller_samples"] > 0
    assert summary["oscillator_samples"] > 0
    assert summary["events"] > 0

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        json_path = window.db.export_json(window.session_id, root / "session.json")
        csv_path = window.db.export_controller_csv(window.session_id, root / "controller.csv")
        report_path = write_html_report(
            root / "report.html",
            title="Gamepad Signal Lab Smoke Report",
            controller_metrics=window.current_timing.to_dict(),
            oscillator_metrics=window.current_osc.to_dict(),
            metadata={"mode": "simulation", "session_id": window.session_id},
            limitations=["integration smoke"],
            sweep_points=window.sweep_results,
            timeline=window.db.list_events(window.session_id),
        )
        assert json_path.stat().st_size > 0
        assert csv_path.stat().st_size > 0
        assert report_path.stat().st_size > 0

    window.instrument.set_output(True)
    assert window.instrument.output_enabled()
    window._emergency_off()
    assert not window.instrument.output_enabled()

    window.close()
    app.processEvents()
    print("Gamepad Signal Lab full simulation smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
