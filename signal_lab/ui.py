"""PySide6 desktop UI for Gamepad Signal Lab."""
from __future__ import annotations

from collections import deque
import math
from pathlib import Path
import queue
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QStandardPaths, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget
)

from . import __version__
from .analysis import align_nearest, oscillator_metrics, pearson_correlation, timing_metrics
from .controller import ControllerAcquisition
from .instruments import (
    SafetyLimits, SimulatedInstrument, VisaScpiGenerator,
    VisaScpiMeasurementInstrument, list_visa_resources,
)
from .oscillator import OscillatorAcquisition
from .reporting import write_html_report
from .simulation import GamepadSimulator, OscillatorSimulator
from .storage import LabDatabase
from .sweep import make_sweep

DARK_QSS = """
QWidget { background: #080B12; color: #EAF0FB; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow { background: #080B12; }
QFrame#Sidebar { background: #0C111C; border-right: 1px solid #1E293B; }
QFrame#Card { background: #101827; border: 1px solid #243249; border-radius: 16px; }
QFrame#Topbar { background: #0A0F18; border-bottom: 1px solid #1E293B; }
QLabel#Title { font-size: 24px; font-weight: 700; }
QLabel#SectionTitle { font-size: 20px; font-weight: 700; }
QLabel#MetricValue { font-size: 25px; font-weight: 750; }
QLabel#MetricLabel { color: #8FA1BA; font-size: 11px; font-weight: 700; }
QLabel#Muted { color: #8FA1BA; }
QLabel#Accent { color: #77D9FF; font-weight: 700; }
QPushButton { background: #121C2D; border: 1px solid #2B3B55; border-radius: 11px; padding: 9px 13px; font-weight: 650; }
QPushButton:hover { background: #18253B; border-color: #3B82F6; }
QPushButton:checked, QPushButton#Primary { background: #2563EB; border-color: #3B82F6; color: white; }
QPushButton#Danger { background: #3A1118; border-color: #8A2637; color: #FFD8DF; }
QPushButton#Danger:hover { background: #511823; }
QPushButton:disabled { color: #56657A; background: #0D1420; border-color: #1B2637; }
QComboBox, QSpinBox, QDoubleSpinBox { background: #0C1421; border: 1px solid #2A3950; border-radius: 10px; padding: 8px; min-height: 18px; }
QProgressBar { background: #0C1421; border: 1px solid #26364D; border-radius: 8px; text-align: center; min-height: 16px; }
QProgressBar::chunk { background: #3B82F6; border-radius: 7px; }
QTableWidget { background: #0C1421; alternate-background-color: #101B2B; gridline-color: #233148; border: 1px solid #233148; border-radius: 12px; }
QHeaderView::section { background: #111C2C; color: #9FB0C9; border: 0; border-bottom: 1px solid #2A3950; padding: 8px; font-weight: 650; }
QScrollArea { border: 0; }
"""

LIGHT_QSS = """
QWidget { background: #F4F7FB; color: #172033; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow { background: #F4F7FB; }
QFrame#Sidebar { background: #FFFFFF; border-right: 1px solid #D7DFEA; }
QFrame#Card { background: #FFFFFF; border: 1px solid #D7DFEA; border-radius: 16px; }
QFrame#Topbar { background: #FFFFFF; border-bottom: 1px solid #D7DFEA; }
QLabel#Title { font-size: 24px; font-weight: 700; }
QLabel#SectionTitle { font-size: 20px; font-weight: 700; }
QLabel#MetricValue { font-size: 25px; font-weight: 750; }
QLabel#MetricLabel, QLabel#Muted { color: #66758B; }
QLabel#Accent { color: #1769E0; font-weight: 700; }
QPushButton { background: #EDF2F8; border: 1px solid #D0D9E6; border-radius: 11px; padding: 9px 13px; font-weight: 650; }
QPushButton:hover { background: #E4ECF7; border-color: #3B82F6; }
QPushButton:checked, QPushButton#Primary { background: #2563EB; border-color: #2563EB; color: white; }
QPushButton#Danger { background: #FFE8EB; border-color: #ECA4AE; color: #8A2637; }
QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #C9D4E3; border-radius: 10px; padding: 8px; }
QProgressBar { background: #E7EDF5; border: 1px solid #CFD9E7; border-radius: 8px; text-align: center; }
QProgressBar::chunk { background: #2563EB; border-radius: 7px; }
QTableWidget { background: white; alternate-background-color: #F5F8FC; gridline-color: #D8E1EC; border: 1px solid #D8E1EC; border-radius: 12px; }
QHeaderView::section { background: #EDF2F8; color: #53647B; border: 0; padding: 8px; font-weight: 650; }
QScrollArea { border: 0; }
"""


def _card(layout=None):
    frame = QFrame()
    frame.setObjectName("Card")
    inner = QVBoxLayout(frame)
    inner.setContentsMargins(16, 14, 16, 14)
    inner.setSpacing(10)
    if layout is not None:
        layout.addWidget(frame)
    return frame, inner


def _heading(text: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    l = QVBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 8)
    l.setSpacing(3)
    title = QLabel(text)
    title.setObjectName("SectionTitle")
    l.addWidget(title)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        l.addWidget(sub)
    return w


class MetricCard(QFrame):
    def __init__(self, label: str, value: str = "—", foot: str = "") -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumHeight(104)
        l = QVBoxLayout(self)
        l.setContentsMargins(15, 13, 15, 13)
        l.setSpacing(4)
        cap = QLabel(label.upper())
        cap.setObjectName("MetricLabel")
        l.addWidget(cap)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("MetricValue")
        l.addWidget(self.value_label)
        self.foot = QLabel(foot)
        self.foot.setObjectName("Muted")
        l.addWidget(self.foot)

    def set_value(self, value: str, foot: str | None = None) -> None:
        self.value_label.setText(value)
        if foot is not None:
            self.foot.setText(foot)


