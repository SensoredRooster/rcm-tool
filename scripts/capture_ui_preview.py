"""Capture readable native-Windows screenshots of the real Gamepad Signal Lab UI."""
from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

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
    window.resize(1440, 900)
    window.show()
    pump(app, 0.4)

    window._start_capture()
    pump(app, 0.6)
    window._refresh_ui()

    window.baseline_seconds.setValue(5)
    window._start_baseline()
    pump(app, 0.2)
    window.baseline_deadline = time.monotonic() - 0.01
    window._sample_tick()
    window._refresh_ui()
    window._set_reference_baseline()

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
    window._add_user_marker()

    preview_root = ROOT / "artifacts" / "ui-preview-native"
    preview_root.mkdir(parents=True, exist_ok=True)
    captures = (
        (0, "dashboard.png"),
        (2, "controller_lab.png"),
        (3, "oscillator_lab.png"),
        (5, "sweep_lab.png"),
        (6, "correlation_lab.png"),
    )
    for index, filename in captures:
        window._navigate(index)
        window._refresh_ui()
        pump(app, 0.2)
        pixmap = window.grab()
        if pixmap.isNull() or not pixmap.save(str(preview_root / filename), "PNG"):
            raise RuntimeError(f"Failed to capture {filename}")

    window.close()
    app.processEvents()
    print("Native Windows UI previews captured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
