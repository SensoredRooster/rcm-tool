"""Headless integration smoke for the hardware-only workflow."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from signal_lab.reporting import write_html_report
from signal_lab.ui import NAV, MainWindow


def pump(app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    QSettings("SensoredRooster", "GamepadSignalLab").setValue("welcomed", True)
    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    pump(app, 0.25)

    assert not hasattr(window, "simulation_mode")
    if window.controller_source_kind == "raw_hid" and window.controller_source_path:
        assert window.controller_acquisition is not None
    else:
        assert window.controller_acquisition is None
        assert all(not button.isEnabled() for button in window.guided_test_buttons)
    assert window.measurement_instrument is None
    assert window.instrument.identify() == "No generator connected"
    assert not window.instrument.output_enabled()
    assert not list(window.controller_sources) or all(
        quality != "simulated" for _, quality in window.controller_sources
    )
    assert not window.osc_freq

    has_live_hid = window._has_measured_raw_hid()
    if has_live_hid:
        window._start_capture()
        pump(app, 0.25)
        window._refresh_ui()
        assert window.session_id
        events = window.db.list_events(window.session_id)
        capture_event = next(event for event in events if event["event_type"] == "capture_started")
        assert capture_event["payload"]["mode"] == "hardware"

        window.baseline_seconds.setValue(5)
        window._start_baseline()
        window.baseline_deadline = time.monotonic() - 0.01
        window._sample_tick()
        window._refresh_ui()
        assert window.last_baseline is not None
    else:
        # A headless CI runner normally has no physical Raw HID controller.
        # Verify that the app stays intentionally blank and never fabricates data.
        assert window.session_id is None
        assert not window.capture_active
        assert all(not button.isEnabled() for button in window.guided_test_buttons)
        assert not window.controller_ts
        assert not window.controller_samples
        window._refresh_ui()

    # Exercise completion even on CI without a connected controller. This
    # must not depend on oscillator widgets removed from the controller UI.
    if not has_live_hid:
        window._finish_baseline()
        assert window.last_baseline["timing"]["sample_count"] == 0
        assert window.last_baseline["oscillator"]["sample_count"] == 0

    preview_root = ROOT / "artifacts" / "ui-preview"
    preview_root.mkdir(parents=True, exist_ok=True)
    assert NAV == ["Test", "Results", "Support"]
    for page_name, filename in (
        ("Test", "test.png"),
        ("Results", "results.png"),
        ("Support", "support.png"),
    ):
        window._navigate(NAV.index(page_name))
        window._refresh_ui()
        pump(app, 0.08)
        image = window.grab()
        assert not image.isNull()
        assert image.save(str(preview_root / filename), "PNG")

    window.db.flush()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        timeline = []
        if window.session_id:
            json_path = window.db.export_json(window.session_id, root / "session.json")
            csv_path = window.db.export_controller_csv(window.session_id, root / "controller.csv")
            assert json_path.stat().st_size > 0
            assert csv_path.stat().st_size > 0
            timeline = window.db.list_events(window.session_id)
        report_path = write_html_report(
            root / "report.html",
            title="RcmTool Hardware Report",
            controller_metrics=window.current_timing.to_dict(),
            oscillator_metrics=window.current_osc.to_dict(),
            metadata={"mode": "hardware", "session_id": window.session_id},
            limitations=["No physical controller or frequency instrument was connected during this smoke run."]
            if not has_live_hid else [],
            timeline=timeline,
        )
        assert report_path.stat().st_size > 0

    window._emergency_off()
    assert not window.instrument.output_enabled()
    window.close()
    app.processEvents()
    print("RcmTool hardware-only smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
