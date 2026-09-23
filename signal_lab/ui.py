"""Modern Qt desktop shell for RcmTool."""
from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import asdict
import json
import logging
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
from .controller import ControllerAcquisition, ControllerMeasurement, detect_controller_family
from .instruments import SafetyLimits, UnavailableInstrument, VisaScpiGenerator, VisaScpiMeasurementInstrument, list_visa_resources
from .metric_catalog import CHART_HELP, METRIC_HELP
from .noise_attribution import analyze_noise_capture
from .oscillator import OscillatorAcquisition, OscillatorMeasurement
from .reporting import write_html_report
from .storage import LabDatabase
from .sweep import make_sweep
from .theme import DARK, LIGHT
from .widgets import ControllerView, HeatMapWidget, LineChart, MetricCard
from support import (
    SESSION_ID as SUPPORT_SESSION_ID,
    create_support_bundle,
    health_snapshot,
    log_event as support_log_event,
    open_logs_folder,
    open_repository,
    report_issue,
    start_heartbeat,
    support_bundle_preview,
    upload_support_bundle,
)

LOGGER = logging.getLogger(__name__)

NAV = [
    "Dashboard", "Live Capture", "Controller Lab", "Oscillator Lab",
    "Interference Lab", "Sweep Lab", "Correlation", "Experiments",
    "Compare", "Reports", "Instruments", "Support", "Settings",
]
FOCUS_NAV = ("Dashboard", "Controller Lab", "Reports", "Support", "Settings")

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

    def __init__(self, bundle_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.bundle_path = bundle_path

    def run(self) -> None:
        try:
            self.completed.emit(upload_support_bundle(bundle_path=self.bundle_path))
        except Exception as exc:
            self.failed.emit(str(exc))


class WelcomeDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to RcmTool")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        title = QLabel("GAMEPAD SIGNAL LAB")
        title.setObjectName("Title")
        layout.addWidget(title)
        copy = QLabel(
            "A hardware-only controller noise and jitter workstation.\n\n"
            "1  Connect the controller by USB\n"
            "2  Select a named Raw HID device\n"
            "3  Run the guided neutral and movement tests\n"
            "4  Export the measured evidence\n\n"
            "No simulated controller values are used. A physical controller is required."
        )
        copy.setWordWrap(True)
        layout.addWidget(copy)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"RcmTool {__version__}")
        self.resize(1480, 920)
        self.setMinimumSize(1120, 720)
        self.settings = QSettings("SensoredRooster", "GamepadSignalLab")
        self.theme_name = str(self.settings.value("theme", "Dark"))
        self.setStyleSheet(LIGHT if self.theme_name == "Light" else DARK)

        data_root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "GamepadSignalLab"
        data_root.mkdir(parents=True, exist_ok=True)
        self.data_root = data_root
        self.db = LabDatabase(data_root / "signal_lab.sqlite3")

        self.capture_active = False
        self.session_id: str | None = None
        self.controller_acquisition: ControllerAcquisition | None = None
        self.controller_source_kind = "automatic"
        self.controller_source_path = None
        self.controller_source_info: dict = {}
        self.controller_queue: queue.Queue[ControllerMeasurement] = queue.Queue()
        self.controller_event_queue: queue.Queue = queue.Queue()
        self.osc_queue: queue.Queue = queue.Queue()

        self.instrument = UnavailableInstrument()
        self.measurement_instrument = None
        self.osc_acquisition: OscillatorAcquisition | None = None
        self.instrument_lock = threading.Lock()
        self.safety_limits = SafetyLimits()

        self.duplicate_raw_reports = 0

        self.controller_ts = deque(maxlen=60000)
        self.controller_samples = deque(maxlen=60000)
        self.controller_sources = deque(maxlen=60000)
        self.controller_raw_report_hex = deque(maxlen=60000)
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
        self.noise_test_active = False
        self.noise_test_kind = ""
        self.noise_test_deadline = 0.0
        self.noise_test_start_timestamp_ns = 0
        self.noise_test_result: dict | None = None
        self.noise_test_results: dict[str, dict] = {}
        self.noise_wizard: dict | None = None

        start_heartbeat()
        support_log_event("gamepad_signal_lab_start", version=__version__)
        self._build_ui()
        self._apply_saved_settings()
        self._start_controller_acquisition()

        self.sample_timer = QTimer(self)
        self.sample_timer.timeout.connect(self._sample_tick)
        self.sample_timer.start(20)

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._refresh_ui)
        self.ui_timer.start(self.graph_refresh.value())

        self.db_flush_timer = QTimer(self)
        self.db_flush_timer.timeout.connect(self._flush_database_buffer)
        self.db_flush_timer.start(500)

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
        side.setContentsMargins(12, 14, 12, 14)

        brand = QLabel("RcmTool")
        brand.setObjectName("Brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        side.addWidget(brand)
        side.addSpacing(14)

        self.nav_buttons: dict[str, QPushButton] = {}
        for index, name in enumerate(NAV):
            if name not in FOCUS_NAV:
                continue
            button = QPushButton(name)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self._navigate(i))
            side.addWidget(button)
            self.nav_buttons[name] = button

        side.addStretch(1)
        self.hardware_status = QLabel("HARDWARE • controller discovery active")
        self.hardware_status.setObjectName("Good")
        self.hardware_status.setWordWrap(True)
        side.addWidget(self.hardware_status)
        self.database_status = QLabel(f"DB • {self.db.path.name}")
        self.database_status.setObjectName("Muted")
        side.addWidget(self.database_status)
        self.error_banner = QLabel()
        self.error_banner.setObjectName("Warn")
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        side.addWidget(self.error_banner)
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
        self.capture_button = QPushButton("Record Session")
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
        for name, button in self.nav_buttons.items():
            button.setChecked(NAV[index] == name)
        if NAV[index] == "Experiments":
            self._refresh_experiments()
        elif NAV[index] == "Compare":
            self._refresh_compare_sources()
            self._refresh_compare()
        elif NAV[index] == "Instruments":
            self._refresh_capabilities()
        if hasattr(self, "ui_timer"):
            QTimer.singleShot(0, self._refresh_ui)

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
            "Focused hardware view: controller noise, report jitter, and movement/settling behavior. No simulated values are used.",
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        self.cards = {}
        specs = [
            ("rate","Gamepad rate","MEASURED"), ("interval","Report interval","MEASURED"),
            ("jitter","Report jitter","CALCULATED"), ("late","Duplicate / late reports","CALCULATED"),
            ("osc","Oscillator","UNUSED"), ("ppm","Clock error","UNUSED"),
            ("clock_jitter","Clock jitter","UNUSED"), ("stimulus","Test stimulus","UNUSED"),
        ]
        for i, (key, title, source) in enumerate(specs[:4]):
            c = MetricCard(title, help_text=METRIC_HELP[key], source=source)
            self.cards[key] = c
            grid.addWidget(c, i // 4, i % 4)
        for key, title, source in specs[4:]:
            self.cards[key] = MetricCard(title, help_text=METRIC_HELP[key], source=source)
            self.cards[key].setVisible(False)
        layout.addLayout(grid)

        evidence, evidence_layout = card("RC FILTER / NOISE EVIDENCE")
        evidence_help = QLabel(
            "Use the guided Raw HID test to measure stationary noise and movement settling. "
            "The export keeps the paired timestamps, normalized samples, and raw HID bytes."
        )
        evidence_help.setWordWrap(True)
        evidence_help.setObjectName("Muted")
        evidence_layout.addWidget(evidence_help)
        evidence_actions = QHBoxLayout()
        guided = QPushButton("Guided smoothing test")
        guided.clicked.connect(self._run_noise_wizard)
        open_controller = QPushButton("Open Controller Lab")
        open_controller.clicked.connect(lambda: self._navigate(NAV.index("Controller Lab")))
        export_evidence = QPushButton("Export evidence")
        export_evidence.clicked.connect(self._export_noise_evidence)
        evidence_actions.addWidget(guided)
        evidence_actions.addWidget(open_controller)
        evidence_actions.addWidget(export_evidence)
        evidence_actions.addStretch(1)
        evidence_layout.addLayout(evidence_actions)
        self.dashboard_noise_status = QLabel("No Raw HID smoothing evidence captured.")
        self.dashboard_noise_status.setObjectName("Muted")
        self.dashboard_noise_status.setWordWrap(True)
        evidence_layout.addWidget(self.dashboard_noise_status)
        layout.addWidget(evidence)

        baseline, bl = card("SESSION RECORDING")
        state_row = QHBoxLayout()
        self.baseline_state = QLabel("Raw samples are acquired continuously. Record Session stores them in the local testing database.")
        self.baseline_state.setObjectName("Muted")
        self.baseline_state.setWordWrap(True)
        self.baseline_progress = QProgressBar()
        self.baseline_progress.setRange(0,1000)
        self.baseline_progress.setVisible(False)
        state_row.addWidget(self.baseline_state,2)
        state_row.addWidget(self.baseline_progress,1)
        bl.addLayout(state_row)
        layout.addWidget(baseline)

        charts = QGridLayout()
        charts.setHorizontalSpacing(14)
        self.dashboard_timing_chart = LineChart(
            "Controller report interval",
            help_text=CHART_HELP["report_interval"],
            x_label="Elapsed controller capture time (s)",
        )
        self.dashboard_osc_chart = LineChart(
            "Unused oscillator chart",
            help_text=CHART_HELP["osc_ppm"],
            x_label="Elapsed time (s)",
        )
        self.dashboard_osc_chart.setVisible(False)
        self.dashboard_noise_chart = LineChart(
            "Raw HID analog output",
            help_text=CHART_HELP["analog_stability"],
            x_label="Elapsed controller capture time (s)",
        )
        charts.addWidget(self.dashboard_timing_chart,0,0)
        charts.addWidget(self.dashboard_noise_chart,0,1)
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

        control_grid = QGridLayout()
        control_grid.setHorizontalSpacing(10)
        control_grid.setVerticalSpacing(10)
        control_grid.addWidget(self.pause_visualization,0,0)
        control_grid.addWidget(self.smoothing_window,0,1,1,2)
        control_grid.addWidget(reset,0,3)
        control_grid.addWidget(export,1,0)
        control_grid.addWidget(fullscreen,1,1)
        control_grid.addWidget(raw,1,2)
        control_grid.setColumnStretch(2,1)
        control_grid.setColumnStretch(3,1)
        controls_layout.addLayout(control_grid)
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
        w, layout = page(
            "Controller Lab",
            "Live controller state first; backend and measurement diagnostics stay separate so unavailable data is never implied.",
        )

        identity, il = card("CONNECTED CONTROLLER")
        identity_row = QHBoxLayout()
        self.controller_meta = QLabel("No physical controller detected")
        self.controller_meta.setObjectName("Good")
        self.controller_meta.setWordWrap(True)
        identity_row.addWidget(self.controller_meta, 1)

        selector_box = QVBoxLayout()
        selector_label = QLabel("CONTROLLER VIEW")
        selector_label.setObjectName("Eyebrow")
        self.controller_skin_combo = QComboBox()
        self.controller_skin_combo.addItem("Auto", "auto")
        self.controller_skin_combo.addItem("Xbox", "xbox")
        self.controller_skin_combo.addItem("DualSense", "dualsense")
        self.controller_skin_combo.addItem("Generic", "generic")
        self.controller_skin_combo.setMinimumWidth(170)
        self.controller_skin_combo.setToolTip(
            "Auto selects the shell from backend and USB identity. Manual override changes only the visual shell; "
            "it does not invent button mappings."
        )
        self.controller_skin_combo.currentIndexChanged.connect(self._controller_skin_changed)
        self.controller_skin_status = QLabel("Auto detection: Generic")
        self.controller_skin_status.setObjectName("Muted")
        selector_box.addWidget(selector_label)
        selector_box.addWidget(self.controller_skin_combo)
        selector_box.addWidget(self.controller_skin_status)
        identity_row.addLayout(selector_box)
        il.addLayout(identity_row)
        layout.addWidget(identity)

        visual, vl = card("LIVE CONTROLLER STATE")
        self.controller_view = ControllerView()
        vl.addWidget(self.controller_view)
        self.controller_axes_readout = QLabel("LX +0.0000  •  LY +0.0000  •  RX +0.0000  •  RY +0.0000  •  LT 0.0%  •  RT 0.0%")
        self.controller_axes_readout.setObjectName("Muted")
        self.controller_axes_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.controller_axes_readout.setWordWrap(True)
        vl.addWidget(self.controller_axes_readout)
        layout.addWidget(visual)

        diagnostics = QGridLayout()
        diagnostics.setHorizontalSpacing(14)
        diagnostics.setVerticalSpacing(14)

        input_card, input_layout = card("BUTTONS / D-PAD")
        self.button_capability = QLabel("Waiting for a decoded input sample")
        self.button_capability.setObjectName("Muted")
        self.button_capability.setWordWrap(True)
        input_layout.addWidget(self.button_capability)
        diagnostics.addWidget(input_card,0,0)

        signal_card, signal_layout = card("ANALOG SIGNAL")
        self.axis_noise = QLabel("Stationary noise: waiting for samples")
        self.axis_noise.setObjectName("Muted")
        self.axis_noise.setWordWrap(True)
        signal_layout.addWidget(self.axis_noise)
        diagnostics.addWidget(signal_card,0,1)

        device_card, device_layout = card("BACKEND / DEVICE DETAILS")
        self.controller_capability = QLabel("Firmware / battery / USB path: shown only when the active backend can report them.")
        self.controller_capability.setObjectName("Muted")
        self.controller_capability.setWordWrap(True)
        device_layout.addWidget(self.controller_capability)
        diagnostics.addWidget(device_card,1,0,1,2)

        source_card, source_layout = card("INPUT SOURCE")
        source_row = QHBoxLayout()
        self.controller_source_combo = QComboBox()
        self.controller_source_combo.setMinimumWidth(420)
        self.controller_source_combo.setToolTip(
            "Automatic tries XInput, SDL, Raw HID, then DirectInput. Raw HID reads the selected device's USB HID reports."
        )
        self.controller_source_combo.currentIndexChanged.connect(self._controller_source_changed)
        refresh_sources = QPushButton("Refresh devices")
        refresh_sources.clicked.connect(self._refresh_controller_sources)
        source_row.addWidget(self.controller_source_combo, 1)
        source_row.addWidget(refresh_sources)
        source_layout.addLayout(source_row)
        self.controller_source_status = QLabel(
            "Automatic backend selection. Hardware acquisition is always active when the app is running."
        )
        self.controller_source_status.setObjectName("Muted")
        self.controller_source_status.setWordWrap(True)
        source_layout.addWidget(self.controller_source_status)
        diagnostics.addWidget(source_card, 2, 0, 1, 2)

        evidence_card, evidence_layout = card("NOISE ATTRIBUTION EVIDENCE")
        evidence_help = QLabel(
            "Raw HID only: the first test measures stationary output noise; the second measures movement/settling behavior. "
            "Neither can prove firmware filtering without an oscilloscope or logic analyzer upstream of USB."
        )
        evidence_help.setObjectName("Muted")
        evidence_help.setWordWrap(True)
        evidence_layout.addWidget(evidence_help)
        evidence_row = QHBoxLayout()
        guided_test = QPushButton("Guided smoothing test")
        guided_test.clicked.connect(self._run_noise_wizard)
        export_evidence = QPushButton("Export evidence")
        export_evidence.clicked.connect(self._export_noise_evidence)
        evidence_row.addWidget(guided_test)
        evidence_row.addWidget(export_evidence)
        evidence_row.addStretch(1)
        evidence_layout.addLayout(evidence_row)
        self.noise_test_status = QLabel("No attribution capture has been run.")
        self.noise_test_status.setObjectName("Muted")
        self.noise_test_status.setWordWrap(True)
        evidence_layout.addWidget(self.noise_test_status)
        diagnostics.addWidget(evidence_card, 3, 0, 1, 2)

        diagnostics.setColumnStretch(0,1)
        diagnostics.setColumnStretch(1,1)
        layout.addLayout(diagnostics)
        self._refresh_controller_sources()
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
        self.osc_instrument_status = QLabel("No measurement instrument connected")
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
        self.interference_status = QLabel("No generator connected • OUTPUT OFF")
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
        self.generator_id=QLabel("Generator: No generator connected • OUTPUT OFF"); self.generator_id.setWordWrap(True); dl.addWidget(self.generator_id)
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
            self._report_error("Support bundle creation failed", exc)
            QMessageBox.critical(self, "Support bundle failed", str(exc))

    def _report_error(self, context: str, exc: Exception) -> None:
        message = f"{context}: {exc}"
        LOGGER.exception(context)
        support_log_event("ui_error", level="ERROR", context=context, error=str(exc))
        if hasattr(self, "error_banner"):
            self.error_banner.setText(message)
            self.error_banner.show()

    def _send_support_bundle(self) -> None:
        try:
            bundle = create_support_bundle()
            preview = support_bundle_preview(bundle)
        except Exception as exc:
            self._report_error("Support bundle creation failed", exc)
            QMessageBox.critical(self, "Support bundle failed", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "Send diagnostics to developer?",
            "Upload this redacted diagnostics bundle to the RCM Tool developer?\n\n"
            f"Local bundle: {bundle}\n"
            f"Session: {preview.get('session_id', SUPPORT_SESSION_ID)}\n"
            f"Scope: {preview.get('support_bundle_scope', 'redacted logs and health manifest only')}\n\n"
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
        self.support_upload_worker = SupportUploadWorker(bundle, self)
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
        self.expected_rate=QDoubleSpinBox(); self.expected_rate.setRange(1,100000); self.expected_rate.setDecimals(0); self.expected_rate.setSingleStep(125); self.expected_rate.setValue(1000); self.expected_rate.setSuffix(" Hz")
        self.expected_rate.setToolTip(
            "Optional reference only; supported range is 1 Hz to 100 kHz. "
            "Measured polling rate always comes from observed report timestamps."
        )
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

        dbcard, dbl=card("DATA")
        label=QLabel(f"SQLite database:\n{self.db.path}\n\nRaw samples are retained and can be exported from Reports.")
        label.setWordWrap(True); dbl.addWidget(label); layout.addWidget(dbcard); layout.addStretch(1)
        return self._scroll(w)

    def _clear_controller_state(self, identity: str) -> None:
        """Prevent samples from one acquisition mode being shown as another."""
        self.controller_ts.clear()
        self.controller_samples.clear()
        self.controller_sources.clear()
        self.controller_raw_report_hex.clear()
        self.controller_metadata = {}
        self.duplicate_raw_reports = 0
        self.noise_test_result = None
        self.noise_test_results.clear()
        if hasattr(self, "noise_test_status"):
            self.noise_test_status.setText("No attribution capture has been run.")
        while True:
            try:
                self.controller_queue.get_nowait()
            except queue.Empty:
                break
        self.controller_meta.setText(identity)
        self.controller_axes_readout.setText(
            "LX unavailable  •  LY unavailable  •  RX unavailable  •  RY unavailable  •  LT unavailable  •  RT unavailable"
        )
        self.axis_noise.setText("Analog noise unavailable until hardware samples arrive")
        self.button_capability.setText("No decoded hardware input sample")
        self.controller_capability.setText("No active physical controller/backend")

    def _start_controller_acquisition(self) -> None:
        if self.controller_acquisition:
            self.controller_acquisition.stop()
        identity = self.controller_source_info.get("product_string") or self.controller_source_kind
        self._clear_controller_state(str(identity))
        self.controller_acquisition = ControllerAcquisition(
            self.controller_queue.put,
            lambda name, payload: self.controller_event_queue.put((name, payload)),
            source_kind=self.controller_source_kind,
            hid_path=self.controller_source_path,
            hid_info=self.controller_source_info,
        )
        self.controller_acquisition.start()
        if self.controller_source_kind == "raw_hid":
            name = self.controller_source_info.get("product_string") or "selected device"
            self.controller_source_status.setText(
                f"Raw HID selected: {name}. Reports are timestamped when received by Windows."
            )
        else:
            self.controller_source_status.setText(
                "Automatic backend selection active: XInput → SDL → Raw HID → DirectInput."
            )

    def _refresh_controller_sources(self) -> None:
        if not hasattr(self, "controller_source_combo"):
            return
        selected_path = self.controller_source_path
        combo = self.controller_source_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Automatic — XInput → SDL → Raw HID → DirectInput", {"kind": "automatic"})
        raw_devices = ControllerAcquisition.enumerate_raw_hid_devices()
        for info in raw_devices:
            path = info.get("path")
            if not path:
                continue
            product = info.get("product_string") or "unnamed HID controller"
            vendor_id = int(info.get("vendor_id") or 0)
            product_id = int(info.get("product_id") or 0)
            combo.addItem(
                f"Raw HID — {product} (VID {vendor_id:04X}, PID {product_id:04X})",
                {"kind": "raw_hid", "path": path, "info": dict(info)},
            )
        if not raw_devices:
            combo.addItem(
                "Raw HID — no device detected (connect a controller and refresh)",
                {"kind": "raw_hid", "path": None, "info": {}},
            )
        combo.blockSignals(False)

        index = 0
        if selected_path is not None:
            for i in range(combo.count()):
                data = combo.itemData(i) or {}
                if data.get("kind") == "raw_hid" and data.get("path") == selected_path:
                    index = i
                    break
        combo.setCurrentIndex(index)
        self._controller_source_changed(index)
        if not raw_devices:
            backend_available, backend_status = ControllerAcquisition.raw_hid_backend_status()
            if backend_available:
                self.controller_source_status.setText(
                    "No Raw HID controller was found. Automatic mode is still available; connect the controller by USB and refresh."
                )
            else:
                self.controller_source_status.setText(backend_status)

    def _controller_source_changed(self, _index: int = 0) -> None:
        if not hasattr(self, "controller_source_combo"):
            return
        data = self.controller_source_combo.currentData() or {"kind": "automatic"}
        self.controller_source_kind = data.get("kind", "automatic")
        self.controller_source_path = data.get("path")
        self.controller_source_info = dict(data.get("info") or {})
        if self.controller_source_kind == "raw_hid":
            product = self.controller_source_info.get("product_string") or "selected device"
            self.controller_source_status.setText(
                f"Raw HID ready: {product}. Hardware acquisition is always active when the app is running."
            )
        elif self.controller_source_kind == "automatic":
            self.controller_source_status.setText(
                "Automatic backend selection. Hardware acquisition is always active when the app is running."
            )
        if not hasattr(self, "sample_timer"):
            return
        if self.capture_active:
            self.controller_source_status.setText("Stop Capture before changing the controller source.")
        else:
            self._start_controller_acquisition()

    def _toggle_capture(self) -> None:
        self._stop_capture() if self.capture_active else self._start_capture()

    def _start_noise_test(self, capture_kind: str) -> None:
        if self.controller_source_kind != "raw_hid" or not self.controller_source_path:
            QMessageBox.information(
                self,
                "Raw HID required",
                "Connect a controller, refresh devices, and select a named Raw HID device before running attribution evidence.",
            )
            return
        if self.noise_test_active:
            return
        self.smoothing_window.setValue(1)
        self.noise_test_active = True
        self.noise_test_kind = capture_kind
        self.noise_test_deadline = time.monotonic() + (10.0 if capture_kind == "neutral" else 20.0)
        self.noise_test_start_timestamp_ns = time.perf_counter_ns()
        if capture_kind == "neutral":
            instruction = "Leave every stick untouched for 10 seconds."
        else:
            instruction = "Use one stick: center → full deflection → center, then repeat with a quick reversal."
        self.noise_test_status.setText(f"RUNNING Raw HID {capture_kind} capture • {instruction}")
        self._add_event("noise_attribution_started", {"capture_kind": capture_kind})

    def _run_noise_wizard(self) -> None:
        if self.controller_source_kind != "raw_hid" or not self.controller_source_path:
            QMessageBox.information(
                self,
                "Raw HID required",
                "Connect a controller, refresh devices, and select a named Raw HID device before running the guided test.",
            )
            return
        if self.noise_test_active or self.noise_wizard is not None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Raw HID smoothing evidence wizard")
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)
        stack = QStackedWidget()
        layout.addWidget(stack, 1)

        intro = QWidget()
        intro_layout = QVBoxLayout(intro)
        intro_title = QLabel("Step 1 of 4 • Confirm the physical test")
        intro_title.setObjectName("Eyebrow")
        intro_layout.addWidget(intro_title)
        intro_text = QLabel(
            "This wizard runs only against the selected Raw HID device. It does not simulate input, inject noise, "
            "or modify the controller.\n\n"
            f"Selected source: {self.controller_source_combo.currentText()}\n"
            "You will leave the sticks untouched for 10 seconds, then perform one repeatable movement for 20 seconds. "
            "The export contains the paired host timestamps, normalized samples, and Raw HID report bytes."
        )
        intro_text.setWordWrap(True)
        intro_layout.addWidget(intro_text)
        intro_layout.addStretch(1)

        neutral = QWidget()
        neutral_layout = QVBoxLayout(neutral)
        neutral_title = QLabel("Step 2 of 4 • Neutral noise capture")
        neutral_title.setObjectName("Eyebrow")
        neutral_layout.addWidget(neutral_title)
        neutral_text = QLabel("Keep both sticks centered and untouched. Start the 10-second capture when ready.")
        neutral_text.setWordWrap(True)
        neutral_layout.addWidget(neutral_text)
        neutral_status = QLabel("Not started")
        neutral_status.setObjectName("Muted")
        neutral_status.setWordWrap(True)
        neutral_layout.addWidget(neutral_status)
        neutral_start = QPushButton("Start neutral capture")
        neutral_layout.addWidget(neutral_start, alignment=Qt.AlignmentFlag.AlignLeft)
        neutral_layout.addStretch(1)

        movement = QWidget()
        movement_layout = QVBoxLayout(movement)
        movement_title = QLabel("Step 3 of 4 • Movement and settling capture")
        movement_title.setObjectName("Eyebrow")
        movement_layout.addWidget(movement_title)
        movement_text = QLabel(
            "Use one stick only: center → full deflection → center, then repeat with one quick reversal. "
            "Do not change the selected device or connection during the capture."
        )
        movement_text.setWordWrap(True)
        movement_layout.addWidget(movement_text)
        movement_status = QLabel("Complete the neutral capture first")
        movement_status.setObjectName("Muted")
        movement_status.setWordWrap(True)
        movement_layout.addWidget(movement_status)
        movement_start = QPushButton("Start movement capture")
        movement_start.setEnabled(False)
        movement_layout.addWidget(movement_start, alignment=Qt.AlignmentFlag.AlignLeft)
        movement_layout.addStretch(1)

        review = QWidget()
        review_layout = QVBoxLayout(review)
        review_title = QLabel("Step 4 of 4 • Review and export")
        review_title.setObjectName("Eyebrow")
        review_layout.addWidget(review_title)
        review_status = QLabel("Run both captures to produce the evidence package.")
        review_status.setWordWrap(True)
        review_layout.addWidget(review_status)
        limitation = QLabel(
            "Interpretation boundary: Raw HID is downstream of firmware and USB. This package can show a "
            "host-observed smoothing signature, but it cannot identify firmware as the cause without a synchronized "
            "oscilloscope or logic-analyzer trace upstream of the USB report."
        )
        limitation.setObjectName("Muted")
        limitation.setWordWrap(True)
        review_layout.addWidget(limitation)
        review_layout.addStretch(1)

        for page_widget in (intro, neutral, movement, review):
            stack.addWidget(page_widget)

        buttons = QDialogButtonBox()
        back = buttons.addButton("Back", QDialogButtonBox.ButtonRole.ActionRole)
        next_button = buttons.addButton("Next", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        state = {"neutral_done": False, "movement_done": False}
        self.noise_wizard = {
            "dialog": dialog,
            "state": state,
            "neutral_status": neutral_status,
            "movement_status": movement_status,
            "review_status": review_status,
            "movement_start": movement_start,
            "next": next_button,
        }

        def update_navigation() -> None:
            index = stack.currentIndex()
            back.setEnabled(index > 0)
            if index == 0:
                next_button.setText("Begin")
                next_button.setEnabled(True)
            elif index == 1:
                next_button.setText("Next")
                next_button.setEnabled(bool(state["neutral_done"]))
            elif index == 2:
                next_button.setText("Review")
                next_button.setEnabled(bool(state["movement_done"]))
            else:
                next_button.setText("Close")
                next_button.setEnabled(bool(state["neutral_done"] and state["movement_done"]))

        def start_neutral() -> None:
            if self.noise_test_active:
                return
            self._start_noise_test("neutral")
            neutral_start.setEnabled(False)
            next_button.setEnabled(False)
            neutral_status.setText("RUNNING • keep the sticks untouched for 10 seconds…")

        def start_movement() -> None:
            if self.noise_test_active:
                return
            self._start_noise_test("movement")
            movement_start.setEnabled(False)
            next_button.setEnabled(False)
            movement_status.setText("RUNNING • perform the instructed movement for 20 seconds…")

        def navigate_next() -> None:
            index = stack.currentIndex()
            if index < 3:
                stack.setCurrentIndex(index + 1)
                update_navigation()
            else:
                dialog.accept()

        def navigate_back() -> None:
            if stack.currentIndex() > 0 and not self.noise_test_active:
                stack.setCurrentIndex(stack.currentIndex() - 1)
                update_navigation()

        neutral_start.clicked.connect(start_neutral)
        movement_start.clicked.connect(start_movement)
        next_button.clicked.connect(navigate_next)
        back.clicked.connect(navigate_back)
        cancel.clicked.connect(dialog.reject)

        def close_wizard() -> None:
            if self.noise_test_active:
                self.noise_test_active = False
                self.noise_test_status.setText("Guided capture cancelled before completion.")
            self.noise_wizard = None

        dialog.finished.connect(lambda _result: close_wizard())
        update_navigation()
        dialog.exec()

    def _finish_noise_test(self) -> None:
        if not self.noise_test_active:
            return
        samples = list(self.controller_samples)
        timestamps = list(self.controller_ts)
        raw_reports = list(self.controller_raw_report_hex)
        window = [
            (timestamp, sample, raw_report)
            for timestamp, sample, raw_report in zip(timestamps, samples, raw_reports)
            if timestamp >= self.noise_test_start_timestamp_ns
        ]
        window_timestamps = [item[0] for item in window]
        window_samples = [item[1] for item in window]
        window_reports = [item[2] for item in window]
        self.noise_test_active = False
        if not window_samples:
            self.noise_test_result = None
            self.noise_test_status.setText(
                "No Raw HID samples arrived. Connect the selected controller, refresh devices, and run the test again."
            )
            return
        self.noise_test_result = analyze_noise_capture(
            timestamps_ns=window_timestamps,
            samples=window_samples,
            raw_report_hex=window_reports,
            capture_kind=self.noise_test_kind,
            source_label=self.controller_source_combo.currentText(),
            device_metadata=self.controller_metadata,
        )
        result = self.noise_test_result
        self.noise_test_results[self.noise_test_kind] = result
        self.noise_test_status.setText(
            f"Complete • {result['sample_count']} Raw HID samples • "
            f"{result['raw_hid_report_count']} reports • attribution remains undetermined without an electrical trace."
        )
        self._add_event("noise_attribution_completed", result)
        if self.noise_wizard is not None:
            state = self.noise_wizard["state"]
            state[f"{self.noise_test_kind}_done"] = True
            if self.noise_test_kind == "neutral":
                self.noise_wizard["neutral_status"].setText(
                    f"Complete • {result['sample_count']} samples • {result['raw_hid_report_count']} Raw HID reports."
                )
                self.noise_wizard["movement_start"].setEnabled(True)
            else:
                self.noise_wizard["movement_status"].setText(
                    f"Complete • {result['sample_count']} samples • {result['raw_hid_report_count']} Raw HID reports."
                )
            self.noise_wizard["next"].setEnabled(True)
            captures = self.noise_test_results
            self.noise_wizard["review_status"].setText(
                f"Evidence ready: {len(captures)} capture(s). Use Export evidence in Controller Lab to save the paired records."
            )

    def _export_noise_evidence(self) -> None:
        if not self.noise_test_result:
            QMessageBox.information(self, "Noise attribution", "Run a Raw HID neutral or movement capture first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Noise Attribution Evidence",
            str(self.data_root / "noise_attribution_evidence.json"),
            "JSON (*.json)",
        )
        if path:
            payload = {
                "evidence_class": "measured-host-observed-raw-hid",
                "captures": self.noise_test_results or {"latest": self.noise_test_result},
                "interpretation_boundary": (
                    "Raw HID is downstream of firmware and USB. Firmware attribution requires a synchronized "
                    "oscilloscope or logic-analyzer trace upstream of the USB report."
                ),
            }
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _start_capture(self) -> None:
        if self.capture_active:
            return
        mode="hardware"
        self.session_id=self.db.create_session(
            "RcmTool capture",mode,__version__,
            {
                "nominal_frequency_hz":self.nominal_freq.value(),
                "timing_reference_mode":self.timing_reference_mode.currentData(),
                "configured_reference_rate_hz":self.expected_rate.value(),
                "host_timer_resolution_ns":self.host_timer_resolution_ns,
                "stationary_excursion_threshold":self.stationary_excursion.value(),
                "late_factor":self.late_factor.value(),
                "oscillator_outlier_sigma":self.outlier_sigma.value(),
            },
        )
        self.capture_active=True
        self.capture_button.setText("Stop Recording")
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
        self.capture_button.setText("Record Session")

    def _sample_tick(self) -> None:
        while True:
            try:
                event_name,event_payload=self.controller_event_queue.get_nowait()
            except queue.Empty:
                break
            self._add_event(event_name,event_payload)
            if event_name == "controller_backend_error":
                self.controller_source_status.setText(
                    f"Controller backend error: {event_payload.get('message', 'unknown error')}"
                )
            elif event_name == "controller_connected":
                self.controller_source_status.setText(
                    f"Controller connected: {event_payload.get('source', 'hardware input')}"
                )
            elif event_name == "controller_disconnected":
                self.controller_source_status.setText(
                    "Controller disconnected or stopped reporting. Check the cable/mode, then refresh devices."
                )
        # Drain enough queued hardware reports for high-rate controllers so this
        # handoff queue does not become an artificial polling ceiling.
        for _ in range(10000):
            try:
                m=self.controller_queue.get_nowait()
            except queue.Empty:
                break
            self._accept_controller(m.timestamp_ns,m.sample,m.source,m.timing_quality,m.raw_report_hex,m.duplicate_raw_report,m.metadata)
        if self.noise_test_active and time.monotonic() >= self.noise_test_deadline:
            self._finish_noise_test()

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
        self.controller_raw_report_hex.append(raw_hex)
        if metadata and metadata != self.controller_metadata:
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
        current_page = NAV[self.stack.currentIndex()] if hasattr(self, "stack") else "Dashboard"

        reference_name="configured" if expected_override is not None else "measured median"
        osc_source="MEASURED" if self.measurement_instrument is not None else "UNAVAILABLE"
        controller_samples_available = t.sample_count > 0

        if current_page == "Dashboard":
            self.cards["rate"].set_value(
                f"{t.effective_rate_hz:,.2f} Hz" if controller_samples_available else "Unavailable",
                f"{t.sample_count:,} observed report timestamps" if controller_samples_available else "No controller reports received",
                source="MEASURED",
            )
            self.cards["interval"].set_value(
                f"{t.mean_interval_ms:.3f} ms" if controller_samples_available else "Unavailable",
                f"min {t.min_interval_ms:.3f} • max {t.max_interval_ms:.3f}" if controller_samples_available else "Requires controller reports",
                source="MEASURED",
            )
            self.cards["jitter"].set_value(
                f"{t.rms_deviation_ms:.3f} ms" if t.sample_count >= 2 else "Unavailable",
                f"RMS vs {reference_name} {reference_ms:.3f} ms • p2p {t.peak_to_peak_jitter_ms:.3f}" if t.sample_count >= 2 else "Requires at least two controller reports",
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
                str(t.late_reports) if controller_samples_available else "Unavailable",
                f"missing estimate {t.missing_reports_estimate} • raw duplicates {self.duplicate_raw_reports}" if controller_samples_available else "Requires controller reports",
                source="CALCULATED",
            )

        try:
            output=self.instrument.output_enabled()
        except Exception:
            output=False
        if current_page == "Dashboard":
            generator_available = bool(getattr(getattr(self.instrument, "capabilities", None), "generator_output", False))
            self.cards["stimulus"].set_value(
                ("ON" if output else "OFF") if generator_available else "Unavailable",
                f"{self.stim_freq.value():g} Hz • {self.stim_amp.value():g} Vpp" if generator_available else "No physical generator connected",
                source="STATE",
            )
        if current_page == "Interference Lab":
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

        if current_page == "Dashboard":
            self.dashboard_timing_chart.set_series(
                [("interval ms",intervals[-500:],"#6AA2FF")],
                x_values=interval_elapsed[-500:],
                x_label="Elapsed controller capture time (s)",
            )
            dashboard_samples = list(self.controller_samples)[-500:]
            dashboard_sample_ts = list(self.controller_ts)[-len(dashboard_samples):] if dashboard_samples else []
            dashboard_elapsed = self._elapsed_seconds(
                dashboard_sample_ts,
                dashboard_sample_ts[0] if dashboard_sample_ts else None,
            )
            self.dashboard_noise_chart.set_series(
                [
                    ("LX", [float(item.get("lx", 0.0)) for item in dashboard_samples], "#6AA2FF"),
                    ("LY", [float(item.get("ly", 0.0)) for item in dashboard_samples], "#6DE0B1"),
                ],
                x_values=dashboard_elapsed,
                x_label="Elapsed controller capture time (s)",
            )
            if self.noise_test_result:
                self.dashboard_noise_status.setText(
                    f"Latest {self.noise_test_result['capture_kind']} capture: "
                    f"{self.noise_test_result['sample_count']} samples • "
                    "host-observed only; firmware attribution requires an upstream electrical trace."
                )
            else:
                self.dashboard_noise_status.setText("No Raw HID smoothing evidence captured.")

        samples=list(self.controller_samples)[-800:] if current_page in {"Live Capture","Controller Lab"} else []
        sample_ts=list(self.controller_ts)[-len(samples):] if samples and current_page == "Live Capture" else []
        sample_elapsed=self._elapsed_seconds(sample_ts,sample_ts[0] if sample_ts else None)
        hist_x,hist_y=self._histogram_xy(intervals[-3000:],32) if current_page == "Live Capture" else ([],[])

        if current_page == "Live Capture" and not self.visualization_paused:
            smooth=max(1,self.smoothing_window.value()) if hasattr(self,"smoothing_window") else 1
            def smooth_fn(values):
                return self._moving_average(values, smooth)
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

        if current_page == "Oscillator Lab":
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

        if current_page == "Controller Lab" and samples:
            last=samples[-1]
            visual_source=self.controller_sources[-1][0] if self.controller_sources else ""
            detected_family=detect_controller_family(self.controller_metadata,visual_source)
            requested_skin=self.controller_skin_combo.currentData() if hasattr(self,"controller_skin_combo") else "auto"
            visual_skin=detected_family if requested_skin=="auto" else str(requested_skin)
            self.controller_view.set_state(
                last,visual_source,skin=visual_skin,mapping_family=detected_family
            )
            mode_text="Auto" if requested_skin=="auto" else "Manual"
            self.controller_skin_status.setText(
                f"{mode_text} view: {visual_skin.title()} • detected {detected_family.title()}"
            )
            self.controller_axes_readout.setText(
                f"LX {float(last.get('lx',0)):+.4f}  •  LY {float(last.get('ly',0)):+.4f}  •  "
                f"RX {float(last.get('rx',0)):+.4f}  •  RY {float(last.get('ry',0)):+.4f}  •  "
                f"LT {float(last.get('lt',0))*100:.1f}%  •  RT {float(last.get('rt',0))*100:.1f}%"
            )
            rolling=samples[-250:]
            stationary_noise,axis_spans=self._stationary_analog_noise(rolling,self.stationary_excursion.value())
            if stationary_noise is None:
                largest=max(axis_spans.items(),key=lambda item:item[1]) if axis_spans else ("—",0.0)
                self.axis_noise.setText(
                    f"Noise floor unavailable while controls are moving • max excursion {largest[0].upper()} {largest[1]:.5f} "
                    f"(stationary limit {self.stationary_excursion.value():.5f})"
                )
            else:
                self.axis_noise.setText(
                    f"Stationary analog noise RMS {stationary_noise:.6f} normalized units • "
                    f"all-axis excursion ≤ {self.stationary_excursion.value():.5f}"
                )
            self.axis_noise.setToolTip(METRIC_HELP["analog_noise"])

            input_parts=[]
            pressed=self.controller_view.pressed_names()
            if "buttons" in last:
                input_parts.append("Pressed: "+(", ".join(pressed) if pressed else "none"))
                input_parts.append(f"raw mask 0x{int(last.get('buttons',0)):04X}")
            else:
                input_parts.append("Buttons: unavailable from active decoded backend")
            if "dpad_x" in last or "dpad_y" in last:
                dx,dy=int(last.get("dpad_x",0)),int(last.get("dpad_y",0))
                input_parts.append(f"D-pad ({dx:+d}, {dy:+d})")
            elif "dpad_pov" in last:
                pov=int(last.get("dpad_pov",65535))
                input_parts.append("D-pad centered" if pov in (65535,4294967295) else f"D-pad {pov/100:.1f}°")
            elif detected_family=="xbox" and "buttons" in last:
                mask=int(last.get("buttons",0))
                dx=(1 if mask&0x0008 else 0)-(1 if mask&0x0004 else 0)
                dy=(1 if mask&0x0001 else 0)-(1 if mask&0x0002 else 0)
                input_parts.append(f"D-pad ({dx:+d}, {dy:+d})")
            else:
                input_parts.append("D-pad: unavailable/source-specific")
            self.button_capability.setText(" • ".join(input_parts))

        if current_page == "Controller Lab" and self.controller_sources:
            source,quality=self.controller_sources[-1]
            meta=self.controller_metadata
            name=meta.get("controller_name") or source
            detected=detect_controller_family(meta,source)
            identity=[str(name),f"Family: {detected.title()}",f"Source: {source}",f"Timing: {quality}"]
            vid,pid=meta.get("vid"),meta.get("pid")
            if vid is not None and pid is not None:
                identity.append(f"VID:PID {int(vid):04X}:{int(pid):04X}")
            self.controller_meta.setText("  •  ".join(identity))

            detail_parts=[
                f"Backend {meta.get('backend','Unavailable')}",
                f"Connection {meta.get('connection_method','Unavailable')}",
                f"Firmware {meta.get('firmware_release','Unavailable')}",
                f"Battery {meta.get('battery_status','Unavailable')}",
            ]
            if meta.get("hid_interface") is not None:
                detail_parts.append(f"HID interface {meta['hid_interface']}")
            if meta.get("usb_path"):
                detail_parts.append(f"USB path {meta['usb_path']}")
            self.controller_capability.setText("  •  ".join(detail_parts))

        if current_page == "Correlation":
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
        if current_page == "Dashboard":
            self.quality_label.setText(
                f"Controller samples {t.sample_count:,} • duration {t.duration_s:.3f} s • timing source {timing_source} • "
                f"host monotonic timer resolution {self.host_timer_resolution_ns:.0f} ns • reference {reference_name} • "
                f"effective rate {t.effective_rate_hz:.2f} Hz • consecutive identical raw HID payloads {self.duplicate_raw_reports}.\n"
                "Host-arrival timestamps include Windows/USB scheduling unless dedicated on-wire timing hardware supplies the timestamp."
            )
        if current_page == "Interference Lab":
            self.safety_label.setText(
                f"Configured safety limits • {self.safety_limits.max_frequency_hz:g} Hz • {self.safety_limits.max_amplitude_vpp:g} Vpp • "
                f"±{self.safety_limits.max_abs_offset_v:g} V • generator output defaults OFF."
            )

        if current_page == "Dashboard" and self.baseline_active:
            duration=self.baseline_seconds.value()
            remaining=max(0,self.baseline_deadline-time.monotonic())
            self.baseline_progress.setValue(int((1-remaining/max(1,duration))*1000))
            self.baseline_state.setText(f"Baseline capture running • {remaining:.1f}s remaining")


    def _flush_database_buffer(self) -> None:
        if self.db.pending_row_count:
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
                {"timestamp_ns":ts,"sample":sample,"source":source[0],"timing_quality":source[1],"raw_report_hex":raw_hex}
                for ts,sample,source,raw_hex in zip(
                    list(self.controller_ts)[-200:],
                    list(self.controller_samples)[-200:],
                    list(self.controller_sources)[-200:],
                    list(self.controller_raw_report_hex)[-200:],
                )
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
            self._report_error("Measurement instrument connection failed", exc)
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
            self._report_error("Oscillator instrument connection failed", exc)
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
            self.osc_instrument_status.setText("No measurement instrument connected")
        if hasattr(self,"cap_table"):
            self._refresh_capabilities()

    def _connect_generator_instrument(self) -> None:
        resource=self._selected_visa_resource()
        if not resource: return
        self._emergency_off()
        try:
            self.instrument.close()
        except Exception:
            LOGGER.exception("Failed to close the previous generator before reconnect")
        try:
            generator=VisaScpiGenerator(resource)
            identity=generator.identify()
            self.instrument=generator
            self.generator_id.setText("Generator: "+identity+" • OUTPUT OFF")
            self.interference_status.setText(identity+" • OUTPUT OFF")
            self._add_event("generator_connected",{"resource":resource,"identity":identity})
            self._refresh_capabilities()
        except Exception as exc:
            self.instrument=UnavailableInstrument()
            self.generator_id.setText("Generator: connection failed • no generator connected")
            self.interference_status.setText("No generator connected • OUTPUT OFF")
            self._report_error("Generator connection failed", exc)
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
        self.instrument=UnavailableInstrument()
        if hasattr(self,"generator_id"):
            self.generator_id.setText("Generator: No generator connected • OUTPUT OFF")
        if hasattr(self,"output_button"):
            self.output_button.setChecked(False)
            self.output_button.setText("Enable Output")
        if hasattr(self,"interference_status"):
            self.interference_status.setText("No generator connected • OUTPUT OFF")
        if hasattr(self,"cap_table"):
            self._refresh_capabilities()


    def _apply_stimulus_settings(self) -> bool:
        self._sync_safety_limits()
        try:
            if not getattr(getattr(self.instrument, "capabilities", None), "generator_output", False):
                raise RuntimeError("Connect a physical VISA/SCPI generator before configuring stimulus")
            self.instrument.set_safety_limits(self.safety_limits)
            self.safety_limits.validate(frequency_hz=self.stim_freq.value(),amplitude_vpp=self.stim_amp.value(),offset_v=self.stim_offset.value())
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
                LOGGER.warning("Generator readback unavailable after configuration", exc_info=True)
                reported={"output_enabled":self.instrument.output_enabled(),"frequency_hz":None,"amplitude_vpp":None,"offset_v":None,"waveform":None}
            self._add_event("stimulus_configured",{"requested":requested,"instrument_reported":reported})
            return True
        except Exception as exc:
            self._report_error("Stimulus settings rejected", exc)
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
            self.output_button.setChecked(False)
            self._report_error("Instrument output change failed", exc)
            QMessageBox.critical(self,"Instrument output",str(exc))

    def _emergency_off(self) -> None:
        self._stop_sweep(output_off=False)
        try:
            with self.instrument_lock: self.instrument.set_output(False)
        except Exception:
            LOGGER.exception("Emergency output-off command failed")
            support_log_event("emergency_output_off_failed", level="ERROR")
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
                LOGGER.exception("Failed to force output off when stopping sweep")
        if was:
            self._add_event("sweep_stopped",{"captured_points":len(self.sweep_results)})

    def _add_event(self,event_type:str,payload:dict) -> None:
        ts=time.perf_counter_ns(); self.events.append((ts,event_type,dict(payload)))
        if self.capture_active and self.session_id: self.db.add_event(self.session_id,ts,event_type,payload)
        try:
            support_log_event("signal_lab_event", event_type=event_type, details=payload)
        except Exception:
            LOGGER.exception("Failed to write signal-lab event to support telemetry")
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
        path,_=QFileDialog.getSaveFileName(self,"Engineering Report",str(self.data_root/"RcmTool_Report.html"),"HTML (*.html)")
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
            path,title="RcmTool Engineering Report",
            controller_metrics=asdict(self.current_timing),
            oscillator_metrics=asdict(self.current_osc),
            metadata={
                "session_id":self.session_id,
                "mode":"hardware",
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
        saved_skin=str(self.settings.value("controller_skin","auto"))
        skin_index=self.controller_skin_combo.findData(saved_skin)
        self.controller_skin_combo.setCurrentIndex(max(0,skin_index))
        self._sync_safety_limits()

    def _save_settings(self) -> None:
        self._sync_safety_limits()
        values={
            "theme":self.theme_combo.currentText(),"baseline_seconds":self.baseline_seconds.value(),
            "timing_reference_mode":self.timing_reference_mode.currentData(),
            "expected_rate":self.expected_rate.value(),"late_factor":self.late_factor.value(),"outlier_sigma":self.outlier_sigma.value(),
            "stationary_excursion":self.stationary_excursion.value(),"nominal_freq":self.nominal_freq.value(),
            "graph_refresh":self.graph_refresh.value(),"max_freq":self.max_freq.value(),
            "max_amp":self.max_amp.value(),"max_offset":self.max_offset.value(),
            "controller_skin":self.controller_skin_combo.currentData()
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

    def _controller_skin_changed(self, _index:int=0) -> None:
        if not hasattr(self,"controller_skin_combo"):
            return
        self.settings.setValue("controller_skin",self.controller_skin_combo.currentData())
        if hasattr(self,"stack") and NAV[self.stack.currentIndex()]=="Controller Lab":
            QTimer.singleShot(0,self._refresh_ui)

    def _reset_graphs(self) -> None:
        for ch in [
            self.dashboard_timing_chart,self.dashboard_osc_chart,self.dashboard_noise_chart,self.live_interval_chart,self.live_jitter_chart,
            self.live_hist_chart,self.live_latency_chart,self.live_analog_chart,self.live_osc_chart,self.live_osc_jitter_chart,
            self.osc_stability_chart,self.osc_period_chart,self.corr_stimulus_chart,self.corr_osc_chart,self.corr_gamepad_chart
        ]: ch.reset_view()

    def _first_run(self) -> None:
        dlg=WelcomeDialog(self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            self.settings.setValue("welcomed",True)

    def closeEvent(self,event:QCloseEvent) -> None:
        self._emergency_off()
        if self.controller_acquisition: self.controller_acquisition.stop()
        self._disconnect_instrument(quiet=True)
        try:
            support_log_event("gamepad_signal_lab_stop", version=__version__)
        except Exception:
            LOGGER.exception("Failed to write application shutdown event")
        if hasattr(self, "db_flush_timer"):
            self.db_flush_timer.stop()
        self.db.close(); event.accept()


def main() -> int:
    app=QApplication.instance() or QApplication([])
    app.setApplicationName("RcmTool")
    app.setOrganizationName("SensoredRooster")
    app.setStyle("Fusion")
    window=MainWindow()
    window.show()
    return app.exec()
