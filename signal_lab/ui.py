"""Modern Qt desktop shell for Gamepad Signal Lab."""
from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import queue
import threading
import time

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from . import __version__
from .analysis import oscillator_metrics, pearson_correlation, timing_metrics
from .controller import ControllerAcquisition, ControllerMeasurement
from .instruments import SafetyLimits, SimulatedInstrument, VisaScpiGenerator, VisaScpiMeasurementInstrument, list_visa_resources
from .oscillator import OscillatorAcquisition, OscillatorMeasurement
from .reporting import write_html_report
from .simulation import GamepadSimulator, OscillatorSimulator, stimulus_response
from .storage import LabDatabase
from .sweep import make_sweep
from .theme import DARK, LIGHT
from .widgets import HeatMapWidget, LineChart, MetricCard, StickView

NAV = [
    "Dashboard", "Live Capture", "Controller Lab", "Oscillator Lab",
    "Interference Lab", "Sweep Lab", "Correlation", "Experiments",
    "Compare", "Reports", "Instruments", "Settings",
]



def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 15, 16, 16)
    layout.setSpacing(10)
    label = QLabel(title)
    label.setObjectName("Eyebrow")
    layout.addWidget(label)
    return frame, layout


def page(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    outer = QWidget()
    layout = QVBoxLayout(outer)
    layout.setContentsMargins(22, 20, 22, 22)
    layout.setSpacing(14)
    heading = QLabel(title)
    heading.setObjectName("PageTitle")
    desc = QLabel(subtitle)
    desc.setObjectName("Muted")
    desc.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(desc)
    return outer, layout


class WelcomeDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Gamepad Signal Lab")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        title = QLabel("GAMEPAD SIGNAL LAB")
        title.setObjectName("Title")
        layout.addWidget(title)
        copy = QLabel(
            "A measurement-first controller timing and oscillator workstation.\n\n"
            "1  Detect gamepad\n"
            "2  Verify timing engine\n"
            "3  Configure oscillator measurement hardware\n"
            "4  Run baseline\n"
            "5  Optionally configure controlled stimulus\n"
            "6  Start experiment\n\n"
            "Simulation Mode is available without hardware."
        )
        copy.setWordWrap(True)
        layout.addWidget(copy)
        self.hardware = QCheckBox("Start in hardware mode")
        layout.addWidget(self.hardware)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Gamepad Signal Lab {__version__}")
        self.resize(1480, 920)
        self.setMinimumSize(1120, 720)
        self.settings = QSettings("SensoredRooster", "GamepadSignalLab")
        self.theme_name = str(self.settings.value("theme", "Dark"))
        self.setStyleSheet(LIGHT if self.theme_name == "Light" else DARK)

        data_root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "GamepadSignalLab"
        data_root.mkdir(parents=True, exist_ok=True)
        self.data_root = data_root
        self.db = LabDatabase(data_root / "signal_lab.sqlite3")

        self.simulation_mode = True
        self.capture_active = False
        self.session_id: str | None = None
        self.controller_acquisition: ControllerAcquisition | None = None
        self.controller_queue: queue.Queue[ControllerMeasurement] = queue.Queue()
        self.controller_event_queue: queue.Queue = queue.Queue()
        self.osc_queue: queue.Queue = queue.Queue()

        self.instrument = SimulatedInstrument()
        self.measurement_instrument = None
        self.osc_acquisition: OscillatorAcquisition | None = None
        self.instrument_lock = threading.Lock()
        self.safety_limits = SafetyLimits()

        self.gamepad_sim = GamepadSimulator()
        self.osc_sim = OscillatorSimulator()
        self.sim_base_jitter_ms = self.gamepad_sim.config.jitter_ms
        self.sim_base_osc_ppm = self.osc_sim.config.ppm_offset
        self.sim_analog_extra = 0.0
        self.duplicate_raw_reports = 0
        self.sim_epoch_ns = time.perf_counter_ns()
        self.last_sim_wall_ns = time.perf_counter_ns()
        self.sim_sample_accum = 0.0
        self.osc_sample_accum = 0.0

        self.controller_ts = deque(maxlen=60000)
        self.controller_samples = deque(maxlen=60000)
        self.controller_sources = deque(maxlen=60000)
        self.osc_ts = deque(maxlen=12000)
        self.osc_freq = deque(maxlen=12000)
        self.events: list[tuple[int, str, dict]] = []

        self.baseline_active = False
        self.baseline_deadline = 0.0
        self.baseline_controller_ts: list[int] = []
        self.baseline_osc_freq: list[float] = []
        self.last_baseline: dict | None = None
        self.reference_baseline: dict | None = None

        self.sweep_active = False
        self.sweep_plan: list[tuple[float, float, int]] = []
        self.sweep_index = 0
        self.sweep_results: list[tuple[float, float, float | None, float | None]] = []
        self.sweep_step_controller_ts: list[int] = []
        self.sweep_step_osc_freq: list[float] = []
        self.sweep_phase = "idle"
        self.sweep_timer = QTimer(self)
        self.sweep_timer.setSingleShot(True)
        self.sweep_timer.timeout.connect(self._sweep_timer_tick)

        self.current_timing = timing_metrics([])
        self.current_osc = oscillator_metrics([], 12_000_000.0)
        self.current_corr: float | None = None
        self.visualization_paused = False

        self._build_ui()
        self._apply_saved_settings()

        self.sample_timer = QTimer(self)
        self.sample_timer.timeout.connect(self._sample_tick)
        self.sample_timer.start(20)

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._refresh_ui)
        self.ui_timer.start(self.graph_refresh.value())

        if not bool(self.settings.value("welcomed", False, type=bool)):
            QTimer.singleShot(150, self._first_run)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(226)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 14)

        brand = QLabel("GAMEPAD\nSIGNAL LAB")
        brand.setObjectName("Title")
        brand.setStyleSheet("font-size: 15pt;")
        side.addWidget(brand)
        byline = QLabel("Measurement workstation")
        byline.setObjectName("Muted")
        side.addWidget(byline)
        side.addSpacing(12)

        self.nav_buttons: dict[str, QPushButton] = {}
        for index, name in enumerate(NAV):
            button = QPushButton(name)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self._navigate(i))
            side.addWidget(button)
            self.nav_buttons[name] = button

        side.addStretch(1)
        self.hardware_status = QLabel("SIMULATION • no external output")
        self.hardware_status.setObjectName("Good")
        self.hardware_status.setWordWrap(True)
        side.addWidget(self.hardware_status)
        self.database_status = QLabel(f"DB • {self.db.path.name}")
        self.database_status.setObjectName("Muted")
        side.addWidget(self.database_status)
        version = QLabel(f"v{__version__}")
        version.setObjectName("Muted")
        side.addWidget(version)
        root_layout.addWidget(sidebar)

        work = QWidget()
        work_layout = QVBoxLayout(work)
        work_layout.setContentsMargins(0, 0, 0, 0)
        work_layout.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("Topbar")
        top = QHBoxLayout(topbar)
        top.setContentsMargins(18, 10, 18, 10)
        self.top_title = QLabel("Dashboard")
        self.top_title.setObjectName("PageTitle")
        top.addWidget(self.top_title)
        top.addStretch(1)
        self.mode_button = QPushButton("Simulation Mode")
        self.mode_button.clicked.connect(self._toggle_mode)
        top.addWidget(self.mode_button)
        self.baseline_button = QPushButton("Run Baseline")
        self.baseline_button.clicked.connect(self._start_baseline)
        top.addWidget(self.baseline_button)
        self.capture_button = QPushButton("Start Capture")
        self.capture_button.setObjectName("Primary")
        self.capture_button.clicked.connect(self._toggle_capture)
        top.addWidget(self.capture_button)
        work_layout.addWidget(topbar)

        self.stack = QStackedWidget()
        for builder in [
            self._dashboard_page, self._live_page, self._controller_page, self._oscillator_page,
            self._interference_page, self._sweep_page, self._correlation_page,
            self._experiments_page, self._compare_page, self._reports_page,
            self._instruments_page, self._settings_page,
        ]:
            self.stack.addWidget(builder())
        work_layout.addWidget(self.stack, 1)
        root_layout.addWidget(work, 1)
        self._navigate(0)

        emergency = QAction("Emergency Output Off", self)
        emergency.setShortcut("Ctrl+Shift+Esc")
        emergency.triggered.connect(self._emergency_off)
        self.addAction(emergency)

    def _navigate(self, index: int) -> None:
        index = max(0, min(index, self.stack.count() - 1))
        self.stack.setCurrentIndex(index)
        self.top_title.setText(NAV[index])
        for i, name in enumerate(NAV):
            self.nav_buttons[name].setChecked(i == index)
        if NAV[index] == "Experiments":
            self._refresh_experiments()
        elif NAV[index] == "Compare":
            self._refresh_compare()
        elif NAV[index] == "Instruments":
            self._refresh_capabilities()

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _dashboard_page(self) -> QWidget:
        w, layout = page("Dashboard", "Live timing, oscillator stability, measurement quality, and baseline state.")
        grid = QGridLayout()
        self.cards = {}
        for i, (key, title) in enumerate([
            ("rate","Gamepad rate"), ("interval","Report interval"), ("jitter","Gamepad jitter"),
            ("osc","Oscillator"), ("ppm","Clock error"), ("clock_jitter","Clock jitter"),
            ("late","Late reports"), ("stimulus","Test stimulus"),
        ]):
            c = MetricCard(title)
            self.cards[key] = c
            grid.addWidget(c, i // 4, i % 4)
        layout.addLayout(grid)

        baseline, bl = card("BASELINE ENGINE")
        row = QHBoxLayout()
        self.baseline_state = QLabel("No baseline captured")
        self.baseline_state.setObjectName("Muted")
        self.baseline_progress = QProgressBar()
        self.baseline_progress.setRange(0,1000)
        save_baseline = QPushButton("Save Baseline")
        save_baseline.clicked.connect(self._save_baseline)
        set_ref = QPushButton("Set last baseline as reference")
        set_ref.clicked.connect(self._set_reference_baseline)
        row.addWidget(self.baseline_state,1)
        row.addWidget(self.baseline_progress,2)
        row.addWidget(save_baseline)
        row.addWidget(set_ref)
        bl.addLayout(row)
        layout.addWidget(baseline)

        charts = QGridLayout()
        self.dashboard_timing_chart = LineChart("Report interval vs time (ms)")
        self.dashboard_osc_chart = LineChart("Oscillator frequency error (ppm)")
        charts.addWidget(self.dashboard_timing_chart,0,0)
        charts.addWidget(self.dashboard_osc_chart,0,1)
        layout.addLayout(charts)

        q, ql = card("MEASUREMENT QUALITY")
        self.quality_label = QLabel("Waiting for samples")
        self.quality_label.setWordWrap(True)
        ql.addWidget(self.quality_label)
        layout.addWidget(q)
        layout.addStretch(1)
        return self._scroll(w)

    def _live_page(self) -> QWidget:
        w, layout = page("Live Capture", "Raw observations are preserved. Display transformations affect graphs only and never overwrite stored samples.")
        bar = QHBoxLayout()
        self.pause_visualization = QCheckBox("Pause visualization")
        self.pause_visualization.toggled.connect(lambda checked: setattr(self, "visualization_paused", bool(checked)))
        self.smoothing_window = QSpinBox()
        self.smoothing_window.setRange(1, 51)
        self.smoothing_window.setValue(1)
        self.smoothing_window.setPrefix("Display smoothing ")
        self.smoothing_window.setSuffix(" samples")
        reset = QPushButton("Reset graph views")
        reset.clicked.connect(self._reset_graphs)
        export = QPushButton("Export timing graph PNG")
        export.clicked.connect(lambda: self.live_interval_chart.export_png(self))
        fullscreen = QPushButton("Fullscreen timing graph")
        fullscreen.clicked.connect(lambda: self._show_chart_fullscreen(self.live_interval_chart))
        raw = QPushButton("Raw data")
        raw.clicked.connect(self._show_raw_data)
        bar.addWidget(self.pause_visualization)
        bar.addWidget(self.smoothing_window)
        bar.addWidget(reset)
        bar.addWidget(export)
        bar.addWidget(fullscreen)
        bar.addWidget(raw)
        bar.addStretch(1)
        layout.addLayout(bar)
        grid = QGridLayout()
        self.live_interval_chart = LineChart("Report interval vs time (ms)")
        self.live_jitter_chart = LineChart("Timing deviation from expected interval (ms)")
        self.live_hist_chart = LineChart("Report interval histogram")
        self.live_latency_chart = LineChart("Input latency distribution (requires device-origin timestamp)")
        self.live_analog_chart = LineChart("Analog stick stability (LX / LY)")
        self.live_osc_chart = LineChart("Oscillator frequency vs time (Hz)")
        self.live_osc_jitter_chart = LineChart("Oscillator frequency error (ppm)")
        charts = [
            self.live_interval_chart, self.live_jitter_chart, self.live_hist_chart,
            self.live_latency_chart, self.live_analog_chart, self.live_osc_chart, self.live_osc_jitter_chart
        ]
        for i, ch in enumerate(charts):
            grid.addWidget(ch, i//2, i%2)
        layout.addLayout(grid)
        return self._scroll(w)

    def _controller_page(self) -> QWidget:
        w, layout = page("Controller Lab", "Controller discovery, live axes/triggers, timing source, and stationary analog noise.")
        meta, ml = card("CONTROLLER")
        self.controller_meta = QLabel("Simulation controller")
        self.controller_meta.setWordWrap(True)
        ml.addWidget(self.controller_meta)
        layout.addWidget(meta)

        row = QHBoxLayout()
        self.left_stick = StickView("LEFT STICK")
        self.right_stick = StickView("RIGHT STICK")
        row.addWidget(self.left_stick)
        row.addWidget(self.right_stick)
        trg, tl = card("TRIGGERS / CAPABILITIES")
        self.lt_bar = QProgressBar(); self.lt_bar.setRange(0,1000)
        self.rt_bar = QProgressBar(); self.rt_bar.setRange(0,1000)
        tl.addWidget(QLabel("LT")); tl.addWidget(self.lt_bar)
        tl.addWidget(QLabel("RT")); tl.addWidget(self.rt_bar)
        self.axis_noise = QLabel("Stationary noise: waiting for samples")
        self.axis_noise.setObjectName("Muted")
        tl.addWidget(self.axis_noise)
        self.button_capability = QLabel("Buttons / D-pad: waiting for an input sample")
        self.button_capability.setObjectName("Muted")
        self.button_capability.setWordWrap(True)
        tl.addWidget(self.button_capability)
        self.controller_capability = QLabel("Firmware / battery / USB path: shown only when the active backend can report them.")
        self.controller_capability.setObjectName("Muted")
        self.controller_capability.setWordWrap(True)
        tl.addWidget(self.controller_capability)
        row.addWidget(trg,1)
        layout.addLayout(row)
        return self._scroll(w)

    def _oscillator_page(self) -> QWidget:
        w, layout = page("Oscillator Lab", "Frequency, period, ppm error, cycle-to-cycle variation, RMS jitter, drift, and Allan deviation.")
        ref, rl = card("REFERENCE")
        form = QFormLayout()
        self.nominal_freq = QDoubleSpinBox()
        self.nominal_freq.setRange(1, 10_000_000_000)
        self.nominal_freq.setDecimals(3)
        self.nominal_freq.setValue(12_000_000)
        self.nominal_freq.setSuffix(" Hz")
        form.addRow("Nominal frequency", self.nominal_freq)
        rl.addLayout(form)
        instrument_row = QHBoxLayout()
        self.osc_visa_combo = QComboBox()
        osc_refresh = QPushButton("Refresh VISA")
        osc_refresh.clicked.connect(self._refresh_osc_visa)
        osc_connect = QPushButton("Connect Measurement Instrument")
        osc_connect.clicked.connect(self._connect_osc_measurement_instrument)
        osc_disconnect = QPushButton("Disconnect")
        osc_disconnect.clicked.connect(self._disconnect_measurement_instrument)
        instrument_row.addWidget(self.osc_visa_combo, 1)
        instrument_row.addWidget(osc_refresh)
        instrument_row.addWidget(osc_connect)
        instrument_row.addWidget(osc_disconnect)
        rl.addLayout(instrument_row)
        self.osc_instrument_status = QLabel("Simulation oscillator")
        self.osc_instrument_status.setObjectName("Muted")
        rl.addWidget(self.osc_instrument_status)
        layout.addWidget(ref)

        grid = QGridLayout()
        self.osc_labels = {}
        for i,(key,title) in enumerate([
            ("mean","Measured frequency"),("error_hz","Frequency error"),("error_ppm","Error ppm"),
            ("period","Mean period"),("rms","RMS period jitter"),("p2p","Peak-to-peak jitter"),
            ("ctc","Cycle-to-cycle RMS"),("allan","Allan deviation τ=1 sample")
        ]):
            c=MetricCard(title); self.osc_labels[key]=c; grid.addWidget(c,i//4,i%4)
        layout.addLayout(grid)
        self.osc_stability_chart = LineChart("Frequency stability (Hz)")
        self.osc_period_chart = LineChart("Period (ns)")
        layout.addWidget(self.osc_stability_chart)
        layout.addWidget(self.osc_period_chart)
        return self._scroll(w)

    def _interference_page(self) -> QWidget:
        w, layout = page("Interference Lab", "Controlled bench stimulus. External output always starts OFF and must be explicitly enabled.")
        stat, sl = card("ACTIVE INSTRUMENT")
        self.interference_status = QLabel("Simulation Instrument • OUTPUT OFF")
        self.interference_status.setWordWrap(True)
        sl.addWidget(self.interference_status)
        layout.addWidget(stat)

        cfg, cl = card("STIMULUS CONFIGURATION")
        form = QFormLayout()
        self.waveform_combo = QComboBox(); self.waveform_combo.addItems(["SINE","SQU","RAMP","PULS","NOIS"])
        self.stim_freq = QDoubleSpinBox(); self.stim_freq.setRange(0,100_000_000); self.stim_freq.setValue(1000); self.stim_freq.setSuffix(" Hz")
        self.stim_amp = QDoubleSpinBox(); self.stim_amp.setRange(0,20); self.stim_amp.setDecimals(4); self.stim_amp.setValue(0.10); self.stim_amp.setSuffix(" Vpp")
        self.stim_offset = QDoubleSpinBox(); self.stim_offset.setRange(-10,10); self.stim_offset.setDecimals(4); self.stim_offset.setSuffix(" V")
        form.addRow("Waveform",self.waveform_combo); form.addRow("Frequency",self.stim_freq)
        form.addRow("Amplitude",self.stim_amp); form.addRow("Offset",self.stim_offset)
        cl.addLayout(form)
        actions=QHBoxLayout()
        apply_btn=QPushButton("Apply settings"); apply_btn.clicked.connect(self._apply_stimulus_settings)
        self.output_button=QPushButton("Enable Output"); self.output_button.setCheckable(True); self.output_button.clicked.connect(self._toggle_instrument_output)
        off=QPushButton("EMERGENCY OUTPUT OFF"); off.setObjectName("Danger"); off.clicked.connect(self._emergency_off)
        actions.addWidget(apply_btn); actions.addWidget(self.output_button); actions.addStretch(1); actions.addWidget(off)
        cl.addLayout(actions)
        self.safety_label=QLabel(); self.safety_label.setObjectName("Muted"); self.safety_label.setWordWrap(True); cl.addWidget(self.safety_label)
        layout.addWidget(cfg)
        return self._scroll(w)

    def _sweep_page(self) -> QWidget:
        w, layout = page("Sweep Lab", "Automated linear/log frequency sweeps with amplitude steps, dwell, settling, repetitions, and randomized ordering.")
        cfg, cl = card("SWEEP PLAN")
        grid=QGridLayout()
        self.sweep_start=QDoubleSpinBox(); self.sweep_start.setRange(1,100_000_000); self.sweep_start.setValue(100)
        self.sweep_stop=QDoubleSpinBox(); self.sweep_stop.setRange(1,100_000_000); self.sweep_stop.setValue(10000)
        self.sweep_steps=QSpinBox(); self.sweep_steps.setRange(1,500); self.sweep_steps.setValue(8)
        self.sweep_log=QCheckBox("Logarithmic")
        self.amp_start=QDoubleSpinBox(); self.amp_start.setRange(0,20); self.amp_start.setDecimals(4); self.amp_start.setValue(0.05)
        self.amp_stop=QDoubleSpinBox(); self.amp_stop.setRange(0,20); self.amp_stop.setDecimals(4); self.amp_stop.setValue(0.20)
        self.amp_steps=QSpinBox(); self.amp_steps.setRange(1,100); self.amp_steps.setValue(3)
        self.dwell_ms=QSpinBox(); self.dwell_ms.setRange(100,60000); self.dwell_ms.setValue(1000); self.dwell_ms.setSuffix(" ms")
        self.settle_ms=QSpinBox(); self.settle_ms.setRange(0,60000); self.settle_ms.setValue(250); self.settle_ms.setSuffix(" ms")
        self.sweep_reps=QSpinBox(); self.sweep_reps.setRange(1,50); self.sweep_reps.setValue(1)
        self.sweep_random=QCheckBox("Randomized order")
        self.sweep_enable_output=QCheckBox("Explicitly enable instrument output for this sweep")
        specs=[
            ("Start frequency",self.sweep_start),("Stop frequency",self.sweep_stop),("Frequency steps",self.sweep_steps),("Sweep type",self.sweep_log),
            ("Amplitude start Vpp",self.amp_start),("Amplitude stop Vpp",self.amp_stop),("Amplitude steps",self.amp_steps),("Dwell",self.dwell_ms),
            ("Settling",self.settle_ms),("Repetitions",self.sweep_reps),("Ordering",self.sweep_random),("Output authorization",self.sweep_enable_output),
        ]
        for i,(label,widget) in enumerate(specs):
            grid.addWidget(QLabel(label),i//2,(i%2)*2); grid.addWidget(widget,i//2,(i%2)*2+1)
        cl.addLayout(grid)
        actions=QHBoxLayout()
        self.sweep_estimate=QLabel("Estimated duration: —")
        start=QPushButton("Start Sweep"); start.setObjectName("Primary"); start.clicked.connect(self._start_sweep)
        stop=QPushButton("Stop Sweep"); stop.clicked.connect(self._stop_sweep)
        actions.addWidget(self.sweep_estimate,1); actions.addWidget(start); actions.addWidget(stop)
        cl.addLayout(actions); layout.addWidget(cfg)
        heatbar=QHBoxLayout()
        heatbar.addWidget(QLabel("Heatmap metric"))
        self.sweep_heatmap_metric=QComboBox()
        self.sweep_heatmap_metric.addItem("Gamepad RMS timing deviation (ms)","gamepad")
        self.sweep_heatmap_metric.addItem("Oscillator clock error (ppm)","clock")
        self.sweep_heatmap_metric.currentIndexChanged.connect(self._refresh_sweep_heatmap)
        heatbar.addWidget(self.sweep_heatmap_metric)
        heatbar.addStretch(1)
        layout.addLayout(heatbar)
        self.sweep_heatmap=HeatMapWidget("Frequency / amplitude response • gamepad RMS timing deviation")
        layout.addWidget(self.sweep_heatmap)
        self.sweep_table=QTableWidget(0,6)
        self.sweep_table.setHorizontalHeaderLabels(["#","Frequency Hz","Amplitude Vpp","Gamepad RMS ms","Clock ppm","Status"])
        self.sweep_table.setAlternatingRowColors(True)
        layout.addWidget(self.sweep_table)
        for ctl in [self.sweep_start,self.sweep_stop,self.sweep_steps,self.amp_start,self.amp_stop,self.amp_steps,self.dwell_ms,self.settle_ms,self.sweep_reps]:
            ctl.valueChanged.connect(self._update_sweep_estimate)
        self.sweep_log.toggled.connect(self._update_sweep_estimate)
        self._update_sweep_estimate()
        return self._scroll(w)

    def _correlation_page(self) -> QWidget:
        w, layout = page("Correlation", "Signals share a common experiment timeline. Correlation is descriptive and is not treated as proof of causation.")
        self.corr_card=MetricCard("Aligned correlation coefficient","—","Gamepad interval deviation vs nearest oscillator ppm sample")
        layout.addWidget(self.corr_card)
        self.corr_stimulus_chart=LineChart("Stimulus frequency (Hz)")
        self.corr_osc_chart=LineChart("Oscillator error (ppm)")
        self.corr_gamepad_chart=LineChart("Gamepad report timing deviation (ms)")
        layout.addWidget(self.corr_stimulus_chart); layout.addWidget(self.corr_osc_chart); layout.addWidget(self.corr_gamepad_chart)
        return self._scroll(w)

    def _experiments_page(self) -> QWidget:
        w, layout = page("Experiments", "Recorded sessions stored in the local SQLite experiment database.")
        refresh=QPushButton("Refresh experiments"); refresh.clicked.connect(self._refresh_experiments); layout.addWidget(refresh,alignment=Qt.AlignmentFlag.AlignLeft)
        self.experiments_table=QTableWidget(0,7)
        self.experiments_table.setHorizontalHeaderLabels(["Created UTC","Name","Mode","Controller samples","Oscillator samples","Events","Session ID"])
        self.experiments_table.setAlternatingRowColors(True)
        layout.addWidget(self.experiments_table)
        return w

    def _compare_page(self) -> QWidget:
        w, layout = page("Compare", "Compare the current capture against the selected baseline reference.")
        self.compare_state=QLabel("No reference baseline selected."); self.compare_state.setObjectName("Muted"); layout.addWidget(self.compare_state)
        self.compare_table=QTableWidget(0,5); self.compare_table.setHorizontalHeaderLabels(["Metric","Reference","Current","Difference","% Change"]); layout.addWidget(self.compare_table)
        return w

    def _reports_page(self) -> QWidget:
        w, layout = page("Reports", "Export raw session data and a measurement-methodology-aware engineering report.")
        actions=QHBoxLayout()
        for label,fn in [("Export JSON",self._export_json),("Export Controller CSV",self._export_csv),("Generate HTML Engineering Report",self._export_html_report)]:
            b=QPushButton(label); b.clicked.connect(fn); actions.addWidget(b)
        actions.addStretch(1); layout.addLayout(actions)
        self.timeline_table=QTableWidget(0,3); self.timeline_table.setHorizontalHeaderLabels(["Timestamp ns","Event","Details"]); self.timeline_table.setAlternatingRowColors(True)
        layout.addWidget(self.timeline_table)
        return w

    def _instruments_page(self) -> QWidget:
        w, layout = page("Instruments", "VISA/SCPI discovery with separate read-only measurement and signal-generator roles. Unsupported measurements stay explicitly unavailable.")
        d, dl=card("DISCOVERY")
        row=QHBoxLayout()
        self.visa_combo=QComboBox()
        refresh=QPushButton("Refresh VISA"); refresh.clicked.connect(self._refresh_visa)
        measure=QPushButton("Connect as Measurement"); measure.clicked.connect(self._connect_measurement_instrument)
        generator=QPushButton("Connect as Generator"); generator.clicked.connect(self._connect_generator_instrument)
        disconnect=QPushButton("Disconnect All / Output Off"); disconnect.clicked.connect(self._disconnect_instrument)
        row.addWidget(self.visa_combo,1); row.addWidget(refresh); row.addWidget(measure); row.addWidget(generator); row.addWidget(disconnect)
        dl.addLayout(row)
        self.measurement_id=QLabel("Measurement: not connected"); self.measurement_id.setWordWrap(True); dl.addWidget(self.measurement_id)
        self.generator_id=QLabel("Generator: Simulation Instrument • OUTPUT OFF"); self.generator_id.setWordWrap(True); dl.addWidget(self.generator_id)
        layout.addWidget(d)
        self.cap_table=QTableWidget(0,3); self.cap_table.setHorizontalHeaderLabels(["Role","Capability","Status"]); layout.addWidget(self.cap_table)
        off=QPushButton("EMERGENCY OUTPUT OFF"); off.setObjectName("Danger"); off.clicked.connect(self._emergency_off); layout.addWidget(off,alignment=Qt.AlignmentFlag.AlignRight)
        return w

    def _settings_page(self) -> QWidget:
        w, layout = page("Settings", "Measurement, safety, data, and performance controls.")
        g, gl=card("GENERAL / MEASUREMENT")
        form=QFormLayout()
        self.theme_combo=QComboBox(); self.theme_combo.addItems(["Dark","Light"]); self.theme_combo.setCurrentText(self.theme_name); self.theme_combo.currentTextChanged.connect(self._change_theme)
        self.baseline_seconds=QSpinBox(); self.baseline_seconds.setRange(5,3600); self.baseline_seconds.setValue(60); self.baseline_seconds.setSuffix(" s")
        self.expected_rate=QDoubleSpinBox(); self.expected_rate.setRange(1,8000); self.expected_rate.setValue(1000); self.expected_rate.setSuffix(" Hz")
        self.late_factor=QDoubleSpinBox(); self.late_factor.setRange(1.01,10.0); self.late_factor.setDecimals(2); self.late_factor.setValue(1.50); self.late_factor.setSuffix(" × expected interval")
        self.graph_refresh=QSpinBox(); self.graph_refresh.setRange(33,1000); self.graph_refresh.setValue(100); self.graph_refresh.setSuffix(" ms")
        self.graph_refresh.valueChanged.connect(lambda v: self.ui_timer.setInterval(v) if hasattr(self,"ui_timer") else None)
        form.addRow("Theme",self.theme_combo); form.addRow("Default baseline duration",self.baseline_seconds); form.addRow("Expected polling rate",self.expected_rate); form.addRow("Late-report threshold",self.late_factor); form.addRow("Graph refresh interval",self.graph_refresh)
        gl.addLayout(form); layout.addWidget(g)

        s, sl=card("INSTRUMENT SAFETY LIMITS")
        sf=QFormLayout()
        self.max_freq=QDoubleSpinBox(); self.max_freq.setRange(1,1e9); self.max_freq.setValue(self.safety_limits.max_frequency_hz); self.max_freq.setSuffix(" Hz")
        self.max_amp=QDoubleSpinBox(); self.max_amp.setRange(.001,100); self.max_amp.setValue(self.safety_limits.max_amplitude_vpp); self.max_amp.setSuffix(" Vpp")
        self.max_offset=QDoubleSpinBox(); self.max_offset.setRange(.001,100); self.max_offset.setValue(self.safety_limits.max_abs_offset_v); self.max_offset.setSuffix(" V")
        sf.addRow("Maximum frequency",self.max_freq); sf.addRow("Maximum amplitude",self.max_amp); sf.addRow("Maximum absolute offset",self.max_offset)
        save=QPushButton("Save Settings"); save.clicked.connect(self._save_settings)
        sl.addLayout(sf); sl.addWidget(save,alignment=Qt.AlignmentFlag.AlignRight); layout.addWidget(s)

        dbcard, dbl=card("DATA")
        label=QLabel(f"SQLite database:\n{self.db.path}\n\nRaw samples are retained and can be exported from Reports.")
        label.setWordWrap(True); dbl.addWidget(label); layout.addWidget(dbcard); layout.addStretch(1)
        return self._scroll(w)

    def _toggle_mode(self) -> None:
        if self.capture_active:
            QMessageBox.information(self,"Capture active","Stop the active capture before changing acquisition mode.")
            return
        if self.simulation_mode:
            self.simulation_mode=False
            self.mode_button.setText("Hardware Mode")
            self.hardware_status.setText("HARDWARE • controller discovery active")
            self.hardware_status.setObjectName("Warn")
            self.controller_acquisition=ControllerAcquisition(self.controller_queue.put, lambda name, payload: self.controller_event_queue.put((name, payload)))
            self.controller_acquisition.start()
            if hasattr(self,"osc_instrument_status"):
                self.osc_instrument_status.setText("No measurement instrument connected")
            self._add_event("hardware_mode_enabled",{})
        else:
            self.simulation_mode=True
            if self.controller_acquisition:
                self.controller_acquisition.stop()
                self.controller_acquisition=None
            self._disconnect_instrument(quiet=True)
            self.instrument=SimulatedInstrument()
            self.mode_button.setText("Simulation Mode")
            self.hardware_status.setText("SIMULATION • no external output")
            self.hardware_status.setObjectName("Good")
            self._add_event("simulation_mode_enabled",{})
        self.style().unpolish(self.hardware_status); self.style().polish(self.hardware_status)

    def _toggle_capture(self) -> None:
        self._stop_capture() if self.capture_active else self._start_capture()

    def _start_capture(self) -> None:
        if self.capture_active:
            return
        mode="simulation" if self.simulation_mode else "hardware"
        self.session_id=self.db.create_session("Gamepad Signal Lab capture",mode,__version__,{"nominal_frequency_hz":self.nominal_freq.value(),"expected_rate_hz":self.expected_rate.value()})
        self.capture_active=True
        self.capture_button.setText("Stop Capture")
        self._add_event("capture_started",{"mode":mode})

    def _stop_capture(self) -> None:
        if not self.capture_active:
            return
        self._add_event("capture_stopped",{})
        self.db.flush()
        self.capture_active=False
        self.capture_button.setText("Start Capture")

    def _sample_tick(self) -> None:
        now=time.perf_counter_ns()
        if self.simulation_mode:
            elapsed=max(0,(now-self.last_sim_wall_ns)/1e9)
            self.last_sim_wall_ns=now
            try:
                sim_output = self.instrument.output_enabled()
            except Exception:
                sim_output = False
            if sim_output and isinstance(self.instrument,SimulatedInstrument):
                response = stimulus_response(self.instrument.frequency_hz, self.instrument.amplitude_vpp)
                self.gamepad_sim.config.jitter_ms = self.sim_base_jitter_ms + response.gamepad_extra_jitter_ms
                self.osc_sim.config.ppm_offset = self.sim_base_osc_ppm + response.oscillator_extra_ppm
                self.sim_analog_extra = response.analog_noise_extra
            else:
                self.gamepad_sim.config.jitter_ms = self.sim_base_jitter_ms
                self.osc_sim.config.ppm_offset = self.sim_base_osc_ppm
                self.sim_analog_extra = 0.0

            rate=max(1.0,self.gamepad_sim.config.rate_hz)
            self.sim_sample_accum += elapsed*rate
            count=min(400,int(self.sim_sample_accum)); self.sim_sample_accum -= count
            for _ in range(count):
                rel=self.gamepad_sim.next_timestamp_ns()
                sample=self.gamepad_sim.sample()
                if self.sim_analog_extra:
                    extra=self.sim_analog_extra*math.sin(2*math.pi*997.0*(rel/1e9))
                    for axis in ("lx","ly","rx","ry"):
                        sample[axis]=max(-1.0,min(1.0,float(sample.get(axis,0.0))+extra))
                self._accept_controller(self.sim_epoch_ns+rel,sample,"Simulation controller","simulated")

            # A physical clock instrument can be used while the controller side
            # remains simulated. Only synthesize oscillator samples when no
            # physical measurement source is attached.
            if self.measurement_instrument is None:
                self.osc_sample_accum += elapsed/0.05
                oc=min(10,int(self.osc_sample_accum)); self.osc_sample_accum -= oc
                for _ in range(oc):
                    self._accept_oscillator(now,self.osc_sim.next_frequency_hz(0.05),"Simulation oscillator","simulated")
        else:
            while True:
                try:
                    event_name,event_payload=self.controller_event_queue.get_nowait()
                except queue.Empty:
                    break
                self._add_event(event_name,event_payload)
            for _ in range(1000):
                try:
                    m=self.controller_queue.get_nowait()
                except queue.Empty:
                    break
                self._accept_controller(m.timestamp_ns,m.sample,m.source,m.timing_quality,m.raw_report_hex,m.duplicate_raw_report)

        # Oscillator acquisition is independent of controller acquisition mode.
        # OscillatorAcquisition emits OscillatorMeasurement objects; tuple support
        # remains for compatibility with older queued poller data.
        while True:
            try:
                item=self.osc_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item,OscillatorMeasurement):
                self._accept_oscillator(
                    item.timestamp_ns,item.frequency_hz,item.source,"measured",
                    item.duty_cycle_percent,
                )
            elif isinstance(item,tuple) and item and item[0]=="error":
                message=str(item[1]) if len(item)>1 else "unknown measurement error"
                if hasattr(self,"osc_instrument_status"):
                    self.osc_instrument_status.setText("Measurement error: "+message)
                self._add_event("instrument_error",{"message":message,"role":"measurement"})
            elif isinstance(item,tuple) and len(item)>=3:
                source=self.measurement_id.text().removeprefix("Measurement: ").strip() if hasattr(self,"measurement_id") else "Measurement instrument"
                self._accept_oscillator(int(item[0]),float(item[1]),source,str(item[2]))

        if self.baseline_active and time.monotonic()>=self.baseline_deadline:
            self._finish_baseline()

    def _accept_controller(self,timestamp_ns:int,sample:dict,source:str,quality:str,raw_hex:str|None=None,duplicate_raw:bool=False) -> None:
        if not all(k in sample for k in ("lx","ly","rx","ry")):
            return
        self.controller_ts.append(int(timestamp_ns)); self.controller_samples.append(dict(sample)); self.controller_sources.append((source,quality))
        if duplicate_raw:
            self.duplicate_raw_reports += 1
        if self.baseline_active: self.baseline_controller_ts.append(int(timestamp_ns))
        if self.sweep_active and self.sweep_phase=="dwell":
            self.sweep_step_controller_ts.append(int(timestamp_ns))
        if self.capture_active and self.session_id:
            self.db.add_controller_sample(self.session_id,timestamp_ns,sample,source=f"{source} [{quality}]",raw_report_hex=raw_hex)

    def _accept_oscillator(self,timestamp_ns:int,frequency_hz:float,source:str,quality:str,duty_cycle_percent:float|None=None) -> None:
        self.osc_ts.append(int(timestamp_ns)); self.osc_freq.append(float(frequency_hz))
        if self.baseline_active: self.baseline_osc_freq.append(float(frequency_hz))
        if self.sweep_active and self.sweep_phase=="dwell":
            self.sweep_step_osc_freq.append(float(frequency_hz))
        if self.capture_active and self.session_id:
            self.db.add_oscillator_sample(self.session_id,timestamp_ns,frequency_hz,source=source,duty_cycle_percent=duty_cycle_percent,quality=quality)

    def _refresh_ui(self) -> None:
        timestamps=list(self.controller_ts)[-5000:]
        expected_ms=1000.0/max(self.expected_rate.value(),1.0)
        self.current_timing=timing_metrics(timestamps,expected_interval_ms=expected_ms,late_factor=self.late_factor.value())
        freqs=list(self.osc_freq)[-3000:]
        nominal=self.nominal_freq.value()
        self.current_osc=oscillator_metrics(freqs,nominal)
        self.current_corr=self._aligned_correlation(timestamps,list(self.osc_ts),freqs,nominal)
        t,o=self.current_timing,self.current_osc

        self.cards["rate"].set_value(f"{t.effective_rate_hz:,.1f} Hz","Measured from observed timestamps")
        self.cards["interval"].set_value(f"{t.mean_interval_ms:.4f} ms",f"min {t.min_interval_ms:.4f} • max {t.max_interval_ms:.4f}")
        self.cards["jitter"].set_value(f"{t.rms_deviation_ms:.4f} ms",f"p2p {t.peak_to_peak_jitter_ms:.4f}")
        self.cards["osc"].set_value(f"{o.mean_frequency_hz/1e6:.6f} MHz" if o.sample_count else "—","Source capability determines precision")
        self.cards["ppm"].set_value(f"{o.frequency_error_ppm:+.3f} ppm" if o.sample_count else "—",f"{o.frequency_error_hz:+.3f} Hz" if o.sample_count else "")
        self.cards["clock_jitter"].set_value(f"{o.rms_period_jitter_s*1e12:.3f} ps" if o.sample_count else "—","Calculated from sampled frequency/period")
        self.cards["late"].set_value(str(t.late_reports),f"missing estimate {t.missing_reports_estimate}")

        try: output=self.instrument.output_enabled()
        except Exception: output=False
        self.cards["stimulus"].set_value("ON" if output else "OFF",f"{self.stim_freq.value():g} Hz • {self.stim_amp.value():g} Vpp")
        generator_name=self.generator_id.text().removeprefix("Generator: ").split(" • ")[0] if hasattr(self,"generator_id") else self.instrument.identify()
        self.interference_status.setText(f"{generator_name} • OUTPUT {'ON' if output else 'OFF'}")
        if hasattr(self,"generator_id"):
            self.generator_id.setText(f"Generator: {generator_name} • OUTPUT {'ON' if output else 'OFF'}")
        self.output_button.setChecked(output); self.output_button.setText("Disable Output" if output else "Enable Output")

        intervals=[(b-a)/1e6 for a,b in zip(timestamps,timestamps[1:]) if b>a]
        deviations=[v-expected_ms for v in intervals]
        ppm_values=[(f-nominal)/nominal*1e6 for f in freqs] if nominal>0 else []
        self.dashboard_timing_chart.set_series([("interval",intervals[-500:],"#6AA2FF")])
        self.dashboard_osc_chart.set_series([("ppm",ppm_values[-500:],"#6DE0B1")])
        samples=list(self.controller_samples)[-800:]
        if not self.visualization_paused:
            smooth=max(1,self.smoothing_window.value()) if hasattr(self,"smoothing_window") else 1
            smooth_fn=lambda values: self._moving_average(values,smooth)
            self.live_interval_chart.set_series([("interval",smooth_fn(intervals[-800:]),"#6AA2FF")])
            self.live_jitter_chart.set_series([("deviation",smooth_fn(deviations[-800:]),"#F0B862")])
            self.live_hist_chart.set_series([("count",self._histogram(intervals[-3000:],32),"#A989FF")])
            self.live_latency_chart.set_series([])
            self.live_analog_chart.set_series([
                ("LX",smooth_fn([float(item.get("lx",0)) for item in samples]),"#6AA2FF"),
                ("LY",smooth_fn([float(item.get("ly",0)) for item in samples]),"#6DE0B1")
            ])
            self.live_osc_chart.set_series([("frequency",smooth_fn(freqs[-800:]),"#6DE0B1")])
            self.live_osc_jitter_chart.set_series([("ppm",smooth_fn(ppm_values[-800:]),"#F0B862")])
        self.osc_stability_chart.set_series([("frequency",freqs[-1200:],"#6DE0B1")])
        self.osc_period_chart.set_series([("period ns",[(1/f)*1e9 for f in freqs[-1200:] if f>0],"#6AA2FF")])

        self.osc_labels["mean"].set_value(f"{o.mean_frequency_hz:,.6f} Hz" if o.sample_count else "—")
        self.osc_labels["error_hz"].set_value(f"{o.frequency_error_hz:+.6f} Hz" if o.sample_count else "—")
        self.osc_labels["error_ppm"].set_value(f"{o.frequency_error_ppm:+.6f} ppm" if o.sample_count else "—")
        self.osc_labels["period"].set_value(f"{o.mean_period_s*1e9:.6f} ns" if o.sample_count else "—")
        self.osc_labels["rms"].set_value(f"{o.rms_period_jitter_s*1e12:.3f} ps" if o.sample_count else "—")
        self.osc_labels["p2p"].set_value(f"{o.peak_to_peak_period_jitter_s*1e12:.3f} ps" if o.sample_count else "—")
        self.osc_labels["ctc"].set_value(f"{o.cycle_to_cycle_rms_s*1e12:.3f} ps" if o.sample_count else "—")
        self.osc_labels["allan"].set_value(f"{o.allan_deviation_tau1:.3e}" if o.allan_deviation_tau1 is not None else "Unavailable")

        if samples:
            last=samples[-1]
            self.left_stick.set_position(last.get("lx",0),last.get("ly",0)); self.right_stick.set_position(last.get("rx",0),last.get("ry",0))
            self.lt_bar.setValue(int(float(last.get("lt",0))*1000)); self.rt_bar.setValue(int(float(last.get("rt",0))*1000))
            rolling=samples[-250:]
            chunks=[]
            for axis in ("lx","ly","rx","ry"):
                vals=[float(s.get(axis,0)) for s in rolling]
                mean=sum(vals)/len(vals)
                rms=math.sqrt(sum((v-mean)**2 for v in vals)/len(vals))
                chunks.append(f"{axis.upper()} {rms:.5f}")
            self.axis_noise.setText("Stationary-window RMS: "+" • ".join(chunks))
            if "buttons" in last:
                parts=[f"Buttons mask: 0x{int(last.get('buttons',0)):04X}"]
                if "dpad_x" in last or "dpad_y" in last:
                    parts.append(f"D-pad: ({int(last.get('dpad_x',0))}, {int(last.get('dpad_y',0))})")
                elif "dpad_pov" in last:
                    pov=int(last.get("dpad_pov",65535))
                    parts.append("D-pad POV: centered" if pov in (65535,4294967295) else f"D-pad POV: {pov/100:.1f}°")
                self.button_capability.setText(" • ".join(parts))
            else:
                self.button_capability.setText("Buttons / D-pad: unavailable from the active decoded backend")
        if self.controller_sources:
            source,quality=self.controller_sources[-1]
            self.controller_meta.setText(f"{source}\nTiming source quality: {quality}")

        self.corr_card.set_value(f"{self.current_corr:+.4f}" if self.current_corr is not None else "Unavailable","Nearest-time aligned samples; correlation does not establish causation.")
        self.corr_osc_chart.set_series([("osc ppm",ppm_values[-600:],"#6DE0B1")])
        self.corr_gamepad_chart.set_series([("gamepad dev",deviations[-600:],"#6AA2FF")])
        self.corr_stimulus_chart.set_series([("stimulus",[self.stim_freq.value() if output else 0.0]*min(600,max(len(ppm_values),len(deviations))),"#F0B862")])

        timing_source=self.controller_sources[-1][1] if self.controller_sources else "none"
        self.quality_label.setText(
            f"Samples: {t.sample_count:,} • capture duration: {t.duration_s:.3f} s • timing source: {timing_source} • "
            f"effective rate: {t.effective_rate_hz:.2f} Hz • consecutive identical raw HID payloads: {self.duplicate_raw_reports}. "
            "Host timestamps include OS/USB scheduling unless dedicated timing hardware supplies timestamps."
        )
        self.safety_label.setText(
            f"Configured limits: {self.safety_limits.max_frequency_hz:g} Hz • {self.safety_limits.max_amplitude_vpp:g} Vpp • "
            f"±{self.safety_limits.max_abs_offset_v:g} V. Output defaults OFF."
        )

        if self.baseline_active:
            duration=self.baseline_seconds.value()
            remaining=max(0,self.baseline_deadline-time.monotonic())
            self.baseline_progress.setValue(int((1-remaining/max(1,duration))*1000))
            self.baseline_state.setText(f"Baseline capture running • {remaining:.1f}s remaining")
        self.db.flush()

    @staticmethod
    def _moving_average(values:list[float],window:int) -> list[float]:
        if window<=1 or len(values)<2:
            return list(values)
        output=[]
        running=0.0
        q=deque()
        for value in values:
            value=float(value)
            q.append(value); running+=value
            if len(q)>window:
                running-=q.popleft()
            output.append(running/len(q))
        return output

    def _show_chart_fullscreen(self,source:LineChart) -> None:
        dlg=QDialog(self)
        dlg.setWindowTitle(source.title)
        dlg.resize(1280,760)
        layout=QVBoxLayout(dlg)
        chart=LineChart(source.title)
        chart.set_series([(name,values,color.name()) for name,values,color in source.series])
        layout.addWidget(chart)
        close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dlg.reject); close.accepted.connect(dlg.accept)
        layout.addWidget(close)
        dlg.exec()

    def _show_raw_data(self) -> None:
        dlg=QDialog(self)
        dlg.setWindowTitle("Raw Capture Data")
        dlg.resize(960,700)
        layout=QVBoxLayout(dlg)
        editor=QPlainTextEdit()
        editor.setReadOnly(True)
        payload={
            "controller":[
                {"timestamp_ns":ts,"sample":sample,"source":source[0],"timing_quality":source[1]}
                for ts,sample,source in zip(list(self.controller_ts)[-200:],list(self.controller_samples)[-200:],list(self.controller_sources)[-200:])
            ],
            "oscillator":[
                {"timestamp_ns":ts,"frequency_hz":freq}
                for ts,freq in zip(list(self.osc_ts)[-100:],list(self.osc_freq)[-100:])
            ],
        }
        editor.setPlainText(json.dumps(payload,indent=2))
        layout.addWidget(editor)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject); buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def _save_baseline(self) -> None:
        if not self.last_baseline:
            QMessageBox.information(self,"Baseline","Run a baseline first.")
            return
        path,_=QFileDialog.getSaveFileName(self,"Save Baseline",str(self.data_root/"baseline.json"),"JSON (*.json)")
        if not path:
            return
        payload={
            "saved_utc":time.time(),
            "app_version":__version__,
            "session_id":self.session_id,
            "baseline":self.last_baseline,
        }
        Path(path).write_text(json.dumps(payload,indent=2),encoding="utf-8")
        self._add_event("baseline_saved",{"path":str(path)})

    @staticmethod
    def _histogram(values:list[float],bins:int) -> list[float]:
        if not values: return []
        lo,hi=min(values),max(values)
        if math.isclose(lo,hi): return [float(len(values))]
        counts=[0.0]*bins
        for v in values:
            idx=min(bins-1,max(0,int((v-lo)/(hi-lo)*bins)))
            counts[idx]+=1
        return counts

    @staticmethod
    def _aligned_correlation(controller_ts:list[int],osc_ts:list[int],osc_freq:list[float],nominal:float) -> float|None:
        if len(controller_ts)<3 or len(osc_ts)<2 or nominal<=0: return None
        intervals=[]; midpoints=[]
        segment=controller_ts[-600:]
        for a,b in zip(segment,segment[1:]):
            if b>a:
                intervals.append((b-a)/1e6); midpoints.append((a+b)//2)
        if len(intervals)<3: return None
        expected=sorted(intervals)[len(intervals)//2]
        deviations=[x-expected for x in intervals]
        times=osc_ts[-len(osc_freq):]; freqs=osc_freq[-len(times):]
        paired_ppm=[]; paired_dev=[]
        for ts,dev in zip(midpoints,deviations):
            idx=bisect_left(times,ts)
            candidates=[i for i in (idx-1,idx) if 0<=i<len(times)]
            if not candidates: continue
            nearest=min(candidates,key=lambda i:abs(times[i]-ts))
            paired_ppm.append((freqs[nearest]-nominal)/nominal*1e6); paired_dev.append(dev)
        return pearson_correlation(paired_dev,paired_ppm)

    def _start_baseline(self) -> None:
        if self.baseline_active: return
        if not self.capture_active: self._start_capture()
        self.baseline_controller_ts=[]; self.baseline_osc_freq=[]
        self.baseline_deadline=time.monotonic()+self.baseline_seconds.value()
        self.baseline_active=True; self.baseline_progress.setValue(0)
        self._add_event("baseline_started",{"duration_s":self.baseline_seconds.value()})

    def _finish_baseline(self) -> None:
        self.baseline_active=False
        t=timing_metrics(self.baseline_controller_ts,expected_interval_ms=1000.0/max(self.expected_rate.value(),1),late_factor=self.late_factor.value())
        o=oscillator_metrics(self.baseline_osc_freq,self.nominal_freq.value())
        self.last_baseline={"timing":asdict(t),"oscillator":asdict(o)}
        self.baseline_progress.setValue(1000)
        self.baseline_state.setText(f"Baseline complete • {t.sample_count:,} controller samples • {o.sample_count:,} oscillator samples")
        self._add_event("baseline_completed",self.last_baseline)
        self._refresh_compare()

    def _set_reference_baseline(self) -> None:
        if not self.last_baseline:
            QMessageBox.information(self,"Baseline","Run a baseline first."); return
        self.reference_baseline=json.loads(json.dumps(self.last_baseline))
        self.baseline_state.setText(self.baseline_state.text()+" • REFERENCE")
        self._add_event("baseline_set_reference",{})
        self._refresh_compare()

    def _refresh_visa(self) -> None:
        resources=list_visa_resources()
        if hasattr(self,"visa_combo"):
            self.visa_combo.clear()
            self.visa_combo.addItems(resources or ["No VISA resources found"])
        if hasattr(self,"osc_visa_combo"):
            self.osc_visa_combo.clear()
            self.osc_visa_combo.addItems(resources or ["No VISA resources found"])

    def _refresh_osc_visa(self) -> None:
        resources=list_visa_resources()
        self.osc_visa_combo.clear()
        self.osc_visa_combo.addItems(resources or ["No VISA resources found"])

    def _selected_visa_resource(self) -> str | None:
        resource=self.visa_combo.currentText().strip()
        if not resource or resource.startswith("No VISA"):
            QMessageBox.information(self,"VISA","No VISA resource is selected.")
            return None
        return resource

    def _set_measurement_instrument(self, resource: str) -> None:
        self._disconnect_measurement_instrument(quiet=True)
        instrument=VisaScpiMeasurementInstrument(resource)
        identity=instrument.identify()
        self.measurement_instrument=instrument
        if hasattr(self,"measurement_id"):
            self.measurement_id.setText("Measurement: "+identity)
        if hasattr(self,"osc_instrument_status"):
            self.osc_instrument_status.setText(identity+" • READ-ONLY MEASUREMENT ROLE")
        self.osc_acquisition=OscillatorAcquisition(instrument,self.osc_queue.put,sample_period_s=0.10)
        self.osc_acquisition.start()
        self._add_event("measurement_instrument_connected",{"resource":resource,"identity":identity})
        if hasattr(self,"cap_table"):
            self._refresh_capabilities()

    def _connect_measurement_instrument(self) -> None:
        resource=self._selected_visa_resource()
        if not resource: return
        try:
            self._set_measurement_instrument(resource)
        except Exception as exc:
            self.measurement_instrument=None
            if hasattr(self,"measurement_id"):
                self.measurement_id.setText("Measurement: connection failed")
            QMessageBox.critical(self,"Measurement instrument connection",str(exc))

    def _connect_osc_measurement_instrument(self) -> None:
        resource=self.osc_visa_combo.currentText().strip()
        if not resource or resource.startswith("No VISA"):
            QMessageBox.information(self,"Oscillator instrument","No VISA resource is selected.")
            return
        try:
            self._set_measurement_instrument(resource)
        except Exception as exc:
            self.measurement_instrument=None
            self.osc_instrument_status.setText("Measurement instrument connection failed")
            QMessageBox.critical(self,"Oscillator instrument connection",str(exc))

    def _disconnect_measurement_instrument(self,quiet:bool=False) -> None:
        if self.osc_acquisition is not None:
            self.osc_acquisition.stop()
            self.osc_acquisition=None
        if self.measurement_instrument is not None:
            try:
                self.measurement_instrument.close()
            except Exception as exc:
                if not quiet:
                    QMessageBox.warning(self,"Measurement instrument",f"Disconnect warning: {exc}")
        self.measurement_instrument=None
        if hasattr(self,"measurement_id"):
            self.measurement_id.setText("Measurement: not connected")
        if hasattr(self,"osc_instrument_status"):
            self.osc_instrument_status.setText("Simulation oscillator" if self.simulation_mode else "No measurement instrument connected")
        if hasattr(self,"cap_table"):
            self._refresh_capabilities()

    def _connect_generator_instrument(self) -> None:
        resource=self._selected_visa_resource()
        if not resource: return
        self._emergency_off()
        if not isinstance(self.instrument,SimulatedInstrument):
            try: self.instrument.close()
            except Exception: pass
        try:
            generator=VisaScpiGenerator(resource)
            identity=generator.identify()
            self.instrument=generator
            self.generator_id.setText("Generator: "+identity+" • OUTPUT OFF")
            self.interference_status.setText(identity+" • OUTPUT OFF")
            self._add_event("generator_connected",{"resource":resource,"identity":identity})
            self._refresh_capabilities()
        except Exception as exc:
            self.instrument=SimulatedInstrument()
            self.generator_id.setText("Generator: connection failed • simulation source restored")
            QMessageBox.critical(self,"Generator connection",str(exc))

    def _disconnect_instrument(self,quiet:bool=False) -> None:
        self._disconnect_measurement_instrument(quiet=quiet)
        try:
            with self.instrument_lock:
                self.instrument.set_output(False)
                self.instrument.close()
        except Exception as exc:
            if not quiet:
                QMessageBox.warning(self,"Generator",f"Disconnect warning: {exc}")
        self.instrument=SimulatedInstrument()
        if hasattr(self,"generator_id"):
            self.generator_id.setText("Generator: Simulation Instrument • OUTPUT OFF")
        if hasattr(self,"output_button"):
            self.output_button.setChecked(False)
            self.output_button.setText("Enable Output")
        if hasattr(self,"interference_status"):
            self.interference_status.setText("Simulation Instrument • OUTPUT OFF")
        if hasattr(self,"cap_table"):
            self._refresh_capabilities()


    def _apply_stimulus_settings(self) -> bool:
        self._sync_safety_limits()
        try:
            self.safety_limits.validate(frequency_hz=self.stim_freq.value(),amplitude_vpp=self.stim_amp.value(),offset_v=self.stim_offset.value())
            if isinstance(self.instrument,SimulatedInstrument):
                self.instrument.frequency_hz=self.stim_freq.value(); self.instrument.amplitude_vpp=self.stim_amp.value()
                self.instrument.offset_v=self.stim_offset.value(); self.instrument.waveform=self.waveform_combo.currentText()
            elif hasattr(self.instrument,"configure_generator"):
                with self.instrument_lock:
                    self.instrument.configure_generator(
                        frequency_hz=self.stim_freq.value(),amplitude_vpp=self.stim_amp.value(),
                        offset_v=self.stim_offset.value(),waveform=self.waveform_combo.currentText(),limits=self.safety_limits
                    )
            requested={"frequency_hz":self.stim_freq.value(),"amplitude_vpp":self.stim_amp.value(),"offset_v":self.stim_offset.value(),"waveform":self.waveform_combo.currentText()}
            try:
                with self.instrument_lock:
                    reported=self.instrument.read_generator_state()
            except Exception:
                reported={"output_enabled":self.instrument.output_enabled(),"frequency_hz":None,"amplitude_vpp":None,"offset_v":None,"waveform":None}
            self._add_event("stimulus_configured",{"requested":requested,"instrument_reported":reported})
            return True
        except Exception as exc:
            QMessageBox.critical(self,"Stimulus settings rejected",str(exc)); return False

    def _toggle_instrument_output(self,checked:bool) -> None:
        if checked:
            if not self._apply_stimulus_settings():
                self.output_button.setChecked(False); return
            answer=QMessageBox.question(self,"Enable external output?","Enable the connected instrument output using the displayed settings?\n\nVerify the DUT connection and configured safety limits before continuing.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if answer!=QMessageBox.StandardButton.Yes:
                self.output_button.setChecked(False); return
        try:
            with self.instrument_lock:
                self.instrument.set_output(bool(checked))
                reported=self.instrument.read_generator_state()
            self._add_event("instrument_output",{"requested_enabled":bool(checked),"instrument_reported":reported})
        except Exception as exc:
            self.output_button.setChecked(False); QMessageBox.critical(self,"Instrument output",str(exc))

    def _emergency_off(self) -> None:
        self._stop_sweep(output_off=False)
        try:
            with self.instrument_lock: self.instrument.set_output(False)
        except Exception: pass
        if hasattr(self,"output_button"): self.output_button.setChecked(False); self.output_button.setText("Enable Output")
        self._add_event("emergency_output_off",{})

    def _refresh_capabilities(self) -> None:
        measurement_caps=getattr(self.measurement_instrument,"capabilities",None)
        generator_caps=getattr(self.instrument,"capabilities",None)
        rows=[
            ("Measurement","Frequency",getattr(measurement_caps,"frequency",False)),
            ("Measurement","Period",getattr(measurement_caps,"period",False)),
            ("Measurement","Duty cycle",getattr(measurement_caps,"duty_cycle",False)),
            ("Measurement","Waveform capture",getattr(measurement_caps,"waveform_capture",False)),
            ("Measurement","Phase noise",getattr(measurement_caps,"phase_noise",False)),
            ("Measurement","High-resolution timestamps",getattr(measurement_caps,"high_resolution_timestamps",False)),
            ("Generator","Controlled output",getattr(generator_caps,"generator_output",False)),
        ]
        self.cap_table.setRowCount(len(rows))
        for r,(role,name,available) in enumerate(rows):
            self.cap_table.setItem(r,0,QTableWidgetItem(role))
            self.cap_table.setItem(r,1,QTableWidgetItem(name))
            self.cap_table.setItem(r,2,QTableWidgetItem("AVAILABLE" if available else "UNAVAILABLE / REQUIRES COMPATIBLE HARDWARE"))

    def _update_sweep_estimate(self) -> None:
        count=self.sweep_steps.value()*self.amp_steps.value()*self.sweep_reps.value()
        per=(self.dwell_ms.value()+self.settle_ms.value())/1000
        self.sweep_estimate.setText(f"Estimated duration: {count*per:.1f}s • {count} points")

    def _start_sweep(self) -> None:
        if self.sweep_active:
            return
        self._sync_safety_limits()
        try:
            steps=make_sweep(
                self.sweep_start.value(),
                self.sweep_stop.value(),
                self.sweep_steps.value(),
                logarithmic=self.sweep_log.isChecked(),
                repetitions=self.sweep_reps.value(),
                randomized=self.sweep_random.isChecked(),
                amplitude_start_vpp=self.amp_start.value(),
                amplitude_stop_vpp=self.amp_stop.value(),
                amplitude_steps=self.amp_steps.value(),
            )
            plan=[(step.frequency_hz,step.amplitude_vpp,step.repetition) for step in steps]
            for freq,amp,_ in plan:
                self.safety_limits.validate(
                    frequency_hz=freq,
                    amplitude_vpp=amp,
                    offset_v=self.stim_offset.value(),
                )
        except Exception as exc:
            QMessageBox.critical(self,"Sweep plan rejected",str(exc))
            return

        if self.sweep_enable_output.isChecked():
            answer=QMessageBox.question(
                self,
                "Authorize sweep output?",
                "This sweep is configured to enable instrument output. Continue?",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer!=QMessageBox.StandardButton.Yes:
                return

        if not self.capture_active:
            self._start_capture()
        self.sweep_plan=plan
        self.sweep_results=[]
        self.sweep_index=0
        self.sweep_phase="idle"
        self.sweep_step_controller_ts=[]
        self.sweep_step_osc_freq=[]
        self.sweep_table.setRowCount(0)
        self.sweep_heatmap.set_points([])
        self.sweep_active=True
        self._add_event("sweep_started",{
            "points":len(plan),
            "settling_ms":self.settle_ms.value(),
            "dwell_ms":self.dwell_ms.value(),
            "randomized":self.sweep_random.isChecked(),
        })
        self._sweep_apply_next()

    def _sweep_apply_next(self) -> None:
        if not self.sweep_active or self.sweep_index>=len(self.sweep_plan):
            self._stop_sweep()
            return

        freq,amp,rep=self.sweep_plan[self.sweep_index]
        self.stim_freq.setValue(freq)
        self.stim_amp.setValue(amp)

        # Configure the next safe point before enabling output. This avoids a
        # transient where the source is on with settings from the prior point.
        if not self._apply_stimulus_settings():
            self._stop_sweep()
            return

        if self.sweep_enable_output.isChecked():
            try:
                with self.instrument_lock:
                    self.instrument.set_output(True)
            except Exception as exc:
                QMessageBox.critical(self,"Sweep output",str(exc))
                self._stop_sweep()
                return

        self._add_event("sweep_step",{
            "index":self.sweep_index+1,
            "frequency_hz":freq,
            "amplitude_vpp":amp,
            "repetition":rep,
        })
        self.sweep_phase="settling"
        if self.settle_ms.value()>0:
            self.sweep_timer.start(self.settle_ms.value())
        else:
            self._begin_sweep_dwell()

    def _sweep_timer_tick(self) -> None:
        if not self.sweep_active:
            return
        if self.sweep_phase=="settling":
            self._begin_sweep_dwell()
        elif self.sweep_phase=="dwell":
            self._sweep_record_and_advance()

    def _begin_sweep_dwell(self) -> None:
        self.sweep_step_controller_ts=[]
        self.sweep_step_osc_freq=[]
        self.sweep_phase="dwell"
        self.sweep_timer.start(self.dwell_ms.value())

    def _refresh_sweep_heatmap(self) -> None:
        if not hasattr(self,"sweep_heatmap"):
            return
        metric=self.sweep_heatmap_metric.currentData() if hasattr(self,"sweep_heatmap_metric") else "gamepad"
        if metric=="clock":
            self.sweep_heatmap.title="Frequency / amplitude response • oscillator clock error (ppm)"
            points=[(f,a,ppm) for f,a,_,ppm in self.sweep_results if ppm is not None]
        else:
            self.sweep_heatmap.title="Frequency / amplitude response • gamepad RMS timing deviation (ms)"
            points=[(f,a,gp) for f,a,gp,_ in self.sweep_results if gp is not None]
        self.sweep_heatmap.set_points(points)

    def _sweep_record_and_advance(self) -> None:
        if not self.sweep_active or self.sweep_index>=len(self.sweep_plan):
            return

        freq,amp,rep=self.sweep_plan[self.sweep_index]
        step_timing=timing_metrics(
            self.sweep_step_controller_ts,
            expected_interval_ms=1000.0/max(self.expected_rate.value(),1.0),
            late_factor=self.late_factor.value(),
        )
        step_osc=oscillator_metrics(
            self.sweep_step_osc_freq,
            self.nominal_freq.value(),
        )
        gp=step_timing.rms_deviation_ms if step_timing.sample_count>=2 else None
        ppm=step_osc.frequency_error_ppm if step_osc.sample_count else None
        self.sweep_results.append((freq,amp,gp,ppm))

        row=self.sweep_table.rowCount()
        self.sweep_table.insertRow(row)
        gp_text=f"{gp:.6g}" if gp is not None else "Unavailable"
        ppm_text=f"{ppm:+.6g}" if ppm is not None else "Unavailable"
        status="Captured" if gp is not None else "Insufficient controller samples"
        vals=[row+1,f"{freq:.8g}",f"{amp:.6g}",gp_text,ppm_text,status]
        for col,val in enumerate(vals):
            self.sweep_table.setItem(row,col,QTableWidgetItem(str(val)))

        self._refresh_sweep_heatmap()
        try:
            with self.instrument_lock:
                reported=self.instrument.read_generator_state()
        except Exception:
            reported={"output_enabled":self.instrument.output_enabled(),"frequency_hz":None,"amplitude_vpp":None,"offset_v":None,"waveform":None}
        self._add_event("sweep_result",{
            "index":self.sweep_index+1,
            "requested":{"frequency_hz":freq,"amplitude_vpp":amp,"offset_v":self.stim_offset.value(),"waveform":self.waveform_combo.currentText()},
            "instrument_reported":reported,
            "repetition":rep,
            "gamepad_rms_ms":gp,
            "clock_ppm":ppm,
            "controller_samples":step_timing.sample_count,
            "oscillator_samples":step_osc.sample_count,
        })
        self.sweep_index+=1
        self.sweep_phase="idle"
        self._sweep_apply_next()

    def _stop_sweep(self,output_off:bool=True) -> None:
        was=self.sweep_active
        self.sweep_active=False
        self.sweep_phase="idle"
        self.sweep_timer.stop()
        if output_off:
            try:
                with self.instrument_lock:
                    self.instrument.set_output(False)
            except Exception:
                pass
        if was:
            self._add_event("sweep_stopped",{"captured_points":len(self.sweep_results)})

    def _add_event(self,event_type:str,payload:dict) -> None:
        ts=time.perf_counter_ns(); self.events.append((ts,event_type,dict(payload)))
        if self.capture_active and self.session_id: self.db.add_event(self.session_id,ts,event_type,payload)
        self._update_timeline()

    def _update_timeline(self) -> None:
        if not hasattr(self,"timeline_table"): return
        rows=self.events[-300:]; self.timeline_table.setRowCount(len(rows))
        for r,(ts,event,payload) in enumerate(rows):
            self.timeline_table.setItem(r,0,QTableWidgetItem(str(ts))); self.timeline_table.setItem(r,1,QTableWidgetItem(event)); self.timeline_table.setItem(r,2,QTableWidgetItem(json.dumps(payload,separators=(",",":"))))

    def _refresh_experiments(self) -> None:
        if not hasattr(self,"experiments_table"): return
        rows=self.db.list_sessions(limit=200); self.experiments_table.setRowCount(len(rows))
        for r,row in enumerate(rows):
            vals=[row["created_utc"],row["name"],row["mode"],row["controller_samples"],row["oscillator_samples"],row["events"],row["id"]]
            for c,val in enumerate(vals): self.experiments_table.setItem(r,c,QTableWidgetItem(str(val)))

    def _refresh_compare(self) -> None:
        if not hasattr(self,"compare_table"): return
        if not self.reference_baseline:
            self.compare_state.setText("No reference baseline selected. Run a baseline and set it as reference."); self.compare_table.setRowCount(0); return
        rt=self.reference_baseline["timing"]; ro=self.reference_baseline["oscillator"]; ct=asdict(self.current_timing); co=asdict(self.current_osc)
        metrics=[
            ("Effective rate Hz",rt["effective_rate_hz"],ct["effective_rate_hz"]),
            ("Mean interval ms",rt["mean_interval_ms"],ct["mean_interval_ms"]),
            ("RMS timing deviation ms",rt["rms_deviation_ms"],ct["rms_deviation_ms"]),
            ("P99 ms",rt["p99_ms"],ct["p99_ms"]),("P99.9 ms",rt["p999_ms"],ct["p999_ms"]),
            ("Clock frequency Hz",ro["mean_frequency_hz"],co["mean_frequency_hz"]),
            ("Clock error ppm",ro["frequency_error_ppm"],co["frequency_error_ppm"]),
            ("Clock RMS period jitter s",ro["rms_period_jitter_s"],co["rms_period_jitter_s"]),
            ("Cycle-to-cycle RMS s",ro["cycle_to_cycle_rms_s"],co["cycle_to_cycle_rms_s"])
        ]
        self.compare_state.setText("Reference baseline loaded. Differences are descriptive measurements, not causal conclusions.")
        self.compare_table.setRowCount(len(metrics))
        for r,(name,ref,cur) in enumerate(metrics):
            pct="Unavailable" if math.isclose(float(ref),0.0,abs_tol=1e-30) else f"{((cur-ref)/abs(ref))*100:+.3f}%"
            for c,val in enumerate([name,f"{ref:.9g}",f"{cur:.9g}",f"{cur-ref:+.9g}",pct]):
                self.compare_table.setItem(r,c,QTableWidgetItem(str(val)))

    def _ensure_session(self) -> bool:
        if not self.session_id:
            QMessageBox.information(self,"Reports","Start a capture first so there is a session to export."); return False
        self.db.flush(); return True

    def _export_json(self) -> None:
        if not self._ensure_session(): return
        path,_=QFileDialog.getSaveFileName(self,"Export JSON",str(self.data_root/"experiment.json"),"JSON (*.json)")
        if path: self.db.export_json(self.session_id,path)

    def _export_csv(self) -> None:
        if not self._ensure_session(): return
        path,_=QFileDialog.getSaveFileName(self,"Export Controller CSV",str(self.data_root/"controller_samples.csv"),"CSV (*.csv)")
        if path: self.db.export_controller_csv(self.session_id,path)

    def _export_html_report(self) -> None:
        if not self._ensure_session(): return
        path,_=QFileDialog.getSaveFileName(self,"Engineering Report",str(self.data_root/"GamepadSignalLab_Report.html"),"HTML (*.html)")
        if not path: return
        timestamps=list(self.controller_ts)[-5000:]
        expected_ms=1000.0/max(self.expected_rate.value(),1.0)
        intervals=[(b-a)/1e6 for a,b in zip(timestamps,timestamps[1:]) if b>a]
        deviations=[value-expected_ms for value in intervals]
        freqs=list(self.osc_freq)[-3000:]
        nominal=self.nominal_freq.value()
        ppm=[(value-nominal)/nominal*1e6 for value in freqs] if nominal>0 else []
        timeline=self.db.list_events(self.session_id,limit=250)
        write_html_report(
            path,title="Gamepad Signal Lab Engineering Report",
            controller_metrics=asdict(self.current_timing),
            oscillator_metrics=asdict(self.current_osc),
            metadata={
                "session_id":self.session_id,
                "mode":"simulation" if self.simulation_mode else "hardware",
                "correlation":self.current_corr,
                "app_version":__version__,
                "nominal_frequency_hz":self.nominal_freq.value(),
                "expected_polling_rate_hz":self.expected_rate.value(),
                "baseline_reference":self.reference_baseline,
                "stimulus":{
                    "waveform":self.waveform_combo.currentText(),
                    "frequency_hz":self.stim_freq.value(),
                    "amplitude_vpp":self.stim_amp.value(),
                    "offset_v":self.stim_offset.value(),
                    "output_enabled":bool(self.instrument.output_enabled()),
                },
                "sweep_points":len(self.sweep_results),
            },
            plots={
                "Report interval vs sample (ms)":intervals[-1200:],
                "Gamepad timing deviation (ms)":deviations[-1200:],
                "Oscillator frequency (Hz)":freqs[-1200:],
                "Oscillator error (ppm)":ppm[-1200:],
            },
            sweep_points=self.sweep_results,
            timeline=timeline,
            limitations=[
                "Host-side gamepad timestamps include USB/OS scheduling unless dedicated analyzer hardware supplies bus-level timestamps.",
                "Oscillator precision cannot exceed the connected measurement instrument and sampling method.",
                "Software-derived frequency/period jitter is calculated and is not a substitute for a dedicated phase-noise analyzer.",
                "Correlation between signals does not by itself demonstrate causation."
            ]
        )

    def _apply_saved_settings(self) -> None:
        self.baseline_seconds.setValue(int(self.settings.value("baseline_seconds",60)))
        self.expected_rate.setValue(float(self.settings.value("expected_rate",1000)))
        self.late_factor.setValue(float(self.settings.value("late_factor",1.5)))
        self.nominal_freq.setValue(float(self.settings.value("nominal_freq",12_000_000)))
        self.graph_refresh.setValue(int(self.settings.value("graph_refresh",100)))
        self.max_freq.setValue(float(self.settings.value("max_freq",20_000_000)))
        self.max_amp.setValue(float(self.settings.value("max_amp",1.0)))
        self.max_offset.setValue(float(self.settings.value("max_offset",0.5)))
        self._sync_safety_limits()

    def _save_settings(self) -> None:
        self._sync_safety_limits()
        values={
            "theme":self.theme_combo.currentText(),"baseline_seconds":self.baseline_seconds.value(),
            "expected_rate":self.expected_rate.value(),"late_factor":self.late_factor.value(),"nominal_freq":self.nominal_freq.value(),
            "graph_refresh":self.graph_refresh.value(),"max_freq":self.max_freq.value(),
            "max_amp":self.max_amp.value(),"max_offset":self.max_offset.value()
        }
        for key,val in values.items(): self.settings.setValue(key,val)
        QMessageBox.information(self,"Settings","Settings saved.")

    def _sync_safety_limits(self) -> None:
        if hasattr(self,"max_freq"):
            self.safety_limits.max_frequency_hz=self.max_freq.value()
            self.safety_limits.max_amplitude_vpp=self.max_amp.value()
            self.safety_limits.max_abs_offset_v=self.max_offset.value()

    def _change_theme(self,name:str) -> None:
        self.theme_name=name; self.setStyleSheet(LIGHT if name=="Light" else DARK); self.settings.setValue("theme",name)

    def _reset_graphs(self) -> None:
        for ch in [
            self.dashboard_timing_chart,self.dashboard_osc_chart,self.live_interval_chart,self.live_jitter_chart,
            self.live_hist_chart,self.live_latency_chart,self.live_analog_chart,self.live_osc_chart,self.live_osc_jitter_chart,
            self.osc_stability_chart,self.osc_period_chart,self.corr_stimulus_chart,self.corr_osc_chart,self.corr_gamepad_chart
        ]: ch.reset_view()

    def _first_run(self) -> None:
        dlg=WelcomeDialog(self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            self.settings.setValue("welcomed",True)
            if dlg.hardware.isChecked(): self._toggle_mode()

    def closeEvent(self,event:QCloseEvent) -> None:
        self._emergency_off()
        if self.controller_acquisition: self.controller_acquisition.stop()
        self._disconnect_instrument(quiet=True)
        self.db.close(); event.accept()


def main() -> int:
    app=QApplication.instance() or QApplication([])
    app.setApplicationName("Gamepad Signal Lab")
    app.setOrganizationName("SensoredRooster")
    app.setStyle("Fusion")
    window=MainWindow()
    window.show()
    return app.exec()
