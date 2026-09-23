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
import webbrowser

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from . import __version__
from .analysis import oscillator_metrics, pearson_correlation, timing_metrics
from .controller import ControllerAcquisition, ControllerMeasurement
from .instruments import SafetyLimits, SimulatedInstrument, VisaScpiGenerator, VisaScpiMeasurementInstrument, list_visa_resources
from .metric_catalog import CHART_HELP, METRIC_HELP
from .oscillator import OscillatorAcquisition, OscillatorMeasurement
from .reporting import write_html_report
from .simulation import GamepadSimulator, OscillatorSimulator, stimulus_response
from .storage import LabDatabase
from .sweep import make_sweep
from .theme import DARK, LIGHT
from .widgets import HeatMapWidget, LineChart, MetricCard, StickView
from support import (
    SESSION_ID as SUPPORT_SESSION_ID,
    create_support_bundle,
    health_snapshot,
    log_event as support_log_event,
    open_logs_folder,
    open_repository,
    report_issue,
    start_heartbeat,
    upload_support_bundle,
)

NAV = [
    "Dashboard", "Live Capture", "Controller Lab", "Oscillator Lab",
    "Interference Lab", "Sweep Lab", "Correlation", "Experiments",
    "Compare", "Reports", "Instruments", "Support", "Settings",
]

TESTER_SHARE_URL = "https://rcm-tool-share.sensoredrooster-com.workers.dev"



def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("SectionCard")
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
    layout.setContentsMargins(22, 18, 22, 22)
    layout.setSpacing(14)
    desc = QLabel(subtitle)
    desc.setObjectName("Muted")
    desc.setWordWrap(True)
    desc.setMinimumHeight(34)
    layout.addWidget(desc)
    return outer, layout


