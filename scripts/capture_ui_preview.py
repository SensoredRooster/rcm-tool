"""Capture readable native-Windows screenshots of the real RcmTool UI."""
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

    preview_root = ROOT / "artifacts" / "ui-preview-native"
    preview_root.mkdir(parents=True, exist_ok=True)
    captures = (
        (0, "dashboard.png"),
        (1, "live_capture.png"),
        (2, "controller_lab.png"),
        (3, "oscillator_lab.png"),
        (5, "sweep_lab.png"),
        (6, "correlation_lab.png"),
        (11, "support.png"),
        (12, "settings.png"),
    )
    for index, filename in captures:
        window._navigate(index)
        window._refresh_ui()
        pump(app, 0.2)
        pixmap = window.grab()
        if pixmap.isNull() or not pixmap.save(str(preview_root / filename), "PNG"):
            raise RuntimeError(f"Failed to capture {filename}")

    # Verify both manual controller views exist independently of auto detection.
    window._navigate(2)
    for family, filename in (("xbox", "controller_xbox.png"), ("dualsense", "controller_dualsense.png")):
        index = window.controller_skin_combo.findData(family)
        if index < 0:
            raise RuntimeError(f"Missing controller view option: {family}")
        window.controller_skin_combo.setCurrentIndex(index)
        window._refresh_ui()
        pump(app, 0.2)
        pixmap = window.grab()
        if pixmap.isNull() or not pixmap.save(str(preview_root / filename), "PNG"):
            raise RuntimeError(f"Failed to capture {filename}")
    window.controller_skin_combo.setCurrentIndex(window.controller_skin_combo.findData("auto"))

    window.close()
    app.processEvents()
    print("Native Windows UI previews captured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