class TrendChart(QWidget):
    def __init__(self, title: str, unit: str = "") -> None:
        super().__init__()
        self.title = title
        self.unit = unit
        self.values = deque(maxlen=360)
        self.setMinimumHeight(220)

    def add_value(self, value: float) -> None:
        if math.isfinite(value):
            self.values.append(float(value))
            self.update()

    def clear(self) -> None:
        self.values.clear()
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(QPen(QColor("#25344A"), 1))
        p.setBrush(QColor("#0C1421"))
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 14, 14)
        p.setPen(QColor("#A9B7CB"))
        p.setFont(QFont("Segoe UI", 9, 600))
        p.drawText(QRectF(14, 10, self.width() - 28, 22), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        if len(self.values) < 2:
            p.setPen(QColor("#617087"))
            p.drawText(rect, Qt.AlignCenter, "Waiting for samples…")
            return
        vals = list(self.values)
        lo, hi = min(vals), max(vals)
        if abs(hi - lo) < 1e-15:
            hi = lo + 1.0
        plot = QRectF(16, 42, max(10, self.width() - 32), max(10, self.height() - 62))
        p.setPen(QPen(QColor("#1E2B40"), 1))
        for i in range(1, 5):
            y = plot.top() + plot.height() * i / 5
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        path = QPainterPath()
        for i, v in enumerate(vals):
            x = plot.left() + plot.width() * i / max(1, len(vals) - 1)
            y = plot.bottom() - (v - lo) / (hi - lo) * plot.height()
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor("#62D4FF"), 2))
        p.drawPath(path)
        p.setPen(QColor("#7789A2"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(plot.left(), plot.bottom() + 3, plot.width(), 16), Qt.AlignRight, f"{vals[-1]:.6g} {self.unit}".strip())


class StickView(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.title = title
        self.x = 0.0
        self.y = 0.0
        self.setMinimumSize(180, 180)

    def set_xy(self, x: float, y: float) -> None:
        self.x = max(-1, min(1, x))
        self.y = max(-1, min(1, y))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = min(self.width(), self.height()) - 46
        cx, cy = self.width() / 2, self.height() / 2
        p.setPen(QPen(QColor("#2A3A53"), 2))
        p.setBrush(QColor("#0C1421"))
        p.drawEllipse(QPointF(cx, cy), r / 2, r / 2)
        p.setPen(QPen(QColor("#26364D"), 1))
        p.drawLine(QPointF(cx - r / 2, cy), QPointF(cx + r / 2, cy))
        p.drawLine(QPointF(cx, cy - r / 2), QPointF(cx, cy + r / 2))
        px, py = cx + self.x * r / 2, cy - self.y * r / 2
        p.setPen(QPen(QColor("#8DE4FF"), 2))
        p.setBrush(QColor("#2563EB"))
        p.drawEllipse(QPointF(px, py), 9, 9)
        p.setPen(QColor("#A9B7CB"))
        p.drawText(QRectF(0, 4, self.width(), 22), Qt.AlignCenter, self.title)


class LabWindow(QMainWindow):
    NAV = [
        "Dashboard", "Live Capture", "Controller Lab", "Oscillator Lab",
        "Interference Lab", "Sweep Lab", "Correlation", "Experiments",
        "Compare", "Reports", "Instruments", "Settings",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Gamepad Signal Lab")
        self.resize(1440, 900)
        self.setMinimumSize(1080, 700)
        self.setStyleSheet(DARK_QSS)
        self.dark_mode = True

        app_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        app_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = app_dir
        self.db = LabDatabase(app_dir / "gamepad_signal_lab.sqlite3")
        self.session_id = self.db.create_session(
            "Live session", "simulation", __version__,
            {"timing_source": "perf_counter_ns"},
        )

        self.gamepad_sim = GamepadSimulator()
        self.osc_sim = OscillatorSimulator()
        self.instrument = SimulatedInstrument()
        self.limits = SafetyLimits()

        self.simulation_mode = True
        self.capture_running = False
        self.baseline_running = False
        self.baseline_started = 0.0
        self.baseline_duration_s = 60
        self.timestamps = deque(maxlen=20000)
        self.osc_freqs = deque(maxlen=5000)
        self.osc_times = deque(maxlen=5000)
        self.controller_samples = deque(maxlen=5000)
        self.baseline_timing = None
        self.baseline_osc = None

        self.hw_queue = queue.SimpleQueue()
        self.hw_acquisition = ControllerAcquisition(self.hw_queue.put)
        self.osc_queue = queue.SimpleQueue()
        self.osc_measurement_instrument = None
        self.osc_acquisition = None
        self.sweep_timer = QTimer(self)
        self.sweep_timer.timeout.connect(self._advance_sweep)
        self.sweep_plan = []
        self.sweep_index = 0

        self._build()
        self._set_page(0)

        self.sample_timer = QTimer(self)
        self.sample_timer.timeout.connect(self._sample_tick)
        self.sample_timer.start(50)
        self.metric_timer = QTimer(self)
        self.metric_timer.timeout.connect(self._update_metrics)
        self.metric_timer.start(250)

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(210)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(14, 18, 14, 14)
        sl.setSpacing(5)
        brand = QLabel("GAMEPAD\nSIGNAL LAB")
        brand.setFont(QFont("Segoe UI", 14, 700))
        sl.addWidget(brand)
        ver = QLabel(f"RCM Tool evolution · v{__version__}")
        ver.setObjectName("Muted")
        sl.addWidget(ver)
        sl.addSpacing(14)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = []
        for i, name in enumerate(self.NAV):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setMinimumHeight(36)
            b.clicked.connect(lambda checked, idx=i: self._set_page(idx))
            self.nav_group.addButton(b)
            self.nav_buttons.append(b)
            sl.addWidget(b)
        sl.addStretch(1)
        self.hw_status = QLabel("● Simulation hardware")
        self.hw_status.setObjectName("Accent")
        sl.addWidget(self.hw_status)
        dbs = QLabel("SQLite · WAL")
        dbs.setObjectName("Muted")
        sl.addWidget(dbs)
        outer.addWidget(sidebar)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        top = QFrame()
        top.setObjectName("Topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(22, 12, 22, 12)
        self.page_title = QLabel("Dashboard")
        self.page_title.setObjectName("Title")
        tl.addWidget(self.page_title)
        tl.addStretch(1)
        self.mode_badge = QLabel("SIMULATION")
        self.mode_badge.setObjectName("Accent")
        tl.addWidget(self.mode_badge)
        self.capture_btn = QPushButton("Start Capture")
        self.capture_btn.setObjectName("Primary")
        self.capture_btn.clicked.connect(self._toggle_capture)
        tl.addWidget(self.capture_btn)
        rl.addWidget(top)

        self.stack = QStackedWidget()
        rl.addWidget(self.stack, 1)
        outer.addWidget(right, 1)
        self._build_pages()

    def _page_shell(self, title: str, subtitle: str):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        scroll.setWidget(host)
        l = QVBoxLayout(host)
        l.setContentsMargins(22, 20, 22, 28)
        l.setSpacing(14)
        l.addWidget(_heading(title, subtitle))
        self.stack.addWidget(scroll)
        return host, l

    def _build_pages(self) -> None:
        _, l = self._page_shell(
            "Dashboard",
            "Live timing, clock stability, quality and stimulus state. Raw measurements stay separate from display smoothing.",
        )
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(12)
        l.addLayout(g)
        self.cards = {}
        specs = [
            ("rate", "Gamepad rate"), ("interval", "Report interval"),
            ("jitter", "Gamepad jitter"), ("osc", "Oscillator"),
            ("ppm", "Clock error"), ("clock_jitter", "Clock jitter"),
            ("late", "Late reports"), ("stimulus", "Test stimulus"),
        ]
        for idx, (key, label) in enumerate(specs):
            self.cards[key] = MetricCard(label)
            g.addWidget(self.cards[key], idx // 4, idx % 4)
        self.dashboard_chart = TrendChart("Report interval · live", "ms")
        l.addWidget(self.dashboard_chart)
        row = QHBoxLayout()
        self.baseline_btn = QPushButton("Run 60 s Baseline")
        self.baseline_btn.clicked.connect(self._start_baseline)
        row.addWidget(self.baseline_btn)
        self.baseline_progress = QProgressBar()
        self.baseline_progress.setRange(0, 1000)
        row.addWidget(self.baseline_progress, 1)
        l.addLayout(row)
        l.addStretch(1)

        _, l = self._page_shell(
            "Live Capture",
            "Controller report timing and oscillator measurements share the same application timeline.",
        )
        self.live_jitter_chart = TrendChart("Gamepad interval", "ms")
        self.live_osc_chart = TrendChart("Oscillator frequency error", "ppm")
        l.addWidget(self.live_jitter_chart)
        l.addWidget(self.live_osc_chart)

        _, l = self._page_shell(
            "Controller Lab",
            "Inspect controller motion, stationary noise and acquisition quality.",
        )
        row = QHBoxLayout()
        self.left_stick = StickView("LEFT STICK")
        self.right_stick = StickView("RIGHT STICK")
        row.addWidget(self.left_stick)
        row.addWidget(self.right_stick)
        l.addLayout(row)
        self.controller_table = QTableWidget(0, 2)
        self.controller_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.controller_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        l.addWidget(self.controller_table)

        _, l = self._page_shell(
            "Oscillator Lab",
            "Clock measurements are marked as measured, calculated, simulated or unavailable based on the active source.",
        )
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Nominal frequency"))
        self.nominal_spin = QDoubleSpinBox()
        self.nominal_spin.setRange(1, 1_000_000_000)
        self.nominal_spin.setDecimals(3)
        self.nominal_spin.setValue(12_000_000)
        self.nominal_spin.setSuffix(" Hz")
        self.nominal_spin.valueChanged.connect(self._nominal_changed)
        controls.addWidget(self.nominal_spin)
        controls.addStretch(1)
        l.addLayout(controls)

        osc_connect, oc = _card()
        osc_form = QFormLayout()
        oc.addLayout(osc_form)
        l.addWidget(osc_connect)
        self.osc_resource_combo = QComboBox()
        self.osc_resource_combo.addItem("Simulation oscillator")
        self.osc_resource_combo.addItems(list_visa_resources())
        osc_form.addRow("Measurement source", self.osc_resource_combo)
        osc_actions = QHBoxLayout()
        self.osc_connect_btn = QPushButton("Connect measurement instrument")
        self.osc_connect_btn.clicked.connect(self._connect_oscillator_instrument)
        osc_actions.addWidget(self.osc_connect_btn)
        self.osc_disconnect_btn = QPushButton("Disconnect")
        self.osc_disconnect_btn.clicked.connect(self._disconnect_oscillator_instrument)
        osc_actions.addWidget(self.osc_disconnect_btn)
        osc_actions.addStretch(1)
        oc.addLayout(osc_actions)
        self.osc_source_status = QLabel("Simulation oscillator · known synthetic data")
        self.osc_source_status.setObjectName("Muted")
        oc.addWidget(self.osc_source_status)

        og = QGridLayout()
        self.osc_cards = {}
        osc_specs = [
            ("frequency", "Frequency"), ("error", "Error"),
            ("ppm", "Error PPM"), ("period", "Period"),
            ("rms", "RMS jitter"), ("p2p", "P-P jitter"),
            ("c2c", "Cycle-cycle RMS"), ("allan", "Allan dev τ=1"),
        ]
        for i, (k, t) in enumerate(osc_specs):
            self.osc_cards[k] = MetricCard(t)
            og.addWidget(self.osc_cards[k], i // 4, i % 4)
        l.addLayout(og)
        self.osc_chart = TrendChart("Frequency drift", "ppm")
        l.addWidget(self.osc_chart)

        _, l = self._page_shell(
            "Interference Lab",
            "Controlled bench stimulus. Generator output always starts OFF and requires explicit activation.",
        )
        form_card, fc = _card()
        form = QFormLayout()
        fc.addLayout(form)
        l.addWidget(form_card)
        self.instrument_combo = QComboBox()
        self.instrument_combo.addItems(["Simulation Instrument"] + list_visa_resources())
        form.addRow("Generator", self.instrument_combo)
        generator_row = QHBoxLayout()
        self.generator_connect_btn = QPushButton("Connect generator")
        self.generator_connect_btn.clicked.connect(self._connect_generator)
        generator_row.addWidget(self.generator_connect_btn)
        self.generator_disconnect_btn = QPushButton("Disconnect generator")
        self.generator_disconnect_btn.clicked.connect(self._disconnect_generator)
        generator_row.addWidget(self.generator_disconnect_btn)
        generator_row.addStretch(1)
        form.addRow("Connection", generator_row)
        self.wave_combo = QComboBox()
        self.wave_combo.addItems(["SINE", "SQU", "RAMP", "PULS", "NOIS"])
        form.addRow("Waveform", self.wave_combo)
        self.stim_freq = QDoubleSpinBox()
        self.stim_freq.setRange(0.1, self.limits.max_frequency_hz)
        self.stim_freq.setValue(1000)
        self.stim_freq.setSuffix(" Hz")
        form.addRow("Frequency", self.stim_freq)
        self.stim_amp = QDoubleSpinBox()
        self.stim_amp.setRange(0, self.limits.max_amplitude_vpp)
        self.stim_amp.setValue(0.1)
        self.stim_amp.setSuffix(" Vpp")
        form.addRow("Amplitude", self.stim_amp)
        self.stim_offset = QDoubleSpinBox()
        self.stim_offset.setRange(-self.limits.max_abs_offset_v, self.limits.max_abs_offset_v)
        self.stim_offset.setValue(0)
        self.stim_offset.setSuffix(" V")
        form.addRow("Offset", self.stim_offset)
        actions = QHBoxLayout()
        self.arm_btn = QPushButton("Enable Output")
        self.arm_btn.clicked.connect(self._toggle_output)
        actions.addWidget(self.arm_btn)
        self.emergency_btn = QPushButton("EMERGENCY OUTPUT OFF")
        self.emergency_btn.setObjectName("Danger")
        self.emergency_btn.clicked.connect(self._emergency_off)
        actions.addWidget(self.emergency_btn)
        actions.addStretch(1)
        l.addLayout(actions)
        self.interference_status = QLabel("Output OFF · safe default")
        self.interference_status.setObjectName("Accent")
        l.addWidget(self.interference_status)
        l.addStretch(1)

        _, l = self._page_shell(
            "Sweep Lab",
            "Create reproducible linear or logarithmic stimulus sweeps. Every step is timestamped.",
        )
        c, cl = _card()
        f = QFormLayout()
        cl.addLayout(f)
        l.addWidget(c)
        self.sweep_start = QDoubleSpinBox()
        self.sweep_start.setRange(1, 20_000_000)
        self.sweep_start.setValue(100)
        self.sweep_start.setSuffix(" Hz")
        f.addRow("Start", self.sweep_start)
        self.sweep_stop = QDoubleSpinBox()
        self.sweep_stop.setRange(1, 20_000_000)
        self.sweep_stop.setValue(10000)
        self.sweep_stop.setSuffix(" Hz")
        f.addRow("Stop", self.sweep_stop)
        self.sweep_steps = QSpinBox()
        self.sweep_steps.setRange(1, 1000)
        self.sweep_steps.setValue(8)
        f.addRow("Steps", self.sweep_steps)
        self.sweep_type = QComboBox()
        self.sweep_type.addItems(["Logarithmic", "Linear"])
        f.addRow("Type", self.sweep_type)
        self.sweep_reps = QSpinBox()
        self.sweep_reps.setRange(1, 100)
        self.sweep_reps.setValue(1)
        f.addRow("Repetitions", self.sweep_reps)
        self.sweep_dwell = QDoubleSpinBox()
        self.sweep_dwell.setRange(0.1, 120)
        self.sweep_dwell.setValue(1)
        self.sweep_dwell.setSuffix(" s")
        f.addRow("Dwell", self.sweep_dwell)
        r = QHBoxLayout()
        b = QPushButton("Build Plan")
        b.clicked.connect(self._build_sweep)
        r.addWidget(b)
        self.sweep_run = QPushButton("Start Sweep")
        self.sweep_run.setObjectName("Primary")
        self.sweep_run.clicked.connect(self._start_sweep)
        r.addWidget(self.sweep_run)
        r.addStretch(1)
        l.addLayout(r)
        self.sweep_progress = QProgressBar()
        l.addWidget(self.sweep_progress)
        self.sweep_table = QTableWidget(0, 3)
        self.sweep_table.setHorizontalHeaderLabels(["#", "Frequency", "Repetition"])
        self.sweep_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        l.addWidget(self.sweep_table)

        _, l = self._page_shell(
            "Correlation",
            "Descriptive correlation only; alignment does not by itself establish causation.",
        )
        self.corr_card = MetricCard(
            "Pearson correlation", "—",
            "Clock error vs gamepad interval deviation",
        )
        l.addWidget(self.corr_card)
        self.corr_note = QLabel("Waiting for synchronized samples.")
        self.corr_note.setObjectName("Muted")
        l.addWidget(self.corr_note)
        l.addStretch(1)

        _, l = self._page_shell(
            "Experiments",
            "Current local session, persistence and raw export.",
        )
        self.session_table = QTableWidget(0, 2)
        self.session_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.session_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        l.addWidget(self.session_table)
        r = QHBoxLayout()
        e1 = QPushButton("Export JSON")
        e1.clicked.connect(self._export_json)
        r.addWidget(e1)
        e2 = QPushButton("Export Controller CSV")
        e2.clicked.connect(self._export_csv)
        r.addWidget(e2)
        r.addStretch(1)
        l.addLayout(r)

        _, l = self._page_shell(
            "Compare",
            "Reference baseline compared with the current live measurement window.",
        )
        self.compare_table = QTableWidget(0, 4)
        self.compare_table.setHorizontalHeaderLabels(["Metric", "Baseline", "Current", "Change"])
        self.compare_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        l.addWidget(self.compare_table)

        _, l = self._page_shell(
            "Reports",
            "Generate a self-contained engineering report from the current session metrics.",
        )
        self.report_status = QLabel("No report generated yet.")
        self.report_status.setObjectName("Muted")
        l.addWidget(self.report_status)
        rb = QPushButton("Generate HTML Report")
        rb.setObjectName("Primary")
        rb.clicked.connect(self._generate_report)
        l.addWidget(rb)
        l.addStretch(1)

        _, l = self._page_shell(
            "Instruments",
            "Discover VISA/SCPI equipment and show capability boundaries.",
        )
        self.instrument_table = QTableWidget(0, 3)
        self.instrument_table.setHorizontalHeaderLabels(["Resource", "Type", "State"])
        self.instrument_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        l.addWidget(self.instrument_table)
        ib = QPushButton("Refresh Instruments")
        ib.clicked.connect(self._refresh_instruments)
        l.addWidget(ib)
        self._refresh_instruments()

        _, l = self._page_shell(
            "Settings",
            "Measurement defaults and appearance. Safety limits are intentionally conservative until explicitly changed in code/config.",
        )
        c, cl = _card()
        f = QFormLayout()
        cl.addLayout(f)
        l.addWidget(c)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Simulation", "Real controller"])
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        f.addRow("Capture source", self.mode_combo)
        self.baseline_spin = QSpinBox()
        self.baseline_spin.setRange(5, 3600)
        self.baseline_spin.setValue(60)
        self.baseline_spin.setSuffix(" s")
        self.baseline_spin.valueChanged.connect(lambda v: setattr(self, "baseline_duration_s", v))
        f.addRow("Baseline duration", self.baseline_spin)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark", "Light"])
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        f.addRow("Theme", self.theme_combo)
        safety = QLabel(
            f"Safety limits: ≤ {self.limits.max_frequency_hz:g} Hz · "
            f"≤ {self.limits.max_amplitude_vpp:g} Vpp · "
            f"|offset| ≤ {self.limits.max_abs_offset_v:g} V"
        )
        safety.setObjectName("Muted")
        l.addWidget(safety)
        l.addStretch(1)

    def _set_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.page_title.setText(self.NAV[index])
        self.nav_buttons[index].setChecked(True)

    def _toggle_capture(self) -> None:
        self.capture_running = not self.capture_running
        self.capture_btn.setText("Stop Capture" if self.capture_running else "Start Capture")
        if self.capture_running:
            self.timestamps.clear()
            self.osc_freqs.clear()
            self.controller_samples.clear()
            self.dashboard_chart.clear()
            self.live_jitter_chart.clear()
            self.live_osc_chart.clear()
            self.osc_chart.clear()
            if not self.simulation_mode:
                self.hw_acquisition.start()
            self.db.add_event(
                self.session_id, time.perf_counter_ns(), "capture_started",
                {"mode": "simulation" if self.simulation_mode else "real"},
            )
            self.db.flush()
        else:
            self.hw_acquisition.stop()
            self.db.add_event(self.session_id, time.perf_counter_ns(), "capture_stopped")
            self.db.flush()

    def _sample_tick(self) -> None:
        if not self.capture_running:
            return
        if self.simulation_mode:
            for _ in range(50):
                ts = self.gamepad_sim.next_timestamp_ns()
                sample = self.gamepad_sim.sample()
                self.timestamps.append(ts)
                self.controller_samples.append(sample)
                self.db.add_controller_sample(
                    self.session_id, ts, sample, source="simulated-controller"
                )
            if self.osc_measurement_instrument is None:
                freq = self.osc_sim.next_frequency_hz(0.05)
                ots = self.gamepad_sim.timestamp_ns
                self.osc_times.append(ots)
                self.osc_freqs.append(freq)
                self.db.add_oscillator_sample(
                    self.session_id, ots, freq,
                    source="simulated-oscillator",
                    duty_cycle_percent=50.0,
                    quality="simulated",
                )
        else:
            drained = 0
            while drained < 500:
                try:
                    m = self.hw_queue.get_nowait()
                except queue.Empty:
                    break
                self.timestamps.append(m.timestamp_ns)
                self.controller_samples.append(m.sample)
                self.db.add_controller_sample(
                    self.session_id, m.timestamp_ns, m.sample,
                    source=m.source,
                    raw_report_hex=m.raw_report_hex,
                )
                drained += 1

        osc_drained = 0
        while osc_drained < 100:
            try:
                measurement = self.osc_queue.get_nowait()
            except queue.Empty:
                break
            self.osc_times.append(measurement.timestamp_ns)
            self.osc_freqs.append(measurement.frequency_hz)
            self.db.add_oscillator_sample(
                self.session_id,
                measurement.timestamp_ns,
                measurement.frequency_hz,
                source=measurement.source,
                duty_cycle_percent=measurement.duty_cycle_percent,
                quality="measured",
            )
            osc_drained += 1

        if len(self.timestamps) % 1000 < 60:
            self.db.flush()

        if self.controller_samples:
            s = self.controller_samples[-1]
            self.left_stick.set_xy(float(s.get("lx", 0)), float(s.get("ly", 0)))
            self.right_stick.set_xy(float(s.get("rx", 0)), float(s.get("ry", 0)))

    def _update_metrics(self) -> None:
        tm = timing_metrics(list(self.timestamps))
        om = oscillator_metrics(
            list(self.osc_freqs),
            self.nominal_spin.value() if hasattr(self, "nominal_spin") else 12_000_000.0,
        )
        self.cards["rate"].set_value(
            f"{tm.effective_rate_hz:,.1f} Hz",
            f"{tm.sample_count:,} timestamps",
        )
        self.cards["interval"].set_value(
            f"{tm.mean_interval_ms:.4f} ms",
            f"P99 {tm.p99_ms:.4f} ms",
        )
        self.cards["jitter"].set_value(
            f"{tm.rms_deviation_ms:.4f} ms",
            f"P-P {tm.peak_to_peak_jitter_ms:.4f} ms",
        )
        self.cards["osc"].set_value(
            f"{om.mean_frequency_hz / 1e6:.6f} MHz" if om.sample_count else "Unavailable",
            "Simulated" if self.simulation_mode else "Requires instrument",
        )
        self.cards["ppm"].set_value(
            f"{om.frequency_error_ppm:+.3f} ppm" if om.sample_count else "—"
        )
        self.cards["clock_jitter"].set_value(
            f"{om.rms_period_jitter_s * 1e12:.2f} ps" if om.sample_count else "—"
        )
        self.cards["late"].set_value(
            str(tm.late_reports),
            f"Missing est. {tm.missing_reports_estimate}",
        )
        stimulus_hz = getattr(
            self.instrument, "frequency_hz",
            self.stim_freq.value() if hasattr(self, "stim_freq") else 0,
        )
        self.cards["stimulus"].set_value(
            "ON" if self.instrument.output_enabled() else "OFF",
            f"{stimulus_hz:g} Hz",
        )

        if tm.mean_interval_ms > 0:
            self.dashboard_chart.add_value(tm.mean_interval_ms)
            self.live_jitter_chart.add_value(tm.mean_interval_ms)
        if om.sample_count:
            self.live_osc_chart.add_value(om.frequency_error_ppm)
            self.osc_chart.add_value(om.frequency_error_ppm)

        self._update_oscillator_cards(om)
        self._update_controller_table(tm)
        self._update_correlation()
        self._update_session()
        self._update_compare(tm, om)
        if self.baseline_running:
            self._update_baseline_progress(tm, om)

    def _update_controller_table(self, tm) -> None:
        rows = [
            ("Acquisition", "Simulation" if self.simulation_mode else (self.hw_acquisition.error or "Real controller")),
            ("Timing quality", "Known simulated timestamps" if self.simulation_mode else "Host read timestamps / backend dependent"),
            ("Samples", f"{tm.sample_count:,}"),
            ("Effective rate", f"{tm.effective_rate_hz:.3f} Hz"),
            ("Mean interval", f"{tm.mean_interval_ms:.6f} ms"),
            ("Stdev", f"{tm.stdev_ms:.6f} ms"),
            ("RMS deviation", f"{tm.rms_deviation_ms:.6f} ms"),
            ("P99", f"{tm.p99_ms:.6f} ms"),
            ("P99.9", f"{tm.p999_ms:.6f} ms"),
            ("Late reports", str(tm.late_reports)),
            ("Missing reports (estimate)", str(tm.missing_reports_estimate)),
        ]
        self._fill_table(self.controller_table, rows)

    def _update_oscillator_cards(self, om) -> None:
        if not om.sample_count:
            for c in self.osc_cards.values():
                c.set_value("Unavailable", "Connect capable hardware or use simulation")
            return
        self.osc_cards["frequency"].set_value(
            f"{om.mean_frequency_hz:,.3f} Hz",
            "Simulated" if self.simulation_mode else "Measured",
        )
        self.osc_cards["error"].set_value(f"{om.frequency_error_hz:+.3f} Hz", "Calculated")
        self.osc_cards["ppm"].set_value(f"{om.frequency_error_ppm:+.4f} ppm", "Calculated")
        self.osc_cards["period"].set_value(f"{om.mean_period_s * 1e9:.4f} ns", "Calculated")
        self.osc_cards["rms"].set_value(f"{om.rms_period_jitter_s * 1e12:.3f} ps", "Calculated")
        self.osc_cards["p2p"].set_value(f"{om.peak_to_peak_period_jitter_s * 1e12:.3f} ps", "Calculated")
        self.osc_cards["c2c"].set_value(f"{om.cycle_to_cycle_rms_s * 1e12:.3f} ps", "Calculated")
        self.osc_cards["allan"].set_value(
            f"{om.allan_deviation_tau1:.3e}" if om.allan_deviation_tau1 is not None else "Unavailable",
            "τ = one sample interval",
        )

    def _update_correlation(self) -> None:
        if len(self.timestamps) < 3 or len(self.osc_freqs) < 3:
            self.corr_card.set_value("—")
            return
        ts = list(self.timestamps)
        intervals = [(b - a) / 1e6 for a, b in zip(ts, ts[1:])]
        if not intervals:
            return
        mean = sum(intervals) / len(intervals)
        dev = [v - mean for v in intervals]
        osc = list(self.osc_freqs)
        osc_times = list(self.osc_times)
        nominal = self.nominal_spin.value()
        oppm = [(f - nominal) / nominal * 1e6 for f in osc]
        aligned_dev, aligned_oppm = align_nearest(
            ts[1:], dev, osc_times, oppm, max_delta_ns=1_000_000_000
        )
        corr = pearson_correlation(aligned_dev, aligned_oppm)
        n = len(aligned_dev)
        self.corr_card.set_value(
            "—" if corr is None else f"{corr:+.4f}",
            f"n={n:,} timestamp-aligned pairs · descriptive only",
        )
        self.corr_note.setText(
            "Positive/negative values describe linear alignment in the current windows. "
            "They do not establish that one signal caused the other."
        )

    def _update_session(self) -> None:
        try:
            s = self.db.session_summary(self.session_id)
        except Exception:
            return
        rows = [
            ("Session ID", s["id"]),
            ("Created", s["created_utc"]),
            ("Mode", "Simulation" if self.simulation_mode else "Real controller"),
            ("Controller samples", str(s["controller_samples"])),
            ("Oscillator samples", str(s["oscillator_samples"])),
            ("Events", str(s["events"])),
            ("Database", str(self.db.path)),
        ]
        self._fill_table(self.session_table, rows)

    def _update_compare(self, tm, om) -> None:
        rows = []
        if self.baseline_timing:
            pairs = [
                ("Rate Hz", self.baseline_timing.effective_rate_hz, tm.effective_rate_hz),
                ("Mean interval ms", self.baseline_timing.mean_interval_ms, tm.mean_interval_ms),
                ("RMS jitter ms", self.baseline_timing.rms_deviation_ms, tm.rms_deviation_ms),
                ("P99 ms", self.baseline_timing.p99_ms, tm.p99_ms),
            ]
            if self.baseline_osc:
                pairs += [
                    ("Oscillator Hz", self.baseline_osc.mean_frequency_hz, om.mean_frequency_hz),
                    ("Clock error ppm", self.baseline_osc.frequency_error_ppm, om.frequency_error_ppm),
                ]
            for name, base, current in pairs:
                rows.append((name, f"{base:.8g}", f"{current:.8g}", f"{current - base:+.8g}"))
        self._fill_table(self.compare_table, rows)

    def _start_baseline(self) -> None:
        if not self.capture_running:
            self._toggle_capture()
        self.baseline_running = True
        self.baseline_started = time.monotonic()
        self.baseline_btn.setEnabled(False)
        self.baseline_progress.setValue(0)
        self.db.add_event(
            self.session_id, time.perf_counter_ns(), "baseline_started",
            {"duration_s": self.baseline_duration_s},
        )

    def _update_baseline_progress(self, tm, om) -> None:
        elapsed = time.monotonic() - self.baseline_started
        self.baseline_progress.setValue(
            min(1000, int(elapsed / self.baseline_duration_s * 1000))
        )
        if elapsed >= self.baseline_duration_s:
            self.baseline_running = False
            self.baseline_btn.setEnabled(True)
            self.baseline_timing = tm
            self.baseline_osc = om
            self.db.add_event(
                self.session_id, time.perf_counter_ns(), "baseline_completed",
                {"timing": tm.to_dict(), "oscillator": om.to_dict()},
            )
            self.db.flush()
            self.baseline_progress.setValue(1000)
            QMessageBox.information(
                self, "Baseline complete",
                "Reference baseline saved for this session.",
            )

    def _nominal_changed(self, value: float) -> None:
        self.osc_sim.config.nominal_frequency_hz = float(value)

    def _toggle_output(self) -> None:
        try:
            enabled = not self.instrument.output_enabled()
            if enabled:
                self.limits.validate(
                    frequency_hz=self.stim_freq.value(),
                    amplitude_vpp=self.stim_amp.value(),
                    offset_v=self.stim_offset.value(),
                )
                if isinstance(self.instrument, SimulatedInstrument):
                    self.instrument.frequency_hz = self.stim_freq.value()
                    self.instrument.amplitude_vpp = self.stim_amp.value()
                    self.instrument.offset_v = self.stim_offset.value()
                    self.instrument.waveform = self.wave_combo.currentText()
                elif isinstance(self.instrument, VisaScpiGenerator):
                    self.instrument.configure_generator(
                        frequency_hz=self.stim_freq.value(),
                        amplitude_vpp=self.stim_amp.value(),
                        offset_v=self.stim_offset.value(),
                        waveform=self.wave_combo.currentText(),
                        limits=self.limits,
                    )
            self.instrument.set_output(enabled)
            self.arm_btn.setText("Disable Output" if enabled else "Enable Output")
            self.interference_status.setText(
                "OUTPUT ON · explicit user enable"
                if enabled else
                "Output OFF · safe default"
            )
            self.db.add_event(
                self.session_id,
                time.perf_counter_ns(),
                "instrument_output",
                {
                    "enabled": enabled,
                    "frequency_hz": self.stim_freq.value(),
                    "amplitude_vpp": self.stim_amp.value(),
                    "offset_v": self.stim_offset.value(),
                    "waveform": self.wave_combo.currentText(),
                },
            )
            self.db.flush()
        except Exception as exc:
            QMessageBox.critical(self, "Instrument safety", str(exc))
            self._emergency_off()

    def _connect_generator(self) -> None:
        selected = self.instrument_combo.currentText()
        if selected == "Simulation Instrument":
            self._disconnect_generator(close_simulation=False)
            self.instrument = SimulatedInstrument()
            self.interference_status.setText("Simulation generator connected · output OFF")
            return
        self._emergency_off()
        self._disconnect_generator(close_simulation=False)
        try:
            candidate = VisaScpiGenerator(selected)
            ident = candidate.identify()
            self.instrument = candidate
            self.interference_status.setText(f"{ident} · output OFF")
            self.db.add_event(
                self.session_id, time.perf_counter_ns(), "generator_connected",
                {"resource": selected, "identity": ident},
            )
            self.db.flush()
        except Exception as exc:
            self.instrument = SimulatedInstrument()
            QMessageBox.critical(self, "Generator connection", str(exc))
            self.interference_status.setText("Generator connection failed · simulation source restored")

    def _disconnect_generator(self, close_simulation: bool = True) -> None:
        try:
            self.instrument.set_output(False)
        except Exception:
            pass
        if close_simulation or not isinstance(self.instrument, SimulatedInstrument):
            try:
                self.instrument.close()
            except Exception:
                pass
        self.instrument = SimulatedInstrument()
        self.arm_btn.setText("Enable Output")
        self.interference_status.setText("Simulation generator · output OFF")

    def _connect_oscillator_instrument(self) -> None:
        selected = self.osc_resource_combo.currentText()
        self._disconnect_oscillator_instrument()
        if selected == "Simulation oscillator":
            self.osc_source_status.setText("Simulation oscillator · known synthetic data")
            return
        try:
            instrument = VisaScpiMeasurementInstrument(selected)
            identity = instrument.identify()
            self.osc_measurement_instrument = instrument
            self.osc_acquisition = OscillatorAcquisition(instrument, self.osc_queue.put)
            self.osc_acquisition.start()
            self.osc_source_status.setText(f"{identity} · frequency acquisition active")
            self.db.add_event(
                self.session_id, time.perf_counter_ns(), "oscillator_instrument_connected",
                {"resource": selected, "identity": identity},
            )
            self.db.flush()
        except Exception as exc:
            self.osc_source_status.setText("Measurement instrument connection failed")
            QMessageBox.critical(self, "Oscillator instrument", str(exc))

    def _disconnect_oscillator_instrument(self) -> None:
        if self.osc_acquisition is not None:
            self.osc_acquisition.stop()
            self.osc_acquisition = None
        if self.osc_measurement_instrument is not None:
            try:
                self.osc_measurement_instrument.close()
            except Exception:
                pass
            self.osc_measurement_instrument = None
        if hasattr(self, "osc_source_status"):
            self.osc_source_status.setText("No physical oscillator measurement instrument connected")

    def _emergency_off(self) -> None:
        try:
            self.instrument.set_output(False)
        except Exception:
            pass
        self.arm_btn.setText("Enable Output")
        self.interference_status.setText("Output OFF · emergency/safe state")
        self.db.add_event(
            self.session_id, time.perf_counter_ns(), "emergency_output_off"
        )
        self.db.flush()

    def _build_sweep(self) -> None:
        try:
            self.sweep_plan = make_sweep(
                self.sweep_start.value(),
                self.sweep_stop.value(),
                self.sweep_steps.value(),
                logarithmic=self.sweep_type.currentText() == "Logarithmic",
                repetitions=self.sweep_reps.value(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Sweep plan", str(exc))
            return
        self.sweep_table.setRowCount(len(self.sweep_plan))
        for r, step in enumerate(self.sweep_plan):
            for c, v in enumerate((step.index, f"{step.frequency_hz:.6g} Hz", step.repetition)):
                self.sweep_table.setItem(r, c, QTableWidgetItem(str(v)))
        self.sweep_progress.setRange(0, max(1, len(self.sweep_plan)))
        self.sweep_progress.setValue(0)

    def _start_sweep(self) -> None:
        if not self.sweep_plan:
            self._build_sweep()
        if not self.sweep_plan:
            return
        try:
            self.limits.validate(
                frequency_hz=max(s.frequency_hz for s in self.sweep_plan),
                amplitude_vpp=self.stim_amp.value(),
                offset_v=self.stim_offset.value(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Sweep safety", str(exc))
            return
        self.sweep_index = 0
        self.sweep_run.setEnabled(False)
        self.db.add_event(
            self.session_id, time.perf_counter_ns(), "sweep_started",
            {"steps": len(self.sweep_plan)},
        )
        self.sweep_timer.start(max(100, int(self.sweep_dwell.value() * 1000)))
        self._advance_sweep()

    def _advance_sweep(self) -> None:
        if self.sweep_index >= len(self.sweep_plan):
            self.sweep_timer.stop()
            self._emergency_off()
            self.sweep_run.setEnabled(True)
            self.db.add_event(
                self.session_id, time.perf_counter_ns(), "sweep_completed"
            )
            self.db.flush()
            return

        step = self.sweep_plan[self.sweep_index]
        self.stim_freq.setValue(step.frequency_hz)
        if isinstance(self.instrument, SimulatedInstrument):
            self.instrument.frequency_hz = step.frequency_hz
        self.instrument.set_output(True)
        self.interference_status.setText(
            f"SWEEP OUTPUT ON · {step.frequency_hz:.6g} Hz"
        )
        self.db.add_event(
            self.session_id,
            time.perf_counter_ns(),
            "sweep_step",
            {
                "index": step.index,
                "frequency_hz": step.frequency_hz,
                "repetition": step.repetition,
                "amplitude_vpp": self.stim_amp.value(),
            },
        )
        self.sweep_index += 1
        self.sweep_progress.setValue(self.sweep_index)

    def _mode_changed(self, index: int) -> None:
        if self.capture_running:
            self._toggle_capture()
        self.simulation_mode = index == 0
        self.mode_badge.setText(
            "SIMULATION" if self.simulation_mode else "REAL CONTROLLER"
        )
        self.hw_status.setText(
            "● Simulation hardware"
            if self.simulation_mode else
            "● Controller backend ready"
        )
        self.db.add_event(
            self.session_id, time.perf_counter_ns(), "capture_mode",
            {"simulation": self.simulation_mode},
        )
        self.db.flush()

    def _theme_changed(self, index: int) -> None:
        self.dark_mode = index == 0
        self.setStyleSheet(DARK_QSS if self.dark_mode else LIGHT_QSS)

    def _refresh_instruments(self) -> None:
        resources = list_visa_resources()
        rows = [("Simulation Instrument", "Virtual", "AVAILABLE")]
        rows += [(r, "VISA/SCPI", "AVAILABLE") for r in resources]
        self.instrument_table.setRowCount(len(rows))
        for ri, row in enumerate(rows):
            for ci, value in enumerate(row):
                self.instrument_table.setItem(ri, ci, QTableWidgetItem(str(value)))
        if hasattr(self, "instrument_combo"):
            selected = self.instrument_combo.currentText()
            self.instrument_combo.clear()
            self.instrument_combo.addItems(["Simulation Instrument"] + resources)
            index = self.instrument_combo.findText(selected)
            if index >= 0:
                self.instrument_combo.setCurrentIndex(index)
        if hasattr(self, "osc_resource_combo"):
            selected = self.osc_resource_combo.currentText()
            self.osc_resource_combo.clear()
            self.osc_resource_combo.addItems(["Simulation oscillator"] + resources)
            index = self.osc_resource_combo.findText(selected)
            if index >= 0:
                self.osc_resource_combo.setCurrentIndex(index)

    def _export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export session JSON",
            str(self.data_dir / "session.json"),
            "JSON (*.json)",
        )
        if path:
            self.db.flush()
            self.db.export_json(self.session_id, path)

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export controller CSV",
            str(self.data_dir / "controller.csv"),
            "CSV (*.csv)",
        )
        if path:
            self.db.flush()
            self.db.export_controller_csv(self.session_id, path)

    def _generate_report(self) -> None:
        tm = timing_metrics(list(self.timestamps))
        om = oscillator_metrics(list(self.osc_freqs), self.nominal_spin.value())
        path, _ = QFileDialog.getSaveFileName(
            self, "Generate report",
            str(self.data_dir / "GamepadSignalLab_Report.html"),
            "HTML (*.html)",
        )
        if not path:
            return
        write_html_report(
            path,
            title="Gamepad Signal Lab — Experiment Report",
            controller_metrics=tm.to_dict(),
            oscillator_metrics=om.to_dict(),
            metadata={
                "session_id": self.session_id,
                "mode": "simulation" if self.simulation_mode else "real",
                "software_version": __version__,
                "database": str(self.db.path),
                "stimulus_output_enabled": self.instrument.output_enabled(),
            },
            limitations=[
                "Host-side controller timestamps include operating-system and API scheduling effects unless a dedicated timing instrument is used.",
                "Simulation measurements are validation data, not physical hardware measurements."
                if self.simulation_mode else
                "Clock metrics require a connected capable instrument; unavailable values must not be treated as zero.",
                "Correlation is descriptive and does not establish causation.",
            ],
        )
        self.report_status.setText(f"Generated {path}")

    @staticmethod
    def _fill_table(table: QTableWidget, rows: list[tuple]) -> None:
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(value)))

    def closeEvent(self, event) -> None:
        self._emergency_off()
        self._disconnect_oscillator_instrument()
        try:
            self.instrument.close()
        except Exception:
            pass
        self.hw_acquisition.stop()
        self.db.flush()
        self.db.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Gamepad Signal Lab")
    app.setOrganizationName("SensoredRooster")
    app.setApplicationVersion(__version__)
    window = LabWindow()
    window.show()
    return app.exec()