class SupportUploadWorker(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.completed.emit(upload_support_bundle())
        except Exception as exc:
            self.failed.emit(str(exc))


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
        self.controller_metadata: dict = {}
        self.osc_ts = deque(maxlen=12000)
        self.osc_freq = deque(maxlen=12000)
        self.osc_duty = deque(maxlen=12000)
        self.events: list[tuple[int, str, dict]] = []

        self.baseline_active = False
        self.baseline_deadline = 0.0
        self.baseline_controller_ts: list[int] = []
        self.baseline_osc_freq: list[float] = []
        self.baseline_osc_duty: list[float] = []
        self.last_baseline: dict | None = None
        self.reference_baseline: dict | None = None

        self.sweep_active = False
        self.sweep_plan: list[tuple[float, float, int]] = []
        self.sweep_index = 0
        self.sweep_results: list[tuple[float, float, float | None, float | None]] = []
        self.sweep_metric_rows: list[dict] = []
        self.sweep_step_controller_ts: list[int] = []
        self.sweep_step_controller_samples: list[dict] = []
        self.sweep_step_osc_freq: list[float] = []
        self.sweep_step_osc_duty: list[float] = []
        self.sweep_phase = "idle"
        self.sweep_reference_rate_hz = 0.0
        self.sweep_timer = QTimer(self)
        self.sweep_timer.setSingleShot(True)
        self.sweep_timer.timeout.connect(self._sweep_timer_tick)

        self.current_timing = timing_metrics([])
        self.current_osc = oscillator_metrics([], 12_000_000.0)
        self.current_corr: float | None = None
        self.corr_time_axis: list[int] = []
        self.visualization_paused = False
        self.host_timer_resolution_ns = time.get_clock_info("perf_counter").resolution * 1_000_000_000.0
        self.support_upload_worker: SupportUploadWorker | None = None

        start_heartbeat()
        support_log_event("gamepad_signal_lab_start", version=__version__)
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
        sidebar.setFixedWidth(238)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 14)

        brand = QLabel("GAMEPAD\nSIGNAL LAB")
        brand.setObjectName("Brand")
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
            self._instruments_page, self._support_page, self._settings_page,
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
            self._refresh_compare_sources()
            self._refresh_compare()
        elif NAV[index] == "Instruments":
            self._refresh_capabilities()

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        return scroll

    def _dashboard_page(self) -> QWidget:
        w, layout = page(
            "Dashboard",
            "Live measured state first; calculated interpretation second. Hover any metric or graph for its definition and measurement caveat.",
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        self.cards = {}
        specs = [
            ("rate","Gamepad rate","MEASURED"), ("interval","Report interval","MEASURED"),
            ("jitter","Gamepad jitter","CALCULATED"), ("osc","Oscillator","MEASURED"),
            ("ppm","Clock error","CALCULATED"), ("clock_jitter","Clock jitter","CALCULATED"),
            ("late","Late reports","CALCULATED"), ("stimulus","Test stimulus","STATE"),
        ]
        for i, (key, title, source) in enumerate(specs):
            c = MetricCard(title, help_text=METRIC_HELP[key], source=source)
            self.cards[key] = c
            grid.addWidget(c, i // 4, i % 4)
        layout.addLayout(grid)

        baseline, bl = card("BASELINE / REFERENCE")
        state_row = QHBoxLayout()
        self.baseline_state = QLabel("No baseline captured")
        self.baseline_state.setObjectName("Muted")
        self.baseline_state.setWordWrap(True)
        self.baseline_progress = QProgressBar()
        self.baseline_progress.setRange(0,1000)
        state_row.addWidget(self.baseline_state,2)
        state_row.addWidget(self.baseline_progress,1)
        bl.addLayout(state_row)
        actions = QHBoxLayout()
        hint = QLabel("Baseline uses the same timing-reference rule selected in Settings.")
        hint.setObjectName("SectionHint")
        hint.setWordWrap(True)
        save_baseline = QPushButton("Save Baseline"); save_baseline.clicked.connect(self._save_baseline)
        set_ref = QPushButton("Set Reference"); set_ref.clicked.connect(self._set_reference_baseline)
        compare = QPushButton("Compare"); compare.clicked.connect(lambda: self._navigate(NAV.index("Compare")))
        actions.addWidget(hint,1); actions.addWidget(save_baseline); actions.addWidget(set_ref); actions.addWidget(compare)
        bl.addLayout(actions)
        layout.addWidget(baseline)

        charts = QGridLayout()
        charts.setHorizontalSpacing(14)
        self.dashboard_timing_chart = LineChart(
            "Controller report interval",
            help_text=CHART_HELP["report_interval"],
            x_label="Elapsed controller capture time (s)",
        )
        self.dashboard_osc_chart = LineChart(
            "Oscillator frequency error",
            help_text=CHART_HELP["osc_ppm"],
            x_label="Elapsed oscillator capture time (s)",
        )
        charts.addWidget(self.dashboard_timing_chart,0,0)
        charts.addWidget(self.dashboard_osc_chart,0,1)
        charts.setColumnStretch(0,1); charts.setColumnStretch(1,1)
        layout.addLayout(charts)

        q, ql = card("MEASUREMENT QUALITY / PROVENANCE")
        self.quality_label = QLabel("Waiting for samples")
        self.quality_label.setWordWrap(True)
        self.quality_label.setToolTip(
            "Timing source, host timer resolution, sample count, capture duration, duplicate/raw-report information, and instrument limitations."
        )
        ql.addWidget(self.quality_label)
        layout.addWidget(q)
        layout.addStretch(1)
        return self._scroll(w)

    def _live_page(self) -> QWidget:
        w, layout = page(
            "Live Capture",
            "Acquisition remains raw and lossless. Pause and smoothing affect only the display layer.",
        )
        controls, controls_layout = card("DISPLAY CONTROLS")
        self.pause_visualization = QCheckBox("Pause visualization")
        self.pause_visualization.setToolTip("Freezes graph repainting only. Acquisition and raw storage continue.")
        self.pause_visualization.toggled.connect(lambda checked: setattr(self, "visualization_paused", bool(checked)))
        self.smoothing_window = QSpinBox()
        self.smoothing_window.setRange(1, 51)
        self.smoothing_window.setValue(1)
        self.smoothing_window.setPrefix("Display smoothing ")
        self.smoothing_window.setSuffix(" samples")
        self.smoothing_window.setToolTip("Moving average applied to displayed traces only. Stored raw data is unchanged.")
        reset = QPushButton("Reset Views"); reset.clicked.connect(self._reset_graphs)
        export = QPushButton("Export Graph"); export.clicked.connect(lambda: self.live_interval_chart.export_png(self))
        fullscreen = QPushButton("Fullscreen"); fullscreen.clicked.connect(lambda: self._show_chart_fullscreen(self.live_interval_chart))
        raw = QPushButton("Inspect Raw Data"); raw.clicked.connect(self._show_raw_data)

        primary_controls = QHBoxLayout()
        primary_controls.addWidget(self.pause_visualization)
        primary_controls.addWidget(self.smoothing_window,1)
        primary_controls.addWidget(reset)
        primary_controls.addStretch(1)
        controls_layout.addLayout(primary_controls)

        secondary_controls = QHBoxLayout()
        secondary_controls.addWidget(export)
        secondary_controls.addWidget(fullscreen)
        secondary_controls.addWidget(raw)
        secondary_controls.addStretch(1)
        controls_layout.addLayout(secondary_controls)
        layout.addWidget(controls)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14); grid.setVerticalSpacing(14)
        self.live_interval_chart = LineChart("Controller report interval", help_text=CHART_HELP["report_interval"], x_label="Elapsed time (s)")
        self.live_jitter_chart = LineChart("Controller timing deviation", help_text=CHART_HELP["gamepad_deviation"], x_label="Elapsed time (s)")
        self.live_hist_chart = LineChart("Report-interval distribution", help_text=CHART_HELP["interval_histogram"], x_label="Report interval (ms)")
        self.live_latency_chart = LineChart("Input latency", help_text=CHART_HELP["latency"], x_label="Latency (ms)")
        self.live_analog_chart = LineChart("Analog stick stability", help_text=CHART_HELP["analog_stability"], x_label="Elapsed time (s)")
        self.live_osc_chart = LineChart("Oscillator frequency", help_text=CHART_HELP["osc_frequency"], x_label="Elapsed time (s)")
        self.live_osc_jitter_chart = LineChart("Oscillator frequency error", help_text=CHART_HELP["osc_ppm"], x_label="Elapsed time (s)")
        charts = [
            self.live_interval_chart, self.live_jitter_chart, self.live_hist_chart,
            self.live_latency_chart, self.live_analog_chart, self.live_osc_chart, self.live_osc_jitter_chart
        ]
        for i, ch in enumerate(charts):
            grid.addWidget(ch, i//2, i%2)
        grid.setColumnStretch(0,1); grid.setColumnStretch(1,1)
        layout.addLayout(grid)
        return self._scroll(w)

    def _controller_page(self) -> QWidget:
        w, layout = page("Controller Lab", "Controller discovery, live axes/triggers, timing source, and stationary analog noise.")
        meta, ml = card("CONTROLLER")
        self.controller_meta = QLabel("Simulation controller")
        self.controller_meta.setWordWrap(True)
        ml.addWidget(self.controller_meta,0,Qt.AlignmentFlag.AlignTop)
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
        self.axis_noise.setWordWrap(True)
        tl.addWidget(self.axis_noise)
        self.button_capability = QLabel("Buttons / D-pad: waiting for an input sample")
        self.button_capability.setObjectName("Muted")
        self.button_capability.setWordWrap(True)
        tl.addWidget(self.button_capability)
        self.controller_capability = QLabel("Firmware / battery / USB path: shown only when the active backend can report them.")
        self.controller_capability.setObjectName("Muted")
        self.controller_capability.setWordWrap(True)
        tl.addWidget(self.controller_capability)
        row.addWidget(trg,2)
        layout.addLayout(row)
        layout.addStretch(1)
        return self._scroll(w)

    def _oscillator_page(self) -> QWidget:
        w, layout = page(
            "Oscillator Lab",
            "Measured frequency samples and explicitly labeled calculations. Unsupported hardware values remain unavailable.",
        )
        ref, rl = card("REFERENCE / MEASUREMENT SOURCE")
        form = QFormLayout()
        self.nominal_freq = QDoubleSpinBox()
        self.nominal_freq.setRange(1, 10_000_000_000)
        self.nominal_freq.setDecimals(3)
        self.nominal_freq.setValue(12_000_000)
        self.nominal_freq.setSuffix(" Hz")
        self.nominal_freq.setToolTip("Reference used for frequency-error and ppm calculations; it is not a measured value.")
        form.addRow("Nominal frequency", self.nominal_freq)
        rl.addLayout(form)
        instrument_row = QHBoxLayout()
        self.osc_visa_combo = QComboBox()
        osc_refresh = QPushButton("Refresh VISA"); osc_refresh.clicked.connect(self._refresh_osc_visa)
        osc_connect = QPushButton("Connect Measurement Instrument"); osc_connect.clicked.connect(self._connect_osc_measurement_instrument)
        osc_disconnect = QPushButton("Disconnect"); osc_disconnect.clicked.connect(self._disconnect_measurement_instrument)
        instrument_row.addWidget(self.osc_visa_combo,1); instrument_row.addWidget(osc_refresh); instrument_row.addWidget(osc_connect); instrument_row.addWidget(osc_disconnect)
        rl.addLayout(instrument_row)
        self.osc_instrument_status = QLabel("Simulation oscillator")
        self.osc_instrument_status.setObjectName("Muted")
        self.osc_instrument_status.setWordWrap(True)
        rl.addWidget(self.osc_instrument_status)
        layout.addWidget(ref)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12); grid.setVerticalSpacing(12)
        for column in range(3): grid.setColumnStretch(column,1)
        self.osc_labels = {}
        specs = [
            ("mean","Measured frequency","osc_mean","MEASURED"),
            ("stdev","Frequency stdev","osc_stdev","CALCULATED"),
            ("error_hz","Frequency error","osc_error_hz","CALCULATED"),
            ("error_ppm","Frequency error ppm","osc_error_ppm","CALCULATED"),
            ("drift","Window drift","osc_drift","CALCULATED"),
            ("outliers","Frequency outliers","osc_outliers","CALCULATED"),
            ("period","Mean derived period","osc_period","CALCULATED"),
            ("rms","RMS derived period jitter","osc_rms","CALCULATED"),
            ("p2p","P2P derived period jitter","osc_p2p","CALCULATED"),
            ("ctc","Successive-period RMS","osc_ctc","CALCULATED"),
            ("duty","Duty cycle","duty","MEASURED"),
            ("allan","Allan deviation","allan","CALCULATED"),
        ]
        for i,(key,title,help_key,source) in enumerate(specs):
            c=MetricCard(title,help_text=METRIC_HELP[help_key],source=source)
            self.osc_labels[key]=c
            grid.addWidget(c,i//3,i%3)
        layout.addLayout(grid)

        self.osc_stability_chart = LineChart("Oscillator frequency",help_text=CHART_HELP["osc_frequency"],x_label="Elapsed time (s)")
        self.osc_period_chart = LineChart(
            "Derived period",
            help_text="Period = 1/f for each valid frequency sample. This trace is calculated unless the connected instrument directly supplies period samples.",
            x_label="Elapsed time (s)",
        )
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
        self.sweep_heatmap_metric.addItem("Oscillator RMS period jitter (ps)","osc_jitter")
        self.sweep_heatmap_metric.addItem("Polling-rate deviation (Hz)","polling")
        self.sweep_heatmap_metric.addItem("Clock frequency deviation (ppm)","clock")
        self.sweep_heatmap_metric.addItem("Late reports","late")
        self.sweep_heatmap_metric.addItem("Analog stationary noise RMS","analog")
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
        marker_row=QHBoxLayout()
        self.marker_text=QLineEdit(); self.marker_text.setPlaceholderText("Marker label")
        marker_button=QPushButton("Add User Marker"); marker_button.clicked.connect(self._add_user_marker)
        marker_row.addWidget(self.marker_text,1); marker_row.addWidget(marker_button)
        layout.addLayout(marker_row)
        self.corr_card=MetricCard(
            "Aligned correlation coefficient","—","Gamepad interval deviation vs nearest oscillator ppm sample",
            help_text=METRIC_HELP["correlation"],source="CALCULATED"
        )
        layout.addWidget(self.corr_card)
        self.corr_stimulus_chart=LineChart(
            "Stimulus frequency",
            help_text="Requested controlled-stimulus frequency aligned to the common experiment timeline.",
            x_label="Elapsed correlated time (s)",
        )
        self.corr_osc_chart=LineChart(
            "Oscillator frequency error",
            help_text=CHART_HELP["osc_ppm"],
            x_label="Elapsed correlated time (s)",
        )
        self.corr_gamepad_chart=LineChart(
            "Controller timing deviation",
            help_text=CHART_HELP["gamepad_deviation"],
            x_label="Elapsed correlated time (s)",
        )
        self.corr_charts=[self.corr_stimulus_chart,self.corr_osc_chart,self.corr_gamepad_chart]
        for chart in self.corr_charts:
            chart.cursorRatioChanged.connect(lambda ratio, source=chart: self._sync_correlation_cursor(source,ratio))
            chart.cursorCleared.connect(self._clear_correlation_cursor)
        layout.addWidget(self.corr_stimulus_chart); layout.addWidget(self.corr_osc_chart); layout.addWidget(self.corr_gamepad_chart)
        self.corr_event_detail=QLabel("Select a timeline event to position the synchronized cursor.")
        self.corr_event_detail.setObjectName("Muted"); self.corr_event_detail.setWordWrap(True)
        layout.addWidget(self.corr_event_detail)
        self.corr_events_table=QTableWidget(0,3)
        self.corr_events_table.setHorizontalHeaderLabels(["Monotonic time","Event","Details"])
        self.corr_events_table.cellClicked.connect(self._select_correlation_event)
        layout.addWidget(self.corr_events_table)
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
        w, layout = page("Compare", "Compare either the active baseline against live data or any two saved experiment sessions.")
        controls=QHBoxLayout()
        self.compare_a=QComboBox(); self.compare_b=QComboBox()
        saved=QPushButton("Compare Saved Sessions"); saved.clicked.connect(self._compare_saved_sessions)
        live=QPushButton("Reference vs Live"); live.clicked.connect(self._refresh_compare)
        controls.addWidget(QLabel("Reference")); controls.addWidget(self.compare_a,1)
        controls.addWidget(QLabel("Test")); controls.addWidget(self.compare_b,1)
        controls.addWidget(saved); controls.addWidget(live)
        layout.addLayout(controls)
        self.compare_state=QLabel("No comparison selected."); self.compare_state.setObjectName("Muted"); layout.addWidget(self.compare_state)
        self.compare_table=QTableWidget(0,5); self.compare_table.setHorizontalHeaderLabels(["Metric","Reference","Test","Difference","% Change"]); layout.addWidget(self.compare_table)
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

    def _support_page(self) -> QWidget:
        w, layout = page(
            "Support & Diagnostics",
            "The existing local-first tester support pipeline is preserved here. Nothing is uploaded until the tester explicitly confirms it.",
        )
        status_card, status_layout = card("PRIVATE SUPPORT SESSION")
        session = QLabel(f"Session ID  •  {SUPPORT_SESSION_ID}")
        session.setObjectName("Good")
        status_layout.addWidget(session)
        privacy = QLabel(
            "Runtime telemetry stays under %LOCALAPPDATA%\\RCMTool\\logs until you explicitly send a redacted support bundle."
        )
        privacy.setObjectName("Muted")
        privacy.setWordWrap(True)
        status_layout.addWidget(privacy)
        layout.addWidget(status_card)

        health_card, health_layout = card("HEALTH SNAPSHOT")
        self.support_health = QPlainTextEdit()
        self.support_health.setReadOnly(True)
        self.support_health.setMinimumHeight(230)
        health_layout.addWidget(self.support_health)
        refresh = QPushButton("Refresh Snapshot")
        refresh.clicked.connect(self._refresh_support_health)
        health_layout.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(health_card)

        actions_card, actions_layout = card("TESTER → DEVELOPER")
        primary = QHBoxLayout()
        bundle = QPushButton("Create Redacted Bundle")
        bundle.clicked.connect(self._create_support_bundle)
        self.send_support_button = QPushButton("Send Diagnostics to Developer")
        self.send_support_button.setObjectName("Primary")
        self.send_support_button.clicked.connect(self._send_support_bundle)
        primary.addWidget(bundle)
        primary.addWidget(self.send_support_button)
        primary.addStretch(1)
        actions_layout.addLayout(primary)

        secondary = QHBoxLayout()
        logs = QPushButton("Open Logs Folder"); logs.clicked.connect(open_logs_folder)
        issue = QPushButton("Report GitHub Issue"); issue.clicked.connect(report_issue)
        repo_button = QPushButton("Open Repository"); repo_button.clicked.connect(open_repository)
        tester_share = QPushButton("Tester Share"); tester_share.clicked.connect(lambda: webbrowser.open(TESTER_SHARE_URL))
        for button in (logs, issue, repo_button, tester_share):
            secondary.addWidget(button)
        secondary.addStretch(1)
        actions_layout.addLayout(secondary)

        self.support_status = QLabel("No upload has been requested.")
        self.support_status.setObjectName("Muted")
        self.support_status.setWordWrap(True)
        actions_layout.addWidget(self.support_status)
        layout.addWidget(actions_card)
        layout.addStretch(1)
        self._refresh_support_health()
        return self._scroll(w)

    def _refresh_support_health(self) -> None:
        if hasattr(self, "support_health"):
            self.support_health.setPlainText(json.dumps(health_snapshot(), indent=2))

    def _create_support_bundle(self) -> None:
        try:
            path = create_support_bundle()
            self.support_status.setText(f"Redacted support bundle created locally: {path}")
            QMessageBox.information(self, "Support bundle created", f"Created locally:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Support bundle failed", str(exc))

    def _send_support_bundle(self) -> None:
        answer = QMessageBox.question(
            self,
            "Send diagnostics to developer?",
            "Create and upload a redacted diagnostics bundle to the RCM Tool developer?\n\n"
            "The bundle contains support logs and a health manifest. It intentionally excludes certification reports, raw controller samples, and raw HID captures.\n\n"
            "Nothing is uploaded unless you choose Yes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.support_upload_worker is not None and self.support_upload_worker.isRunning():
            return
        self.send_support_button.setEnabled(False)
        self.support_status.setText("Uploading redacted diagnostics…")
        self.support_upload_worker = SupportUploadWorker(self)
        self.support_upload_worker.completed.connect(self._support_upload_complete)
        self.support_upload_worker.failed.connect(self._support_upload_failed)
        self.support_upload_worker.start()

    def _support_upload_complete(self, result: dict) -> None:
        self.send_support_button.setEnabled(True)
        self.support_status.setText(
            f"Diagnostics sent successfully • HTTP {result.get('status', '?')} • session {SUPPORT_SESSION_ID}"
        )
        self._add_event("support_diagnostics_sent", {"status": result.get("status")})
        QMessageBox.information(self, "Diagnostics sent", f"Upload completed successfully.\nHTTP status: {result.get('status')}")

    def _support_upload_failed(self, message: str) -> None:
        self.send_support_button.setEnabled(True)
        self.support_status.setText("Upload failed. A local support bundle can still be created and shared manually.")
        self._add_event("support_diagnostics_failed", {"error": message})
        QMessageBox.critical(self, "Diagnostics upload failed", message)

    def _settings_page(self) -> QWidget:
        w, layout = page("Settings", "Measurement, safety, data, and performance controls.")
        g, gl=card("GENERAL / MEASUREMENT")
        form=QFormLayout()
        self.theme_combo=QComboBox(); self.theme_combo.addItems(["Dark","Light"]); self.theme_combo.setCurrentText(self.theme_name); self.theme_combo.currentTextChanged.connect(self._change_theme)
        self.baseline_seconds=QSpinBox(); self.baseline_seconds.setRange(5,3600); self.baseline_seconds.setValue(60); self.baseline_seconds.setSuffix(" s")
        self.timing_reference_mode=QComboBox()
        self.timing_reference_mode.addItem("Measured median interval (recommended)","median")
        self.timing_reference_mode.addItem("Configured reference rate","configured")
        self.timing_reference_mode.setToolTip(
            "Measured median derives the timing reference from the capture itself. Configured reference uses the field below and should only be selected intentionally."
        )
        self.expected_rate=QDoubleSpinBox(); self.expected_rate.setRange(1,8000); self.expected_rate.setValue(1000); self.expected_rate.setSuffix(" Hz")
        self.expected_rate.setToolTip("Used only when Timing reference is set to Configured reference rate.")
        self.late_factor=QDoubleSpinBox(); self.late_factor.setRange(1.01,10.0); self.late_factor.setDecimals(2); self.late_factor.setValue(1.50); self.late_factor.setSuffix(" × reference interval")
        self.outlier_sigma=QDoubleSpinBox(); self.outlier_sigma.setRange(0.5,20.0); self.outlier_sigma.setDecimals(2); self.outlier_sigma.setValue(4.0); self.outlier_sigma.setSuffix(" σ")
        self.stationary_excursion=QDoubleSpinBox(); self.stationary_excursion.setRange(0.0001,0.5000); self.stationary_excursion.setDecimals(4); self.stationary_excursion.setValue(0.0200)
        self.stationary_excursion.setToolTip("Maximum max−min excursion allowed on every normalized stick axis before the window is considered moving rather than stationary.")
        self.graph_refresh=QSpinBox(); self.graph_refresh.setRange(33,1000); self.graph_refresh.setValue(100); self.graph_refresh.setSuffix(" ms")
        self.graph_refresh.valueChanged.connect(lambda v: self.ui_timer.setInterval(v) if hasattr(self,"ui_timer") else None)
        form.addRow("Theme",self.theme_combo)
        form.addRow("Default baseline duration",self.baseline_seconds)
        form.addRow("Timing reference",self.timing_reference_mode)
        form.addRow("Configured reference rate",self.expected_rate)
        form.addRow("Late-report threshold",self.late_factor)
        form.addRow("Oscillator outlier threshold",self.outlier_sigma)
        form.addRow("Stationary stick max excursion",self.stationary_excursion)
        form.addRow("Graph refresh interval",self.graph_refresh)
        gl.addLayout(form); layout.addWidget(g)

        s, sl=card("INSTRUMENT SAFETY LIMITS")
        sf=QFormLayout()
        self.max_freq=QDoubleSpinBox(); self.max_freq.setRange(1,1e9); self.max_freq.setValue(self.safety_limits.max_frequency_hz); self.max_freq.setSuffix(" Hz")
        self.max_amp=QDoubleSpinBox(); self.max_amp.setRange(.001,100); self.max_amp.setValue(self.safety_limits.max_amplitude_vpp); self.max_amp.setSuffix(" Vpp")
        self.max_offset=QDoubleSpinBox(); self.max_offset.setRange(.001,100); self.max_offset.setValue(self.safety_limits.max_abs_offset_v); self.max_offset.setSuffix(" V")
        sf.addRow("Maximum frequency",self.max_freq); sf.addRow("Maximum amplitude",self.max_amp); sf.addRow("Maximum absolute offset",self.max_offset)
        save=QPushButton("Save Settings"); save.clicked.connect(self._save_settings)
        sl.addLayout(sf); sl.addWidget(save,alignment=Qt.AlignmentFlag.AlignRight); layout.addWidget(s)

        simcard, siml=card("SIMULATION")
        simf=QFormLayout()
        self.sim_rate=QDoubleSpinBox(); self.sim_rate.setRange(1,8000); self.sim_rate.setValue(self.gamepad_sim.config.rate_hz); self.sim_rate.setSuffix(" Hz")
        self.sim_jitter=QDoubleSpinBox(); self.sim_jitter.setRange(0,20); self.sim_jitter.setDecimals(4); self.sim_jitter.setValue(self.sim_base_jitter_ms); self.sim_jitter.setSuffix(" ms")
        self.sim_periodic_jitter=QDoubleSpinBox(); self.sim_periodic_jitter.setRange(0,20); self.sim_periodic_jitter.setDecimals(4); self.sim_periodic_jitter.setValue(self.gamepad_sim.config.periodic_jitter_ms); self.sim_periodic_jitter.setSuffix(" ms")
        self.sim_periodic_hz=QDoubleSpinBox(); self.sim_periodic_hz.setRange(0.01,5000); self.sim_periodic_hz.setDecimals(3); self.sim_periodic_hz.setValue(self.gamepad_sim.config.periodic_hz); self.sim_periodic_hz.setSuffix(" Hz")
        self.sim_spike_every=QSpinBox(); self.sim_spike_every.setRange(0,1000000); self.sim_spike_every.setValue(self.gamepad_sim.config.spike_every); self.sim_spike_every.setSpecialValueText("Off")
        self.sim_spike_ms=QDoubleSpinBox(); self.sim_spike_ms.setRange(0,100); self.sim_spike_ms.setDecimals(4); self.sim_spike_ms.setValue(self.gamepad_sim.config.spike_ms); self.sim_spike_ms.setSuffix(" ms")
        self.sim_drop_every=QSpinBox(); self.sim_drop_every.setRange(0,1000000); self.sim_drop_every.setValue(self.gamepad_sim.config.drop_every); self.sim_drop_every.setSpecialValueText("Off")
        self.sim_osc_ppm=QDoubleSpinBox(); self.sim_osc_ppm.setRange(-100000,100000); self.sim_osc_ppm.setDecimals(5); self.sim_osc_ppm.setValue(self.sim_base_osc_ppm); self.sim_osc_ppm.setSuffix(" ppm")
        self.sim_osc_random=QDoubleSpinBox(); self.sim_osc_random.setRange(0,100000); self.sim_osc_random.setDecimals(5); self.sim_osc_random.setValue(self.osc_sim.config.random_jitter_ppm); self.sim_osc_random.setSuffix(" ppm")
        self.sim_osc_periodic=QDoubleSpinBox(); self.sim_osc_periodic.setRange(0,100000); self.sim_osc_periodic.setDecimals(5); self.sim_osc_periodic.setValue(self.osc_sim.config.periodic_jitter_ppm); self.sim_osc_periodic.setSuffix(" ppm")
        self.sim_osc_drift=QDoubleSpinBox(); self.sim_osc_drift.setRange(-10000,10000); self.sim_osc_drift.setDecimals(6); self.sim_osc_drift.setValue(self.osc_sim.config.drift_ppm_per_second); self.sim_osc_drift.setSuffix(" ppm/s")
        for label_text,widget in [
            ("Gamepad rate",self.sim_rate),("Random jitter",self.sim_jitter),("Periodic jitter",self.sim_periodic_jitter),
            ("Periodic jitter frequency",self.sim_periodic_hz),("Spike every N reports",self.sim_spike_every),("Spike size",self.sim_spike_ms),
            ("Missing-report cadence",self.sim_drop_every),("Oscillator offset",self.sim_osc_ppm),("Oscillator random jitter",self.sim_osc_random),
            ("Oscillator periodic jitter",self.sim_osc_periodic),("Oscillator drift",self.sim_osc_drift),
        ]:
            simf.addRow(label_text,widget)
        apply_sim=QPushButton("Apply Simulation Settings"); apply_sim.clicked.connect(self._apply_simulation_settings)
        siml.addLayout(simf); siml.addWidget(apply_sim,alignment=Qt.AlignmentFlag.AlignRight); layout.addWidget(simcard)

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
        self.session_id=self.db.create_session(
            "Gamepad Signal Lab capture",mode,__version__,
            {
                "nominal_frequency_hz":self.nominal_freq.value(),
                "timing_reference_mode":self.timing_reference_mode.currentData(),
                "configured_reference_rate_hz":self.expected_rate.value(),
                "host_timer_resolution_ns":self.host_timer_resolution_ns,
                "stationary_excursion_threshold":self.stationary_excursion.value(),
                "late_factor":self.late_factor.value(),
                "oscillator_outlier_sigma":self.outlier_sigma.value(),
                "late_factor":self.late_factor.value(),
                "oscillator_outlier_sigma":self.outlier_sigma.value(),
            },
        )
        self.capture_active=True
        self.capture_button.setText("Stop Capture")
        self._add_event("capture_started",{
            "mode":mode,
            "timing_reference_mode":self.timing_reference_mode.currentData(),
            "configured_reference_rate_hz":self.expected_rate.value(),
            "nominal_frequency_hz":self.nominal_freq.value(),
            "host_timer_resolution_ns":self.host_timer_resolution_ns,
        })

    def _stop_capture(self) -> None:
        if not self.capture_active:
            return
        self._add_event("capture_stopped",{
            "controller":asdict(self.current_timing),
            "oscillator":asdict(self.current_osc),
            "correlation":self.current_corr,
            "duplicate_raw_reports":self.duplicate_raw_reports,
            "controller_metadata":self.controller_metadata,
        })
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
                self._accept_controller(
                    self.sim_epoch_ns+rel,sample,"Simulation controller","simulated",
                    metadata={"controller_name":"Simulation controller","connection_method":"Simulation","backend":"GamepadSimulator"},
                )

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
                self._accept_controller(m.timestamp_ns,m.sample,m.source,m.timing_quality,m.raw_report_hex,m.duplicate_raw_report,m.metadata)

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

    def _accept_controller(self,timestamp_ns:int,sample:dict,source:str,quality:str,raw_hex:str|None=None,duplicate_raw:bool=False,metadata:dict|None=None) -> None:
        if not all(k in sample for k in ("lx","ly","rx","ry")):
            return
        self.controller_ts.append(int(timestamp_ns)); self.controller_samples.append(dict(sample)); self.controller_sources.append((source,quality))
        if metadata:
            self.controller_metadata=dict(metadata)
        if duplicate_raw:
            self.duplicate_raw_reports += 1
        if self.baseline_active: self.baseline_controller_ts.append(int(timestamp_ns))
        if self.sweep_active and self.sweep_phase=="dwell":
            self.sweep_step_controller_ts.append(int(timestamp_ns))
            self.sweep_step_controller_samples.append(dict(sample))
        if self.capture_active and self.session_id:
            self.db.add_controller_sample(self.session_id,timestamp_ns,sample,source=f"{source} [{quality}]",raw_report_hex=raw_hex)

    def _accept_oscillator(self,timestamp_ns:int,frequency_hz:float,source:str,quality:str,duty_cycle_percent:float|None=None) -> None:
        self.osc_ts.append(int(timestamp_ns)); self.osc_freq.append(float(frequency_hz)); self.osc_duty.append(duty_cycle_percent)
        if self.baseline_active:
            self.baseline_osc_freq.append(float(frequency_hz))
            if duty_cycle_percent is not None: self.baseline_osc_duty.append(float(duty_cycle_percent))
        if self.sweep_active and self.sweep_phase=="dwell":
            self.sweep_step_osc_freq.append(float(frequency_hz))
            if duty_cycle_percent is not None: self.sweep_step_osc_duty.append(float(duty_cycle_percent))
        if self.capture_active and self.session_id:
            self.db.add_oscillator_sample(self.session_id,timestamp_ns,frequency_hz,source=source,duty_cycle_percent=duty_cycle_percent,quality=quality)

    def _refresh_ui(self) -> None:
        timestamps=list(self.controller_ts)[-5000:]
        interval_pairs=[(b,(b-a)/1e6) for a,b in zip(timestamps,timestamps[1:]) if b>a]
        interval_times=[item[0] for item in interval_pairs]
        intervals=[item[1] for item in interval_pairs]
        expected_override=self._timing_reference_ms(intervals)
        reference_ms=expected_override if expected_override is not None else self._median(intervals)
        self.current_timing=timing_metrics(
            timestamps,
            expected_interval_ms=expected_override,
            late_factor=self.late_factor.value(),
        )

        osc_times_all=list(self.osc_ts)
        osc_freq_all=list(self.osc_freq)
        osc_duty_all=list(self.osc_duty)
        osc_count=min(3000,len(osc_times_all),len(osc_freq_all))
        osc_times=osc_times_all[-osc_count:] if osc_count else []
        freqs=osc_freq_all[-osc_count:] if osc_count else []
        duties=[float(value) for value in osc_duty_all[-osc_count:] if value is not None] if osc_count else []
        nominal=self.nominal_freq.value()
        self.current_osc=oscillator_metrics(
            freqs,nominal,duty_cycles_percent=duties,outlier_sigma=self.outlier_sigma.value()
        )
        self.current_corr=self._aligned_correlation(
            timestamps,osc_times,freqs,nominal,expected_interval_ms=expected_override
        )
        t,o=self.current_timing,self.current_osc

        reference_name="configured" if expected_override is not None else "measured median"
        osc_source="SIMULATED" if self.simulation_mode and self.measurement_instrument is None else "MEASURED"

        self.cards["rate"].set_value(
            f"{t.effective_rate_hz:,.2f} Hz",
            f"{t.sample_count:,} observed report timestamps",
            source="MEASURED" if not self.simulation_mode else "SIMULATED",
        )
        self.cards["interval"].set_value(
            f"{t.mean_interval_ms:.3f} ms",
            f"min {t.min_interval_ms:.3f} • max {t.max_interval_ms:.3f}",
            source="MEASURED" if not self.simulation_mode else "SIMULATED",
        )
        self.cards["jitter"].set_value(
            f"{t.rms_deviation_ms:.3f} ms",
            f"RMS vs {reference_name} {reference_ms:.3f} ms • p2p {t.peak_to_peak_jitter_ms:.3f}",
            source="CALCULATED",
        )
        self.cards["osc"].set_value(
            f"{o.mean_frequency_hz/1e6:.6f} MHz" if o.sample_count else "Unavailable",
            f"{o.sample_count:,} frequency samples • source-limited precision" if o.sample_count else "No compatible frequency samples",
            source=osc_source,
        )
        self.cards["ppm"].set_value(
            f"{o.frequency_error_ppm:+.4f} ppm" if o.sample_count else "Unavailable",
            f"{o.frequency_error_hz:+.3f} Hz vs nominal" if o.sample_count else "Requires measured frequency + nominal reference",
            source="CALCULATED",
        )
        self.cards["clock_jitter"].set_value(
            f"{o.rms_period_jitter_s*1e12:.3f} ps" if o.sample_count else "Unavailable",
            "Derived from reciprocal frequency samples; not direct phase jitter",
            source="CALCULATED",
        )
        self.cards["late"].set_value(
            str(t.late_reports),
            f"missing estimate {t.missing_reports_estimate} • raw duplicates {self.duplicate_raw_reports}",
            source="CALCULATED",
        )

        try:
            output=self.instrument.output_enabled()
        except Exception:
            output=False
        self.cards["stimulus"].set_value(
            "ON" if output else "OFF",
            f"{self.stim_freq.value():g} Hz • {self.stim_amp.value():g} Vpp",
            source="STATE",
        )
        generator_name=self.generator_id.text().removeprefix("Generator: ").split(" • ")[0] if hasattr(self,"generator_id") else self.instrument.identify()
        self.interference_status.setText(f"{generator_name} • OUTPUT {'ON' if output else 'OFF'}")
        if hasattr(self,"generator_id"):
            self.generator_id.setText(f"Generator: {generator_name} • OUTPUT {'ON' if output else 'OFF'}")
        self.output_button.setChecked(output)
        self.output_button.setText("Disable Output" if output else "Enable Output")

        deviations=[value-reference_ms for value in intervals] if intervals else []
        ppm_values=[(f-nominal)/nominal*1e6 for f in freqs] if nominal>0 else []
        interval_elapsed=self._elapsed_seconds(interval_times,timestamps[0] if timestamps else None)
        osc_elapsed=self._elapsed_seconds(osc_times,osc_times[0] if osc_times else None)

        self.dashboard_timing_chart.set_series(
            [("interval ms",intervals[-500:],"#6AA2FF")],
            x_values=interval_elapsed[-500:],
            x_label="Elapsed controller capture time (s)",
        )
        self.dashboard_osc_chart.set_series(
            [("error ppm",ppm_values[-500:],"#6DE0B1")],
            x_values=osc_elapsed[-500:],
            x_label="Elapsed oscillator capture time (s)",
        )

        samples=list(self.controller_samples)[-800:]
        sample_ts=list(self.controller_ts)[-len(samples):] if samples else []
        sample_elapsed=self._elapsed_seconds(sample_ts,sample_ts[0] if sample_ts else None)
        hist_x,hist_y=self._histogram_xy(intervals[-3000:],32)

        if not self.visualization_paused:
            smooth=max(1,self.smoothing_window.value()) if hasattr(self,"smoothing_window") else 1
            smooth_fn=lambda values: self._moving_average(values,smooth)
            self.live_interval_chart.set_series(
                [("interval ms",smooth_fn(intervals[-800:]),"#6AA2FF")],
                x_values=interval_elapsed[-800:],
                x_label="Elapsed time (s)",
            )
            self.live_jitter_chart.set_series(
                [("deviation ms",smooth_fn(deviations[-800:]),"#F0B862")],
                x_values=interval_elapsed[-800:],
                x_label="Elapsed time (s)",
            )
            self.live_hist_chart.set_series(
                [("count",hist_y,"#A989FF")],
                x_values=hist_x,
                x_label="Report interval (ms)",
            )
            self.live_latency_chart.set_series([],x_values=[],x_label="Latency (ms)")
            self.live_analog_chart.set_series(
                [
                    ("LX",smooth_fn([float(item.get("lx",0)) for item in samples]),"#6AA2FF"),
                    ("LY",smooth_fn([float(item.get("ly",0)) for item in samples]),"#6DE0B1"),
                ],
                x_values=sample_elapsed,
                x_label="Elapsed time (s)",
            )
            self.live_osc_chart.set_series(
                [("frequency Hz",smooth_fn(freqs[-800:]),"#6DE0B1")],
                x_values=osc_elapsed[-800:],
                x_label="Elapsed time (s)",
            )
            self.live_osc_jitter_chart.set_series(
                [("error ppm",smooth_fn(ppm_values[-800:]),"#F0B862")],
                x_values=osc_elapsed[-800:],
                x_label="Elapsed time (s)",
            )

        self.osc_stability_chart.set_series(
            [("frequency Hz",freqs[-1200:],"#6DE0B1")],
            x_values=osc_elapsed[-1200:],
            x_label="Elapsed time (s)",
        )
        period_pairs=[(ts,(1/f)*1e9) for ts,f in zip(osc_times,freqs) if f>0][-1200:]
        period_times=[pair[0] for pair in period_pairs]
        period_values=[pair[1] for pair in period_pairs]
        period_elapsed=self._elapsed_seconds(period_times,period_times[0] if period_times else None)
        self.osc_period_chart.set_series(
            [("derived period ns",period_values,"#6AA2FF")],
            x_values=period_elapsed,
            x_label="Elapsed time (s)",
        )

        self.osc_labels["mean"].set_value(
            f"{o.mean_frequency_hz:,.3f} Hz" if o.sample_count else "Unavailable",
            "Observed frequency-sample mean; accuracy is instrument/source limited",
            source=osc_source,
        )
        self.osc_labels["stdev"].set_value(f"{o.frequency_stdev_hz:.3f} Hz" if o.sample_count else "Unavailable","Population standard deviation",source="CALCULATED")
        self.osc_labels["error_hz"].set_value(f"{o.frequency_error_hz:+.3f} Hz" if o.sample_count else "Unavailable","Mean measured frequency − nominal",source="CALCULATED")
        self.osc_labels["error_ppm"].set_value(f"{o.frequency_error_ppm:+.4f} ppm" if o.sample_count else "Unavailable","Normalized frequency error",source="CALCULATED")
        self.osc_labels["drift"].set_value(
            f"{o.frequency_drift_ppm:+.4f} ppm" if o.sample_count else "Unavailable",
            f"{o.frequency_drift_hz:+.3f} Hz first/last analysis window" if o.sample_count else "Requires multiple samples",
            source="CALCULATED",
        )
        self.osc_labels["outliers"].set_value(str(o.outlier_count) if o.sample_count else "Unavailable",f">{self.outlier_sigma.value():.2f} σ from sample mean",source="CALCULATED")
        self.osc_labels["period"].set_value(f"{o.mean_period_s*1e9:.6f} ns" if o.sample_count else "Unavailable","Mean reciprocal-frequency period",source="CALCULATED")
        self.osc_labels["rms"].set_value(f"{o.rms_period_jitter_s*1e12:.3f} ps" if o.sample_count else "Unavailable","RMS reciprocal-period deviation",source="CALCULATED")
        self.osc_labels["p2p"].set_value(f"{o.peak_to_peak_period_jitter_s*1e12:.3f} ps" if o.sample_count else "Unavailable","Peak-to-peak reciprocal-period deviation",source="CALCULATED")
        self.osc_labels["ctc"].set_value(f"{o.cycle_to_cycle_rms_s*1e12:.3f} ps" if o.sample_count else "Unavailable","Successive sampled-period difference; see hover definition",source="CALCULATED")
        self.osc_labels["duty"].set_value(
            f"{o.duty_cycle_percent:.4f} %" if o.duty_cycle_percent is not None else "Unavailable",
            "Instrument-reported duty-cycle samples only",
            source="MEASURED" if o.duty_cycle_percent is not None else "UNAVAILABLE",
        )
        self.osc_labels["allan"].set_value(f"{o.allan_deviation_tau1:.3e}" if o.allan_deviation_tau1 is not None else "Unavailable","τ = one sample interval",source="CALCULATED")

        if samples:
            last=samples[-1]
            self.left_stick.set_position(last.get("lx",0),last.get("ly",0))
            self.right_stick.set_position(last.get("rx",0),last.get("ry",0))
            self.lt_bar.setValue(int(float(last.get("lt",0))*1000))
            self.rt_bar.setValue(int(float(last.get("rt",0))*1000))
            rolling=samples[-250:]
            stationary_noise,axis_spans=self._stationary_analog_noise(rolling,self.stationary_excursion.value())
            if stationary_noise is None:
                largest=max(axis_spans.items(),key=lambda item:item[1]) if axis_spans else ("—",0.0)
                self.axis_noise.setText(
                    f"Stationary noise: unavailable • movement detected • max excursion {largest[0].upper()} {largest[1]:.5f} "
                    f"(limit {self.stationary_excursion.value():.5f})"
                )
            else:
                self.axis_noise.setText(
                    f"Stationary noise RMS: {stationary_noise:.6f} normalized units • "
                    f"all-axis excursion ≤ {self.stationary_excursion.value():.5f}"
                )
            self.axis_noise.setToolTip(METRIC_HELP["analog_noise"])
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
            meta=self.controller_metadata
            fields=[source,f"Timing source quality: {quality}"]
            name=meta.get("controller_name")
            if name and name not in source: fields.append(f"Controller: {name}")
            vid,pid=meta.get("vid"),meta.get("pid")
            if vid is not None: fields.append(f"VID: {int(vid):04X}")
            if pid is not None: fields.append(f"PID: {int(pid):04X}")
            if meta.get("usb_path"): fields.append(f"USB path: {meta['usb_path']}")
            if meta.get("hid_interface") is not None: fields.append(f"HID interface: {meta['hid_interface']}")
            self.controller_meta.setText("\n".join(fields))
            optional=[
                f"Firmware: {meta.get('firmware_release','Unavailable')}",
                f"Battery: {meta.get('battery_status','Unavailable')}",
                f"Connection: {meta.get('connection_method','Unavailable')}",
            ]
            self.controller_capability.setText(" • ".join(optional))

        self.corr_card.set_value(
            f"{self.current_corr:+.4f}" if self.current_corr is not None else "Unavailable",
            "Nearest-time aligned samples; descriptive only",
            source="CALCULATED",
        )
        corr_pairs=[((ta+tb)//2,(tb-ta)/1e6-reference_ms) for ta,tb in zip(timestamps,timestamps[1:]) if tb>ta][-600:]
        self.corr_time_axis=[item[0] for item in corr_pairs]
        corr_game=[item[1] for item in corr_pairs]
        corr_elapsed=self._elapsed_seconds(self.corr_time_axis,self.corr_time_axis[0] if self.corr_time_axis else None)
        corr_ppm=[]
        for ts in self.corr_time_axis:
            idx=bisect_left(osc_times,ts)
            candidates=[i for i in (idx-1,idx) if 0<=i<len(osc_times)]
            if candidates:
                nearest=min(candidates,key=lambda i:abs(osc_times[i]-ts))
                corr_ppm.append((freqs[nearest]-nominal)/nominal*1e6 if nominal>0 else float("nan"))
            else:
                corr_ppm.append(float("nan"))
        corr_stimulus=[self.stim_freq.value() if output else 0.0]*len(self.corr_time_axis)
        self.corr_osc_chart.set_series([("osc error ppm",corr_ppm,"#6DE0B1")],x_values=corr_elapsed,x_label="Elapsed correlated time (s)")
        self.corr_gamepad_chart.set_series([("report deviation ms",corr_game,"#6AA2FF")],x_values=corr_elapsed,x_label="Elapsed correlated time (s)")
        self.corr_stimulus_chart.set_series([("stimulus Hz",corr_stimulus,"#F0B862")],x_values=corr_elapsed,x_label="Elapsed correlated time (s)")

        timing_source=self.controller_sources[-1][1] if self.controller_sources else "none"
        self.quality_label.setText(
            f"Controller samples {t.sample_count:,} • duration {t.duration_s:.3f} s • timing source {timing_source} • "
            f"host monotonic timer resolution {self.host_timer_resolution_ns:.0f} ns • reference {reference_name} • "
            f"effective rate {t.effective_rate_hz:.2f} Hz • consecutive identical raw HID payloads {self.duplicate_raw_reports}.\n"
            "Host-arrival timestamps include Windows/USB scheduling unless dedicated on-wire timing hardware supplies the timestamp."
        )
        self.safety_label.setText(
            f"Configured safety limits • {self.safety_limits.max_frequency_hz:g} Hz • {self.safety_limits.max_amplitude_vpp:g} Vpp • "
            f"±{self.safety_limits.max_abs_offset_v:g} V • generator output defaults OFF."
        )

        if self.baseline_active:
            duration=self.baseline_seconds.value()
            remaining=max(0,self.baseline_deadline-time.monotonic())
            self.baseline_progress.setValue(int((1-remaining/max(1,duration))*1000))
            self.baseline_state.setText(f"Baseline capture running • {remaining:.1f}s remaining")
        self.db.flush()


    def _timing_reference_ms(self, intervals_ms:list[float]) -> float | None:
        if hasattr(self,"timing_reference_mode") and self.timing_reference_mode.currentData()=="configured":
            return 1000.0/max(self.expected_rate.value(),1.0)
        return None

    @staticmethod
    def _median(values:list[float]) -> float:
        if not values:
            return 0.0
        ordered=sorted(float(value) for value in values)
        mid=len(ordered)//2
        if len(ordered)%2:
            return ordered[mid]
        return (ordered[mid-1]+ordered[mid])/2.0

    @staticmethod
    def _elapsed_seconds(timestamps_ns:list[int], origin_ns:int|None=None) -> list[float]:
        if not timestamps_ns:
            return []
        origin=int(timestamps_ns[0] if origin_ns is None else origin_ns)
        return [(int(ts)-origin)/1_000_000_000.0 for ts in timestamps_ns]

    @staticmethod
    def _histogram_xy(values:list[float],bins:int) -> tuple[list[float],list[float]]:
        if not values:
            return [],[]
        lo,hi=min(values),max(values)
        if math.isclose(lo,hi):
            return [lo],[float(len(values))]
        width=(hi-lo)/bins
        counts=[0.0]*bins
        for value in values:
            idx=min(bins-1,max(0,int((value-lo)/(hi-lo)*bins)))
            counts[idx]+=1
        centers=[lo+(i+0.5)*width for i in range(bins)]
        return centers,counts

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
        chart=LineChart(source.title,help_text=source.help_text,x_label=source.x_label)
        chart.set_series(
            [(name,values,color.name()) for name,values,color in source.series],
            x_values=source.x_values,
            x_label=source.x_label,
        )
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
    def _stationary_analog_noise(samples:list[dict], max_excursion:float) -> tuple[float | None, dict[str,float]]:
        if len(samples)<2:
            return None, {}
        rms_values=[]
        spans={}
        for axis in ("lx","ly","rx","ry"):
            values=[float(sample.get(axis,0.0)) for sample in samples]
            span=max(values)-min(values)
            spans[axis]=span
            if span>max_excursion:
                return None, spans
            mean=sum(values)/len(values)
            rms_values.append(math.sqrt(sum((value-mean)**2 for value in values)/len(values)))
        return (sum(rms_values)/len(rms_values) if rms_values else None), spans

    def _analog_noise_rms(self, samples:list[dict]) -> float | None:
        value,_=self._stationary_analog_noise(samples,self.stationary_excursion.value())
        return value

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
    def _aligned_correlation(
        controller_ts:list[int],osc_ts:list[int],osc_freq:list[float],nominal:float,
        expected_interval_ms:float|None=None,
    ) -> float|None:
        if len(controller_ts)<3 or len(osc_ts)<2 or nominal<=0: return None
        intervals=[]; midpoints=[]
        segment=controller_ts[-600:]
        for a,b in zip(segment,segment[1:]):
            if b>a:
                intervals.append((b-a)/1e6); midpoints.append((a+b)//2)
        if len(intervals)<3: return None
        expected=float(expected_interval_ms) if expected_interval_ms is not None else sorted(intervals)[len(intervals)//2]
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
        self.baseline_controller_ts=[]; self.baseline_osc_freq=[]; self.baseline_osc_duty=[]
        self.baseline_deadline=time.monotonic()+self.baseline_seconds.value()
        self.baseline_active=True; self.baseline_progress.setValue(0)
        self._add_event("baseline_started",{"duration_s":self.baseline_seconds.value()})

    def _finish_baseline(self) -> None:
        self.baseline_active=False
        baseline_intervals=[(b-a)/1e6 for a,b in zip(self.baseline_controller_ts,self.baseline_controller_ts[1:]) if b>a]
        t=timing_metrics(
            self.baseline_controller_ts,
            expected_interval_ms=self._timing_reference_ms(baseline_intervals),
            late_factor=self.late_factor.value(),
        )
        o=oscillator_metrics(
            self.baseline_osc_freq,self.nominal_freq.value(),
            duty_cycles_percent=self.baseline_osc_duty,
            outlier_sigma=self.outlier_sigma.value(),
        )
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
        self.sweep_metric_rows=[]
        self.sweep_index=0
        self.sweep_phase="idle"
        self.sweep_step_controller_ts=[]
        self.sweep_step_controller_samples=[]
        self.sweep_step_osc_freq=[]
        self.sweep_step_osc_duty=[]
        self.sweep_step_osc_duty=[]
        self.sweep_reference_rate_hz=self.current_timing.effective_rate_hz or self.expected_rate.value()
        self.sweep_table.setRowCount(0)
        self.sweep_heatmap.set_points([])
        self.sweep_active=True
        self._add_event("sweep_started",{
            "points":len(plan),
            "settling_ms":self.settle_ms.value(),
            "dwell_ms":self.dwell_ms.value(),
            "randomized":self.sweep_random.isChecked(),
            "reference_rate_hz":self.sweep_reference_rate_hz,
            "timing_reference_mode":self.timing_reference_mode.currentData(),
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
        self.sweep_step_controller_samples=[]
        self.sweep_step_osc_freq=[]
        self.sweep_phase="dwell"
        self.sweep_timer.start(self.dwell_ms.value())

    def _refresh_sweep_heatmap(self) -> None:
        if not hasattr(self,"sweep_heatmap"):
            return
        metric=self.sweep_heatmap_metric.currentData() if hasattr(self,"sweep_heatmap_metric") else "gamepad"
        specs={
            "gamepad":("gamepad_rms_ms","Frequency / amplitude response • gamepad RMS timing deviation (ms)"),
            "osc_jitter":("oscillator_jitter_ps","Frequency / amplitude response • oscillator RMS period jitter (ps)"),
            "polling":("polling_deviation_hz","Frequency / amplitude response • polling-rate deviation (Hz)"),
            "clock":("clock_ppm","Frequency / amplitude response • clock frequency deviation (ppm)"),
            "late":("late_reports","Frequency / amplitude response • late reports"),
            "analog":("analog_noise_rms","Frequency / amplitude response • analog stationary noise RMS"),
        }
        key,title=specs.get(metric,specs["gamepad"])
        self.sweep_heatmap.title=title
        points=[
            (row["frequency_hz"],row["amplitude_vpp"],float(row[key]))
            for row in self.sweep_metric_rows
            if row.get(key) is not None
        ]
        self.sweep_heatmap.set_points(points)

    def _sweep_record_and_advance(self) -> None:
        if not self.sweep_active or self.sweep_index>=len(self.sweep_plan):
            return

        freq,amp,rep=self.sweep_plan[self.sweep_index]
        step_intervals=[(b-a)/1e6 for a,b in zip(self.sweep_step_controller_ts,self.sweep_step_controller_ts[1:]) if b>a]
        step_timing=timing_metrics(
            self.sweep_step_controller_ts,
            expected_interval_ms=self._timing_reference_ms(step_intervals),
            late_factor=self.late_factor.value(),
        )
        step_osc=oscillator_metrics(
            self.sweep_step_osc_freq,
            self.nominal_freq.value(),
            duty_cycles_percent=self.sweep_step_osc_duty,
            outlier_sigma=self.outlier_sigma.value(),
        )
        gp=step_timing.rms_deviation_ms if step_timing.sample_count>=2 else None
        ppm=step_osc.frequency_error_ppm if step_osc.sample_count else None
        polling_dev=(step_timing.effective_rate_hz-self.sweep_reference_rate_hz) if step_timing.sample_count>=2 else None
        osc_jitter_ps=(step_osc.rms_period_jitter_s*1e12) if step_osc.sample_count else None
        analog_noise=self._analog_noise_rms(self.sweep_step_controller_samples)
        metric_row={
            "frequency_hz":freq,
            "amplitude_vpp":amp,
            "gamepad_rms_ms":gp,
            "oscillator_jitter_ps":osc_jitter_ps,
            "polling_deviation_hz":polling_dev,
            "clock_ppm":ppm,
            "late_reports":step_timing.late_reports if step_timing.sample_count>=2 else None,
            "analog_noise_rms":analog_noise,
        }
        self.sweep_metric_rows.append(metric_row)
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
            "oscillator_jitter_ps":osc_jitter_ps,
            "polling_deviation_hz":polling_dev,
            "clock_ppm":ppm,
            "late_reports":metric_row["late_reports"],
            "analog_noise_rms":analog_noise,
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
        try:
            support_log_event("signal_lab_event", event_type=event_type, details=payload)
        except Exception:
            pass
        self._update_timeline()

    def _update_timeline(self) -> None:
        if not hasattr(self,"timeline_table"): return
        rows=self.events[-300:]; self.timeline_table.setRowCount(len(rows))
        for r,(ts,event,payload) in enumerate(rows):
            self.timeline_table.setItem(r,0,QTableWidgetItem(str(ts))); self.timeline_table.setItem(r,1,QTableWidgetItem(event)); self.timeline_table.setItem(r,2,QTableWidgetItem(json.dumps(payload,separators=(",",":"))))
        self._update_correlation_events()

    def _update_correlation_events(self) -> None:
        if not hasattr(self,"corr_events_table"):
            return
        rows=self.events[-120:]
        self.corr_events_table.setRowCount(len(rows))
        for r,(ts,event,payload) in enumerate(rows):
            item=QTableWidgetItem(f"{ts/1e9:.6f} s")
            item.setData(Qt.ItemDataRole.UserRole,int(ts))
            self.corr_events_table.setItem(r,0,item)
            self.corr_events_table.setItem(r,1,QTableWidgetItem(event))
            self.corr_events_table.setItem(r,2,QTableWidgetItem(json.dumps(payload,separators=(",",":"))))

    def _add_user_marker(self) -> None:
        label=self.marker_text.text().strip() if hasattr(self,"marker_text") else ""
        self._add_event("user_marker",{"label":label or "Marker"})
        if hasattr(self,"marker_text"):
            self.marker_text.clear()

    def _sync_correlation_cursor(self,source:LineChart,ratio:float) -> None:
        for chart in getattr(self,"corr_charts",[]):
            chart.set_external_cursor_ratio(None if chart is source else ratio)

    def _clear_correlation_cursor(self) -> None:
        for chart in getattr(self,"corr_charts",[]):
            chart.set_external_cursor_ratio(None)

    def _select_correlation_event(self,row:int,column:int) -> None:
        item=self.corr_events_table.item(row,0)
        if item is None:
            return
        ts=item.data(Qt.ItemDataRole.UserRole)
        if ts is None or not self.corr_time_axis:
            return
        idx=min(range(len(self.corr_time_axis)),key=lambda i:abs(self.corr_time_axis[i]-int(ts)))
        ratio=idx/max(1,len(self.corr_time_axis)-1)
        for chart in getattr(self,"corr_charts",[]):
            chart.set_external_cursor_ratio(ratio)
        event=self.corr_events_table.item(row,1).text() if self.corr_events_table.item(row,1) else ""
        details=self.corr_events_table.item(row,2).text() if self.corr_events_table.item(row,2) else ""
        self.corr_event_detail.setText(f"{event} • {details}")

    def _refresh_experiments(self) -> None:
        if not hasattr(self,"experiments_table"): return
        rows=self.db.list_sessions(limit=200); self.experiments_table.setRowCount(len(rows))
        for r,row in enumerate(rows):
            vals=[row["created_utc"],row["name"],row["mode"],row["controller_samples"],row["oscillator_samples"],row["events"],row["id"]]
            for c,val in enumerate(vals): self.experiments_table.setItem(r,c,QTableWidgetItem(str(val)))
        self._refresh_compare_sources()

    def _refresh_compare_sources(self) -> None:
        if not hasattr(self,"compare_a"):
            return
        rows=self.db.list_sessions(limit=200)
        current_a=self.compare_a.currentData()
        current_b=self.compare_b.currentData()
        for combo in (self.compare_a,self.compare_b):
            combo.clear()
            for row in rows:
                label=f"{row['created_utc']} • {row['mode']} • {row['controller_samples']} pad / {row['oscillator_samples']} clock"
                combo.addItem(label,row["id"])
        if current_a:
            index=self.compare_a.findData(current_a)
            if index>=0: self.compare_a.setCurrentIndex(index)
        if current_b:
            index=self.compare_b.findData(current_b)
            if index>=0: self.compare_b.setCurrentIndex(index)
        if self.compare_b.count()>1 and self.compare_b.currentIndex()==self.compare_a.currentIndex():
            self.compare_b.setCurrentIndex(1)

    def _session_metrics(self,session_id:str):
        data=self.db.session_series(session_id)
        metadata=data["session"].get("metadata") or {}
        configured_rate=float(metadata.get("configured_reference_rate_hz") or metadata.get("expected_rate_hz") or self.expected_rate.value())
        reference_mode=str(metadata.get("timing_reference_mode") or "median")
        nominal=float(metadata.get("nominal_frequency_hz") or self.nominal_freq.value())
        timing=timing_metrics(
            data["controller_timestamps_ns"],
            expected_interval_ms=(1000.0/max(configured_rate,1.0)) if reference_mode=="configured" else None,
            late_factor=float(metadata.get("late_factor") or self.late_factor.value()),
        )
        oscillator=oscillator_metrics(
            data["oscillator_frequencies_hz"],
            nominal,
            duty_cycles_percent=data["duty_cycles_percent"],
            outlier_sigma=float(metadata.get("oscillator_outlier_sigma") or self.outlier_sigma.value()),
        )
        return timing,oscillator,data["session"]

    @staticmethod
    def _comparison_metric_help(name:str) -> str:
        help_map={
            "Effective rate Hz":METRIC_HELP["rate"],
            "Mean interval ms":METRIC_HELP["interval"],
            "RMS timing deviation ms":METRIC_HELP["jitter"],
            "Peak-to-peak jitter ms":"Maximum observed report interval minus minimum observed report interval.",
            "P99 ms":"99th percentile of positive consecutive controller-report intervals.",
            "P99.9 ms":"99.9th percentile of positive consecutive controller-report intervals.",
            "Late reports":METRIC_HELP["late"],
            "Missing reports estimate":"Estimated missing reports inferred from unusually long intervals relative to the selected timing reference.",
            "Clock frequency Hz":METRIC_HELP["osc_mean"],
            "Clock error ppm":METRIC_HELP["osc_error_ppm"],
            "Oscillator RMS jitter s":METRIC_HELP["osc_rms"],
            "Successive sampled-period RMS s":METRIC_HELP["osc_ctc"],
            "Duty cycle %":METRIC_HELP["duty"],
        }
        return help_map.get(name,name)

    def _populate_compare_table(self,reference_t,reference_o,test_t,test_o,label:str) -> None:
        metrics=[
            ("Effective rate Hz",reference_t.effective_rate_hz,test_t.effective_rate_hz),
            ("Mean interval ms",reference_t.mean_interval_ms,test_t.mean_interval_ms),
            ("RMS timing deviation ms",reference_t.rms_deviation_ms,test_t.rms_deviation_ms),
            ("Peak-to-peak jitter ms",reference_t.peak_to_peak_jitter_ms,test_t.peak_to_peak_jitter_ms),
            ("P99 ms",reference_t.p99_ms,test_t.p99_ms),("P99.9 ms",reference_t.p999_ms,test_t.p999_ms),
            ("Late reports",reference_t.late_reports,test_t.late_reports),
            ("Missing reports estimate",reference_t.missing_reports_estimate,test_t.missing_reports_estimate),
            ("Clock frequency Hz",reference_o.mean_frequency_hz,test_o.mean_frequency_hz),
            ("Clock error ppm",reference_o.frequency_error_ppm,test_o.frequency_error_ppm),
            ("Oscillator RMS jitter s",reference_o.rms_period_jitter_s,test_o.rms_period_jitter_s),
            ("Successive sampled-period RMS s",reference_o.cycle_to_cycle_rms_s,test_o.cycle_to_cycle_rms_s),
            ("Duty cycle %",reference_o.duty_cycle_percent,test_o.duty_cycle_percent),
        ]
        self.compare_state.setText(label)
        self.compare_table.setRowCount(len(metrics))
        for r,(name,ref,cur) in enumerate(metrics):
            if ref is None or cur is None:
                values=[name,"Unavailable" if ref is None else f"{float(ref):.9g}","Unavailable" if cur is None else f"{float(cur):.9g}","Unavailable","Unavailable"]
            else:
                ref_value=float(ref); cur_value=float(cur)
                pct="Unavailable" if math.isclose(ref_value,0.0,abs_tol=1e-30) else f"{((cur_value-ref_value)/abs(ref_value))*100:+.3f}%"
                values=[name,f"{ref_value:.9g}",f"{cur_value:.9g}",f"{cur_value-ref_value:+.9g}",pct]
            for c,val in enumerate(values):
                item=QTableWidgetItem(str(val))
                item.setToolTip(self._comparison_metric_help(name))
                self.compare_table.setItem(r,c,item)

    def _compare_saved_sessions(self) -> None:
        a=self.compare_a.currentData() if hasattr(self,"compare_a") else None
        b=self.compare_b.currentData() if hasattr(self,"compare_b") else None
        if not a or not b:
            QMessageBox.information(self,"Compare","Choose two saved sessions.")
            return
        ta,oa,sa=self._session_metrics(str(a))
        tb,ob,sb=self._session_metrics(str(b))
        self._populate_compare_table(ta,oa,tb,ob,f"Saved session comparison • {sa['created_utc']} → {sb['created_utc']}")

    def _refresh_compare(self) -> None:
        if not hasattr(self,"compare_table"): return
        if not self.reference_baseline:
            self.compare_state.setText("No reference baseline selected. Run a baseline and set it as reference, or compare two saved sessions.")
            self.compare_table.setRowCount(0)
            return
        rt=timing_metrics([])
        ro=oscillator_metrics([],self.nominal_freq.value())
        for key,value in self.reference_baseline["timing"].items():
            if hasattr(rt,key):
                pass
        from .analysis import TimingMetrics, OscillatorMetrics
        rt=TimingMetrics(**self.reference_baseline["timing"])
        ro=OscillatorMetrics(**self.reference_baseline["oscillator"])
        self._populate_compare_table(rt,ro,self.current_timing,self.current_osc,"Reference baseline vs live measurement")

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
        intervals=[(b-a)/1e6 for a,b in zip(timestamps,timestamps[1:]) if b>a]
        expected_override=self._timing_reference_ms(intervals)
        report_reference_ms=expected_override if expected_override is not None else self._median(intervals)
        deviations=[value-report_reference_ms for value in intervals]
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
                "timing_reference_mode":self.timing_reference_mode.currentData(),
                "timing_reference_interval_ms":report_reference_ms,
                "configured_reference_rate_hz":self.expected_rate.value(),
                "host_timer_resolution_ns":self.host_timer_resolution_ns,
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
                "Report interval (ms)":intervals[-1200:],
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

    def _apply_simulation_settings(self) -> None:
        self.gamepad_sim.config.rate_hz=self.sim_rate.value()
        self.sim_base_jitter_ms=self.sim_jitter.value()
        self.gamepad_sim.config.jitter_ms=self.sim_base_jitter_ms
        self.gamepad_sim.config.periodic_jitter_ms=self.sim_periodic_jitter.value()
        self.gamepad_sim.config.periodic_hz=self.sim_periodic_hz.value()
        self.gamepad_sim.config.spike_every=self.sim_spike_every.value()
        self.gamepad_sim.config.spike_ms=self.sim_spike_ms.value()
        self.gamepad_sim.config.drop_every=self.sim_drop_every.value()
        self.sim_base_osc_ppm=self.sim_osc_ppm.value()
        self.osc_sim.config.ppm_offset=self.sim_base_osc_ppm
        self.osc_sim.config.random_jitter_ppm=self.sim_osc_random.value()
        self.osc_sim.config.periodic_jitter_ppm=self.sim_osc_periodic.value()
        self.osc_sim.config.drift_ppm_per_second=self.sim_osc_drift.value()
        self._add_event("simulation_settings",{
            "gamepad_rate_hz":self.sim_rate.value(),
            "random_jitter_ms":self.sim_jitter.value(),
            "periodic_jitter_ms":self.sim_periodic_jitter.value(),
            "periodic_hz":self.sim_periodic_hz.value(),
            "spike_every":self.sim_spike_every.value(),
            "spike_ms":self.sim_spike_ms.value(),
            "drop_every":self.sim_drop_every.value(),
            "oscillator_ppm_offset":self.sim_osc_ppm.value(),
            "oscillator_random_jitter_ppm":self.sim_osc_random.value(),
            "oscillator_periodic_jitter_ppm":self.sim_osc_periodic.value(),
            "oscillator_drift_ppm_per_second":self.sim_osc_drift.value(),
        })

    def _apply_saved_settings(self) -> None:
        self.baseline_seconds.setValue(int(self.settings.value("baseline_seconds",60)))
        saved_reference=str(self.settings.value("timing_reference_mode","median"))
        reference_index=self.timing_reference_mode.findData(saved_reference)
        self.timing_reference_mode.setCurrentIndex(max(0,reference_index))
        self.expected_rate.setValue(float(self.settings.value("expected_rate",1000)))
        self.late_factor.setValue(float(self.settings.value("late_factor",1.5)))
        self.outlier_sigma.setValue(float(self.settings.value("outlier_sigma",4.0)))
        self.stationary_excursion.setValue(float(self.settings.value("stationary_excursion",0.02)))
        self.nominal_freq.setValue(float(self.settings.value("nominal_freq",12_000_000)))
        self.graph_refresh.setValue(int(self.settings.value("graph_refresh",100)))
        self.max_freq.setValue(float(self.settings.value("max_freq",20_000_000)))
        self.max_amp.setValue(float(self.settings.value("max_amp",1.0)))
        self.max_offset.setValue(float(self.settings.value("max_offset",0.5)))
        self.sim_rate.setValue(float(self.settings.value("sim_rate",1000.0)))
        self.sim_jitter.setValue(float(self.settings.value("sim_jitter",0.05)))
        self.sim_periodic_jitter.setValue(float(self.settings.value("sim_periodic_jitter",0.0)))
        self.sim_periodic_hz.setValue(float(self.settings.value("sim_periodic_hz",60.0)))
        self.sim_spike_every.setValue(int(self.settings.value("sim_spike_every",0)))
        self.sim_spike_ms.setValue(float(self.settings.value("sim_spike_ms",1.0)))
        self.sim_drop_every.setValue(int(self.settings.value("sim_drop_every",0)))
        self.sim_osc_ppm.setValue(float(self.settings.value("sim_osc_ppm",-1.5)))
        self.sim_osc_random.setValue(float(self.settings.value("sim_osc_random",0.25)))
        self.sim_osc_periodic.setValue(float(self.settings.value("sim_osc_periodic",0.15)))
        self.sim_osc_drift.setValue(float(self.settings.value("sim_osc_drift",0.01)))
        self._sync_safety_limits()
        self._apply_simulation_settings()

    def _save_settings(self) -> None:
        self._sync_safety_limits()
        values={
            "theme":self.theme_combo.currentText(),"baseline_seconds":self.baseline_seconds.value(),
            "timing_reference_mode":self.timing_reference_mode.currentData(),
            "expected_rate":self.expected_rate.value(),"late_factor":self.late_factor.value(),"outlier_sigma":self.outlier_sigma.value(),
            "stationary_excursion":self.stationary_excursion.value(),"nominal_freq":self.nominal_freq.value(),
            "graph_refresh":self.graph_refresh.value(),"max_freq":self.max_freq.value(),
            "max_amp":self.max_amp.value(),"max_offset":self.max_offset.value(),
            "sim_rate":self.sim_rate.value(),"sim_jitter":self.sim_jitter.value(),
            "sim_periodic_jitter":self.sim_periodic_jitter.value(),"sim_periodic_hz":self.sim_periodic_hz.value(),
            "sim_spike_every":self.sim_spike_every.value(),"sim_spike_ms":self.sim_spike_ms.value(),
            "sim_drop_every":self.sim_drop_every.value(),"sim_osc_ppm":self.sim_osc_ppm.value(),
            "sim_osc_random":self.sim_osc_random.value(),"sim_osc_periodic":self.sim_osc_periodic.value(),
            "sim_osc_drift":self.sim_osc_drift.value()
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
        try:
            support_log_event("gamepad_signal_lab_stop", version=__version__)
        except Exception:
            pass
        self.db.close(); event.accept()


def main() -> int:
    app=QApplication.instance() or QApplication([])
    app.setApplicationName("Gamepad Signal Lab")
    app.setOrganizationName("SensoredRooster")
    app.setStyle("Fusion")
    window=MainWindow()
    window.show()
    return app.exec()
