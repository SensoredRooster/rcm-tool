"""Hardware-only Stick Cleaner calibration page.

The page consumes live samples supplied by ``MainWindow``.  It has no
simulation mode and does not claim to change controller firmware or the game
input path.
"""
from __future__ import annotations

import csv
from pathlib import Path
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from joystick_filter import JoystickFilter, PRESETS


def _axis(sample: dict, key: str) -> float:
    try:
        return float(sample.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


class StickCleanerPage(QWidget):
    """Rest calibration and transparent host-side cleaned-value preview."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.filter = JoystickFilter.from_preset("marius_midas")
        self.right_filter = JoystickFilter.from_preset("marius_midas")
        self._calibrating = False
        self._calibration_deadline = 0.0
        self._calibration_timestamps: list[int] = []
        self._calibration_samples: list[dict] = []
        self._last_timestamp_ns = -1
        self._last_samples: list[dict] = []
        self._last_rate_hz: float | None = None
        self._last_evidence_class = "unavailable"
        self._last_report: dict[str, float | int] | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(12)

        title = QLabel("Stick Cleaner")
        title.setObjectName("Title")
        root.addWidget(title)
        intro = QLabel(
            "Host-side rest calibration for a physical controller. It reads only the selected live stream; "
            "it does not simulate reports, write firmware, or modify what a game receives."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        root.addWidget(intro)

        config = QGroupBox("Hardware and processing")
        form = QFormLayout(config)
        self.hardware_combo = QComboBox()
        for key, preset in PRESETS.items():
            self.hardware_combo.addItem(str(preset["label"]), key)
        self.hardware_combo.currentIndexChanged.connect(self._preset_changed)
        form.addRow("Hardware preset", self.hardware_combo)
        self.power_curve = QCheckBox("Apply power curve here")
        self.power_curve.setToolTip("Leave off when the controller firmware already applies its own response curve.")
        self.power_curve.toggled.connect(self._options_changed)
        form.addRow("", self.power_curve)
        self.overlay = QCheckBox("Show cleaned overlay")
        self.overlay.setChecked(True)
        form.addRow("", self.overlay)
        root.addWidget(config)

        self.rate_label = QLabel("Observed report rate: unavailable")
        self.evidence_label = QLabel("Evidence: no physical controller reports received")
        self.status_label = QLabel("Connect a controller, select Raw HID in Controller Lab, and start Record Session.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("Muted")
        root.addWidget(self.rate_label)
        root.addWidget(self.evidence_label)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        self.calibrate_button = QPushButton("Calibrate rest 2 s")
        self.calibrate_button.clicked.connect(self.start_rest_calibration)
        actions.addWidget(self.calibrate_button)
        self.export_button = QPushButton("Export rest CSV")
        self.export_button.clicked.connect(self.export_rest_csv)
        self.export_button.setEnabled(False)
        actions.addWidget(self.export_button)
        actions.addStretch(1)
        root.addLayout(actions)

        metrics = QGroupBox("Measured rest result")
        grid = QGridLayout(metrics)
        for column, text in enumerate(("", "Center", "Peak-to-peak", "Hysteresis")):
            grid.addWidget(QLabel(text), 0, column)
        self.metric_labels: dict[str, QLabel] = {}
        for row, axis in enumerate(("Left", "Right"), start=1):
            grid.addWidget(QLabel(axis), row, 0)
            for column, suffix in enumerate(("center", "pp", "hyst"), start=1):
                label = QLabel("unavailable")
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.metric_labels[f"{axis.lower()}_{suffix}"] = label
                grid.addWidget(label, row, column)
        root.addWidget(metrics)

        preview = QGroupBox("Latest live values")
        preview_form = QFormLayout(preview)
        self.raw_label = QLabel("Raw: unavailable")
        self.cleaned_label = QLabel("Cleaned: unavailable")
        self.raw_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cleaned_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_form.addRow("Raw axes", self.raw_label)
        preview_form.addRow("Cleaned axes", self.cleaned_label)
        root.addWidget(preview)
        root.addStretch(1)

    def _preset_changed(self, _index: int) -> None:
        key = str(self.hardware_combo.currentData() or "marius_midas")
        self.filter = JoystickFilter.from_preset(key)
        self.right_filter = JoystickFilter.from_preset(key)
        self._apply_options()
        self._last_report = None
        self.export_button.setEnabled(False)
        self.status_label.setText("Preset changed. Run a new physical rest calibration before judging cleaned values.")

    def _options_changed(self, _checked: bool) -> None:
        self._apply_options()

    def _apply_options(self) -> None:
        self.filter.apply_power_curve = self.power_curve.isChecked()
        self.right_filter.apply_power_curve = self.power_curve.isChecked()

    def start_rest_calibration(self) -> None:
        if self._last_evidence_class in {"unavailable", ""} or not self._last_samples:
            self.status_label.setText("No physical samples are available. Connect the controller, select Raw HID, and start Record Session first.")
            return
        self._calibrating = True
        self._calibration_deadline = time.monotonic() + 2.0
        self._calibration_timestamps.clear()
        self._calibration_samples.clear()
        self._last_report = None
        self.export_button.setEnabled(False)
        self.status_label.setText("REST CAPTURE — keep both thumbs off the sticks for 2 seconds.")

    def update_from_lab(self, timestamps: list[int], samples: list[dict], *, evidence_class: str, timing) -> None:
        count = min(len(timestamps), len(samples))
        if count:
            timestamps = timestamps[-count:]
            samples = samples[-count:]
        else:
            timestamps, samples = [], []
        self._last_evidence_class = str(evidence_class or "unavailable")
        self._last_rate_hz = float(getattr(timing, "effective_rate_hz", 0.0) or 0.0) or None
        self._last_samples = [dict(sample) for sample in samples]
        if self._last_rate_hz is None:
            self.rate_label.setText("Observed report rate: unavailable")
        else:
            self.rate_label.setText(f"Observed report rate: {self._last_rate_hz:,.2f} Hz")
        if self._last_evidence_class == "measured-host-observed-raw-hid":
            self.evidence_label.setText("Evidence: Raw HID measured at host arrival")
        elif self._last_evidence_class == "unavailable":
            self.evidence_label.setText("Evidence: no physical controller reports received")
        else:
            self.evidence_label.setText("Evidence: host-poll estimate — select named Raw HID for measured-host timing")

        if samples:
            last = samples[-1]
            self.raw_label.setText(self._format_axes(last))
            dt = (1.0 / self._last_rate_hz) if self._last_rate_hz else 1.0 / 1000.0
            left = self.filter.process(_axis(last, "lx"), _axis(last, "ly"), dt=dt)
            right = self.right_filter.process(_axis(last, "rx"), _axis(last, "ry"), dt=dt)
            cleaned = (*left, *right)
            self.cleaned_label.setText("LX %.4f  LY %.4f  RX %.4f  RY %.4f" % cleaned if self.overlay.isChecked() else "Overlay disabled")

        if self._calibrating:
            for timestamp, sample in zip(timestamps, samples):
                timestamp = int(timestamp)
                if timestamp <= self._last_timestamp_ns:
                    continue
                self._last_timestamp_ns = timestamp
                self._calibration_timestamps.append(timestamp)
                self._calibration_samples.append(dict(sample))
            remaining = max(0.0, self._calibration_deadline - time.monotonic())
            if remaining > 0:
                self.status_label.setText(f"REST CAPTURE — keep still • {remaining:.1f} s remaining • {len(self._calibration_samples):,} reports")
            else:
                self._finish_calibration()

    def _finish_calibration(self) -> None:
        self._calibrating = False
        if not self._calibration_samples:
            self.status_label.setText("Rest capture failed: no physical reports arrived during the window.")
            return
        left = JoystickFilter.from_preset(str(self.hardware_combo.currentData() or "marius_midas"))
        right = JoystickFilter.from_preset(str(self.hardware_combo.currentData() or "marius_midas"))
        left_report = left.calibrate_from_rest(
            [_axis(sample, "lx") for sample in self._calibration_samples],
            [_axis(sample, "ly") for sample in self._calibration_samples],
        )
        right_report = right.calibrate_from_rest(
            [_axis(sample, "rx") for sample in self._calibration_samples],
            [_axis(sample, "ry") for sample in self._calibration_samples],
        )
        self.filter.center_x, self.filter.center_y = left.center_x, left.center_y
        self.filter.hysteresis = max(left.hysteresis, right.hysteresis)
        self.right_filter.center_x, self.right_filter.center_y = right.center_x, right.center_y
        self.right_filter.hysteresis = max(left.hysteresis, right.hysteresis)
        self._last_report = {
            "sample_count": int(left_report["sample_count"]),
            "left_center_x": float(left_report["center_x"]),
            "left_center_y": float(left_report["center_y"]),
            "left_peak_to_peak_x": float(left_report["peak_to_peak_x"]),
            "left_peak_to_peak_y": float(left_report["peak_to_peak_y"]),
            "left_hysteresis": float(left_report["hysteresis"]),
            "right_center_x": float(right_report["center_x"]),
            "right_center_y": float(right_report["center_y"]),
            "right_peak_to_peak_x": float(right_report["peak_to_peak_x"]),
            "right_peak_to_peak_y": float(right_report["peak_to_peak_y"]),
            "right_hysteresis": float(right_report["hysteresis"]),
        }
        self.metric_labels["left_center"].setText("%.4f, %.4f" % (left.center_x, left.center_y))
        self.metric_labels["left_pp"].setText("%.4f / %.4f" % (left_report["peak_to_peak_x"], left_report["peak_to_peak_y"]))
        self.metric_labels["left_hyst"].setText("%.4f" % left.hysteresis)
        self.metric_labels["right_center"].setText("%.4f, %.4f" % (right.center_x, right.center_y))
        self.metric_labels["right_pp"].setText("%.4f / %.4f" % (right_report["peak_to_peak_x"], right_report["peak_to_peak_y"]))
        self.metric_labels["right_hyst"].setText("%.4f" % right.hysteresis)
        self.export_button.setEnabled(True)
        self.status_label.setText(f"Rest applied from {len(self._calibration_samples):,} physical reports. Cleaned values are host-side only.")

    def export_rest_csv(self) -> None:
        if not self._calibration_samples:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export rest capture", "stick_rest.csv", "CSV files (*.csv)")
        if not path:
            return
        output = Path(path)
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp_ns", "lx", "ly", "rx", "ry"])
            for timestamp, sample in zip(self._calibration_timestamps, self._calibration_samples):
                writer.writerow([timestamp, _axis(sample, "lx"), _axis(sample, "ly"), _axis(sample, "rx"), _axis(sample, "ry")])
        QMessageBox.information(self, "Rest capture exported", f"Saved {len(self._calibration_samples):,} physical reports to:\n{output}")

    @staticmethod
    def _format_axes(sample: dict) -> str:
        return "LX %.4f  LY %.4f  RX %.4f  RY %.4f" % tuple(_axis(sample, key) for key in ("lx", "ly", "rx", "ry"))
