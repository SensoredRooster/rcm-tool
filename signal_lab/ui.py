"""Modern Qt desktop shell for RcmTool."""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, deque
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
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from . import __version__
from .analysis import oscillator_metrics, pearson_correlation, recent_window_timing_metrics, timing_metrics
from .controller import (
    ControllerAcquisition,
    ControllerMeasurement,
    detect_controller_family,
    detect_controller_layout,
)
from .instruments import SafetyLimits, UnavailableInstrument, VisaScpiGenerator, VisaScpiMeasurementInstrument, list_visa_resources
from .metric_catalog import CHART_HELP, METRIC_HELP
from .noise_attribution import analyze_noise_capture
from .oscillator import OscillatorAcquisition, OscillatorMeasurement
from .reporting import write_html_report, write_noise_evidence_report, write_trace_evidence_report
from .storage import LabDatabase
from .sweep import make_sweep
from .trace_capture import (
    SigrokCaptureConfig,
    analyze_sigrok_csv,
    check_sigrok_cli,
    run_sigrok_capture,
    scan_sigrok,
)
from .stick_cleaner_page import StickCleanerPage
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
    windows_gui_resource_counts,
)

LOGGER = logging.getLogger(__name__)

NAV = ["Dashboard", "Controller Lab", "Electrical Trace", "Reports", "Support", "Settings"]

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


class SigrokCaptureWorker(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, config: SigrokCaptureConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config

    def run(self) -> None:
        result = run_sigrok_capture(self.config)
        if result.get("ok"):
            self.completed.emit(result)
        else:
            self.failed.emit(str(result.get("output") or result.get("message") or "sigrok capture failed"))


class WelcomeDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Welcome to RcmTool")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        title = QLabel("GAMEPAD SIGNAL LAB")
        title.setObjectName("Title")
        layout.addWidget(title)
        copy = QLabel(
            "A hardware-only controller noise and jitter workstation.\n\n"
            "1  Connect the controller by USB\n"
            "2  Select its named Raw HID device\n"
            "3  Run the guided neutral and movement tests\n"
            "4  Export the measured evidence\n\n"
            "No simulated controller values are used. Windows HID identity alone cannot certify that a device is physical rather than virtual."
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
        self.controller_source_kind = "none"
        self.controller_source_path = None
        self.controller_source_info: dict = {}
        self.controller_connected = False
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
        self.refresh_once_timer = QTimer(self)
        self.refresh_once_timer.setSingleShot(True)
        self.refresh_once_timer.timeout.connect(self._refresh_ui)
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
        self.noise_test_smoothing_tau_seconds = 0.05
        self.noise_test_deadline = 0.0
        self.noise_test_start_timestamp_ns = 0
        self.noise_test_result: dict | None = None
        self.noise_test_results: dict[str, dict] = {}
        self.guided_test_buttons: list[QPushButton] = []
        self.noise_capture_timestamps: list[int] = []
        self.noise_capture_samples: list[dict] = []
        self.noise_capture_raw_reports: list[str | None] = []
        self.noise_wizard: dict | None = None
        self.trace_worker: SigrokCaptureWorker | None = None
        self.trace_capture_active = False
        self.trace_capture_start_timestamp_ns = 0
        self.trace_capture_result: dict | None = None
        self.trace_capture_configuration: dict = {}
        self.trace_scanned_drivers: list[str] = []
        self.trace_scanned_executable = ""
        self.trace_controller_timestamps: list[int] = []
        self.trace_controller_samples: list[dict] = []
        self.trace_controller_raw_reports: list[str | None] = []
        self._last_gui_resource_audit = 0.0
        self._last_gui_resource_count: int | None = None
        self._resource_startup_until = time.monotonic() + 30.0
        self._startup_gui_snapshot_logged = False
        self._gui_resource_limit_triggered = False

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
            button = QPushButton(name)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self._navigate(i))
            side.addWidget(button)
            self.nav_buttons[name] = button

        side.addStretch(1)
        self.hardware_status = QLabel("HARDWARE • select a named Raw HID device")
        self.hardware_status.setObjectName("Muted")
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
        for builder in (
            self._dashboard_page,
            self._controller_page,
            self._trace_page,
            self._reports_page,
            self._support_page,
            self._settings_page,
        ):
            self.stack.addWidget(builder())
        work_layout.addWidget(self.stack, 1)
        root_layout.addWidget(work, 1)
        self._navigate(0)
        self._sync_hardware_controls()

    def _navigate(self, index: int) -> None:
        index = max(0, min(index, self.stack.count() - 1))
        self.stack.setCurrentIndex(index)
        self.top_title.setText(NAV[index])
        for name, button in self.nav_buttons.items():
            button.setChecked(NAV[index] == name)
        if hasattr(self, "ui_timer"):
            self._schedule_ui_refresh()

    def _schedule_ui_refresh(self) -> None:
        """Coalesce deferred refreshes instead of creating one timer per click."""
        if hasattr(self, "refresh_once_timer"):
            self.refresh_once_timer.start(0)
        else:
            self._refresh_ui()

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
            "Start here: select your controller, move a stick, then run the guided test. Readings show what this PC received—not a firmware guarantee.",
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        self.cards = {}
        specs = [
            ("rate", "Recent report rate", "MEASURED"),
            ("interval", "Average time between reports", "MEASURED"),
            ("jitter", "Timing variation", "CALCULATED"),
            ("late", "Long gaps / repeats", "CALCULATED"),
        ]
        for i, (key, title, source) in enumerate(specs):
            c = MetricCard(title, help_text=METRIC_HELP[key], source=source)
            self.cards[key] = c
            grid.addWidget(c, i // 4, i % 4)
        layout.addLayout(grid)

        evidence, evidence_layout = card("NOISE & SMOOTHING TEST")
        evidence_help = QLabel(
            "The guided test checks stick noise while still, then compares movement before and after an offline smoother. "
            "It analyzes a copy only; controller input is never changed."
        )
        evidence_help.setWordWrap(True)
        evidence_help.setObjectName("Muted")
        evidence_layout.addWidget(evidence_help)
        evidence_actions = QHBoxLayout()
        guided = QPushButton("Start guided test")
        guided.clicked.connect(self._run_noise_wizard)
        self.guided_test_buttons.append(guided)
        open_controller = QPushButton("Open Controller Lab")
        open_controller.clicked.connect(lambda: self._navigate(NAV.index("Controller Lab")))
        export_evidence = QPushButton("Open results report")
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

        baseline, bl = card("SAVE A RAW SESSION")
        state_row = QHBoxLayout()
        self.baseline_state = QLabel("Record Session saves incoming controller reports and measurements on this PC for review or export.")
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
            "Report gaps over recent history",
            help_text=CHART_HELP["report_interval"],
            x_label="Elapsed controller capture time (s)",
        )
        self.dashboard_noise_chart = LineChart(
            "Stick position received by this PC",
            help_text=CHART_HELP["analog_stability"],
            x_label="Elapsed controller capture time (s)",
        )
        charts.addWidget(self.dashboard_timing_chart,0,0)
        charts.addWidget(self.dashboard_noise_chart,0,1)
        charts.setColumnStretch(0,1); charts.setColumnStretch(1,1)
        layout.addLayout(charts)

        q, ql = card("ABOUT THESE READINGS (DETAILS)")
        self.quality_label = QLabel("Connect a controller and move a stick to see recent readings.")
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
            "The picture follows the connected model when recognized. Stick dots use live readings; button names are shown only when their mapping is known.",
        )

        identity, il = card("CONNECTED CONTROLLER")
        identity_row = QHBoxLayout()
        self.controller_meta = QLabel("No live Raw HID report stream; select a named device to begin")
        self.controller_meta.setObjectName("Good")
        self.controller_meta.setWordWrap(True)
        identity_row.addWidget(self.controller_meta, 1)

        selector_box = QVBoxLayout()
        selector_label = QLabel("CONTROLLER SHAPE")
        selector_label.setObjectName("Eyebrow")
        self.controller_skin_combo = QComboBox()
        self.controller_skin_combo.addItem("Match connected controller", "auto")
        self.controller_skin_combo.addItem("Flydigi Vader 5 Pro", "vader5pro")
        self.controller_skin_combo.addItem("Xbox", "xbox")
        self.controller_skin_combo.addItem("DualSense", "dualsense")
        self.controller_skin_combo.addItem("Standard gamepad", "generic")
        self.controller_skin_combo.setMinimumWidth(170)
        self.controller_skin_combo.setToolTip(
            "Automatic mode matches a known controller name to its outline. Choosing a shape manually changes only the drawing; it does not guess how buttons are encoded."
        )
        self.controller_skin_combo.currentIndexChanged.connect(self._controller_skin_changed)
        self.controller_skin_status = QLabel("Waiting for a named controller")
        self.controller_skin_status.setObjectName("Muted")
        selector_box.addWidget(selector_label)
        selector_box.addWidget(self.controller_skin_combo)
        selector_box.addWidget(self.controller_skin_status)
        identity_row.addLayout(selector_box)
        il.addLayout(identity_row)
        layout.addWidget(identity)

        visual, vl = card("LIVE STICK POSITION")
        self.controller_view = ControllerView()
        vl.addWidget(self.controller_view)
        self.controller_axes_readout = QLabel("LX unavailable  •  LY unavailable  •  RX unavailable  •  RY unavailable  •  LT unavailable  •  RT unavailable")
        self.controller_axes_readout.setObjectName("Muted")
        self.controller_axes_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.controller_axes_readout.setWordWrap(True)
        vl.addWidget(self.controller_axes_readout)
        layout.addWidget(visual)

        diagnostics = QGridLayout()
        diagnostics.setHorizontalSpacing(14)
        diagnostics.setVerticalSpacing(14)

        input_card, input_layout = card("BUTTONS & D-PAD")
        self.button_capability = QLabel("Waiting for a decoded input sample")
        self.button_capability.setObjectName("Muted")
        self.button_capability.setWordWrap(True)
        input_layout.addWidget(self.button_capability)
        diagnostics.addWidget(input_card,0,0)

        signal_card, signal_layout = card("STICK SIGNAL QUALITY")
        self.axis_noise = QLabel("Stationary noise: waiting for samples")
        self.axis_noise.setObjectName("Muted")
        self.axis_noise.setWordWrap(True)
        signal_layout.addWidget(self.axis_noise)
        diagnostics.addWidget(signal_card,0,1)

        device_card, device_layout = card("BACKEND / DEVICE DETAILS")
        self.controller_capability = QLabel("No live Raw HID stream. Device details appear only when the selected interface provides them.")
        self.controller_capability.setObjectName("Muted")
        self.controller_capability.setWordWrap(True)
        device_layout.addWidget(self.controller_capability)
        diagnostics.addWidget(device_card,1,0,1,2)

        source_card, source_layout = card("INPUT SOURCE")
        source_row = QHBoxLayout()
        self.controller_source_combo = QComboBox()
        self.controller_source_combo.setMinimumWidth(420)
        self.controller_source_combo.setToolTip(
            "Only a specifically selected Raw HID device is measured. XInput polling is excluded; a virtual device that exposes a HID interface may still appear and must be independently verified."
        )
        self.controller_source_combo.currentIndexChanged.connect(self._controller_source_changed)
        refresh_sources = QPushButton("Refresh Raw HID")
        refresh_sources.clicked.connect(self._refresh_controller_sources)
        source_row.addWidget(self.controller_source_combo, 1)
        source_row.addWidget(refresh_sources)
        self.refresh_controller_button = refresh_sources
        source_layout.addLayout(source_row)
        self.controller_source_status = QLabel(
            "Choose the named controller above, then move a stick to confirm live readings. This tool reads controller reports; it does not change controller settings."
        )
        self.controller_source_status.setObjectName("Muted")
        self.controller_source_status.setWordWrap(True)
        source_layout.addWidget(self.controller_source_status)
        diagnostics.addWidget(source_card, 2, 0, 1, 2)

        evidence_card, evidence_layout = card("RAW HID NOISE + SMOOTHING TEST")
        evidence_help = QLabel(
            "Raw HID only: the first test measures stationary output noise; the second measures movement/settling behavior. "
            "Neither can prove firmware filtering without an oscilloscope or logic analyzer upstream of USB."
        )
        evidence_help.setObjectName("Muted")
        evidence_help.setWordWrap(True)
        evidence_layout.addWidget(evidence_help)
        smoothing_row = QHBoxLayout()
        smoothing_row.addWidget(QLabel("Offline smoother time constant"))
        self.noise_smoothing_tau_ms = QDoubleSpinBox()
        self.noise_smoothing_tau_ms.setRange(1.0, 200.0)
        self.noise_smoothing_tau_ms.setDecimals(1)
        self.noise_smoothing_tau_ms.setSingleStep(1.0)
        self.noise_smoothing_tau_ms.setValue(50.0)
        self.noise_smoothing_tau_ms.setSuffix(" ms")
        self.noise_smoothing_tau_ms.setToolTip(
            "Applies a first-order smoother to a duplicate of the captured Raw HID data for comparison only. "
            "It does not change the controller or game input."
        )
        smoothing_row.addWidget(self.noise_smoothing_tau_ms)
        smoothing_row.addStretch(1)
        evidence_layout.addLayout(smoothing_row)
        evidence_row = QHBoxLayout()
        guided_test = QPushButton("Guided Raw HID + smoothing test")
        guided_test.clicked.connect(self._run_noise_wizard)
        self.guided_test_buttons.append(guided_test)
        export_evidence = QPushButton("Export + open results report")
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

    def _stick_cleaner_page(self) -> QWidget:
        self.stick_cleaner = StickCleanerPage()
        return self._scroll(self.stick_cleaner)

    def _trace_page(self) -> QWidget:
        w, layout = page(
            "Electrical Trace",
            "Optional capture from a connected physical analyzer through sigrok/libsigrok. The built-in software demo is rejected and cannot create evidence.",
        )

        tool_card, tool_layout = card("OPEN-SOURCE TRACE TOOL")
        tool_form = QFormLayout()
        self.trace_executable = QLineEdit("sigrok-cli")
        self.trace_executable.setToolTip("sigrok-cli executable name or full path. Install sigrok/PulseView separately.")
        tool_form.addRow("sigrok-cli", self.trace_executable)
        tool_layout.addLayout(tool_form)
        tool_actions = QHBoxLayout()
        check_tool = QPushButton("Check sigrok")
        check_tool.clicked.connect(self._check_trace_tool)
        scan_tool = QPushButton("Scan devices")
        scan_tool.clicked.connect(self._scan_trace_devices)
        tool_actions.addWidget(check_tool)
        tool_actions.addWidget(scan_tool)
        tool_actions.addStretch(1)
        tool_layout.addLayout(tool_actions)
        self.trace_tool_status = QLabel("sigrok-cli not checked")
        self.trace_tool_status.setObjectName("Muted")
        self.trace_tool_status.setWordWrap(True)
        tool_layout.addWidget(self.trace_tool_status)
        self.trace_scan_output = QPlainTextEdit()
        self.trace_scan_output.setReadOnly(True)
        self.trace_scan_output.setMaximumHeight(150)
        self.trace_scan_output.setPlaceholderText("sigrok-cli --scan output will appear here.")
        tool_layout.addWidget(self.trace_scan_output)
        layout.addWidget(tool_card)

        config_card, config_layout = card("CAPTURE CONFIGURATION")
        config_form = QFormLayout()
        self.trace_driver = QLineEdit()
        self.trace_driver.setPlaceholderText("Real analyzer driver from Scan devices (software demo blocked)")
        self.trace_channels = QLineEdit()
        self.trace_channels.setPlaceholderText("Required: channel names reported for your connected analyzer")
        self.trace_samplerate = QLineEdit("1m")
        self.trace_samplerate.setToolTip(
            "Requested sigrok rate, not a guarantee. Choose at least 10× the highest electrical frequency of interest; "
            "the report records the timestamp-derived rate actually present in the CSV."
        )
        self.trace_duration = QDoubleSpinBox()
        self.trace_duration.setRange(1.0, 600.0)
        self.trace_duration.setDecimals(1)
        self.trace_duration.setValue(10.0)
        self.trace_duration.setSuffix(" s")
        self.trace_wait_trigger = QCheckBox("Wait for hardware trigger")
        self.trace_triggers = QLineEdit()
        self.trace_triggers.setPlaceholderText("Optional sigrok trigger expression, for example 0=r")
        config_form.addRow("Driver / connection", self.trace_driver)
        config_form.addRow("Channels", self.trace_channels)
        config_form.addRow("Sample rate", self.trace_samplerate)
        config_form.addRow("Duration", self.trace_duration)
        config_form.addRow("Trigger", self.trace_wait_trigger)
        config_form.addRow("Trigger expression", self.trace_triggers)
        config_layout.addLayout(config_form)
        sync_help = QLabel(
            "Recommended procedure: select Raw HID first, connect the probe to the upstream sensor node, choose the "
            "actual driver/channel from Scan devices, and use a physical trigger or marker shared by the trace and "
            "the movement. If no hardware trigger is configured, the report is labeled host-start-aligned and cannot "
            "prove electrical-to-USB causation. Requested sample rate is never treated as measured."
        )
        sync_help.setObjectName("Muted")
        sync_help.setWordWrap(True)
        config_layout.addWidget(sync_help)
        capture_actions = QHBoxLayout()
        start_trace = QPushButton("Start synchronized capture")
        start_trace.setObjectName("Primary")
        start_trace.clicked.connect(self._start_trace_capture)
        self.start_trace_button = start_trace
        export_trace = QPushButton("Export trace evidence")
        export_trace.clicked.connect(self._export_trace_evidence)
        capture_actions.addWidget(start_trace)
        capture_actions.addWidget(export_trace)
        capture_actions.addStretch(1)
        config_layout.addLayout(capture_actions)
        self.trace_capture_status = QLabel("No electrical trace captured.")
        self.trace_capture_status.setObjectName("Muted")
        self.trace_capture_status.setWordWrap(True)
        config_layout.addWidget(self.trace_capture_status)
        layout.addWidget(config_card)

        limits, limits_layout = card("PROBE / INTERPRETATION BOUNDARY")
        limits_label = QLabel(
            "Connect the oscilloscope probe to the stick sensor/potentiometer output, or connect the logic analyzer "
            "to the upstream sensor bus. Use a high-impedance or differential probe as appropriate. Never connect a "
            "scope ground to an unknown powered node. RcmTool reports instrument-measured trace metrics separately "
            "from host-observed Raw HID metrics; it will not label firmware filtering without a valid upstream trace."
        )
        limits_label.setWordWrap(True)
        limits_label.setObjectName("Muted")
        limits_layout.addWidget(limits_label)
        layout.addWidget(limits)
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
        self.support_upload_worker.finished.connect(self.support_upload_worker.deleteLater)
        self.support_upload_worker.start()

    def _support_upload_complete(self, result: dict) -> None:
        self.support_upload_worker = None
        self.send_support_button.setEnabled(True)
        self.support_status.setText(
            f"Diagnostics sent successfully • HTTP {result.get('status', '?')} • session {SUPPORT_SESSION_ID}"
        )
        self._add_event("support_diagnostics_sent", {"status": result.get("status")})
        QMessageBox.information(self, "Diagnostics sent", f"Upload completed successfully.\nHTTP status: {result.get('status')}")

    def _support_upload_failed(self, message: str) -> None:
        self.support_upload_worker = None
        self.send_support_button.setEnabled(True)
        self.support_status.setText("Upload failed. A local support bundle can still be created and shared manually.")
        self._add_event("support_diagnostics_failed", {"error": message})
        QMessageBox.critical(self, "Diagnostics upload failed", message)

    def _settings_page(self) -> QWidget:
        w, layout = page("Settings", "A few capture and display controls. Existing sessions and logs are kept.")
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
        self.stationary_excursion=QDoubleSpinBox(); self.stationary_excursion.setRange(0.0001,0.5000); self.stationary_excursion.setDecimals(4); self.stationary_excursion.setValue(0.0200)
        self.stationary_excursion.setToolTip("Maximum max−min excursion allowed on every normalized stick axis before the window is considered moving rather than stationary.")
        self.graph_refresh=QSpinBox(); self.graph_refresh.setRange(100,1000); self.graph_refresh.setValue(200); self.graph_refresh.setSuffix(" ms")
        self.graph_refresh.valueChanged.connect(lambda v: self.ui_timer.setInterval(v) if hasattr(self,"ui_timer") else None)
        form.addRow("Theme",self.theme_combo)
        form.addRow("Default baseline duration",self.baseline_seconds)
        form.addRow("Timing reference",self.timing_reference_mode)
        form.addRow("Configured reference rate",self.expected_rate)
        form.addRow("Late-report threshold",self.late_factor)
        form.addRow("Stationary stick max excursion",self.stationary_excursion)
        form.addRow("Graph refresh interval",self.graph_refresh)
        gl.addLayout(form)
        save=QPushButton("Save Settings"); save.clicked.connect(self._save_settings)
        gl.addWidget(save,alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(g)

        dbcard, dbl=card("DATA")
        label=QLabel(f"SQLite database:\n{self.db.path}\n\nRaw samples are retained and can be exported from Reports.")
        label.setWordWrap(True); dbl.addWidget(label); layout.addWidget(dbcard); layout.addStretch(1)
        return self._scroll(w)

    def _set_controller_visual(self, sample: dict | None = None, source: str = "") -> None:
        """Show a known or manually selected shell without fabricating live input."""
        if not hasattr(self, "controller_view"):
            return
        metadata = self.controller_metadata or self.controller_source_info
        visual_source = source or str(
            self.controller_source_info.get("product_string")
            or self.controller_source_info.get("controller_name")
            or ""
        )
        detected_family = detect_controller_family(metadata, visual_source)
        detected_layout = detect_controller_layout(metadata, visual_source)
        requested_skin = (
            self.controller_skin_combo.currentData()
            if hasattr(self, "controller_skin_combo") else "auto"
        )
        visual_skin = detected_layout if requested_skin == "auto" else str(requested_skin)
        self.controller_view.set_state(
            sample or {}, visual_source, skin=visual_skin, mapping_family=detected_family
        )
        if hasattr(self, "controller_skin_status"):
            layout_names = {
                "vader5pro": "Flydigi Vader 5 Pro",
                "dualsense": "PlayStation DualSense",
                "xbox": "Xbox-style controller",
                "generic": "standard gamepad",
            }
            mode_text = "Auto match" if requested_skin == "auto" else "Manual shape"
            mapping_text = (
                "button map unverified" if detected_family == "generic"
                else "button map available"
            )
            if sample:
                status = f"{mode_text}: {layout_names[visual_skin]} • {mapping_text}"
            elif visual_source:
                status = f"{layout_names[visual_skin]} outline • waiting for live input"
            elif requested_skin != "auto":
                status = f"{layout_names[visual_skin]} outline • connect a controller for live input"
            else:
                status = "Waiting for a named controller • standard outline shown"
            self.controller_skin_status.setText(status)

    def _clear_controller_state(self, identity: str) -> None:
        """Prevent samples from one acquisition mode being shown as another."""
        self.controller_connected = False
        self.controller_ts.clear()
        self.controller_samples.clear()
        self.controller_sources.clear()
        self.controller_raw_report_hex.clear()
        self.controller_metadata = {}
        self.duplicate_raw_reports = 0
        self.noise_capture_timestamps.clear()
        self.noise_capture_samples.clear()
        self.noise_capture_raw_reports.clear()
        self.trace_controller_timestamps.clear()
        self.trace_controller_samples.clear()
        self.trace_controller_raw_reports.clear()
        if hasattr(self, "noise_test_status"):
            if self.noise_test_results:
                self.noise_test_status.setText(
                    f"{len(self.noise_test_results)} completed evidence capture(s) retained; each includes its own device metadata."
                )
            else:
                self.noise_test_status.setText("No attribution capture has been run.")
        while True:
            try:
                self.controller_queue.get_nowait()
            except queue.Empty:
                break
        self.controller_meta.setText(identity)
        self._set_controller_visual()
        self.controller_axes_readout.setText(
            "LX unavailable  •  LY unavailable  •  RX unavailable  •  RY unavailable  •  LT unavailable  •  RT unavailable"
        )
        self.axis_noise.setText("Analog noise unavailable until hardware samples arrive")
        self.button_capability.setText("No decoded hardware input sample")
        self.controller_capability.setText("No live Raw HID stream selected; physical authenticity cannot be certified from HID identity alone.")
        self._sync_hardware_controls()

    def _start_controller_acquisition(self) -> None:
        if self.controller_acquisition:
            self.controller_acquisition.stop()
            self.controller_acquisition = None
        if self._gui_resource_limit_triggered:
            self.controller_source_status.setText(
                "Hardware reader blocked by the Windows UI-resource safety stop. Close and reopen RcmTool before testing."
            )
            return
        if self.controller_source_kind != "raw_hid" or not self.controller_source_path:
            self._clear_controller_state("No named Raw HID device selected")
            self.controller_source_status.setText(
                "Waiting for a named Raw HID device. Connect by USB, select its entry, and confirm reports arrive. HID descriptors alone cannot certify physical hardware."
            )
            self.hardware_status.setText("HARDWARE • waiting for named Raw HID selection")
            self.hardware_status.setObjectName("Muted")
            return
        identity = self.controller_source_info.get("product_string") or "Selected Raw HID device"
        self._clear_controller_state(str(identity))
        self.controller_acquisition = ControllerAcquisition(
            self.controller_queue.put,
            lambda name, payload: self.controller_event_queue.put((name, payload)),
            source_kind=self.controller_source_kind,
            hid_path=self.controller_source_path,
            hid_info=self.controller_source_info,
        )
        self.controller_acquisition.start()
        name = self.controller_source_info.get("product_string") or "selected device"
        self.controller_source_status.setText(
            f"Selected Raw HID entry: {name}. Waiting for reports. Windows cannot certify whether this HID interface is physical or virtual."
        )
        self.hardware_status.setText(f"HARDWARE • waiting for Raw HID reports from {name}")
        self.hardware_status.setObjectName("Good")

    def _refresh_controller_sources(self) -> None:
        if not hasattr(self, "controller_source_combo"):
            return
        selected_path = self.controller_source_path
        combo = self.controller_source_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Select a named Raw HID device…", {"kind": "none"})
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
                "No Raw HID device detected — connect USB and refresh",
                {"kind": "none"},
            )
        index = 0
        if selected_path is not None:
            for i in range(combo.count()):
                data = combo.itemData(i) or {}
                if data.get("kind") == "raw_hid" and data.get("path") == selected_path:
                    index = i
                    break
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        selected_data = combo.itemData(index) or {}
        if selected_path and selected_data.get("path") == selected_path:
            self._start_controller_acquisition()
        else:
            self._controller_source_changed(index)
        if not raw_devices:
            backend_available, backend_status = ControllerAcquisition.raw_hid_backend_status()
            if backend_available:
                self.controller_source_status.setText(
                    "No Raw HID device was found. Connect the controller directly by USB, then refresh. XInput-only and virtual gamepad states are excluded; a virtual HID device may still appear."
                )
            else:
                self.controller_source_status.setText(backend_status)

    def _diagnose_controller_backends(self) -> None:
        diagnostics = ControllerAcquisition.backend_diagnostics()
        lines = []
        for item in diagnostics:
            marker = "FOUND" if item.get("detected") else "not found"
            lines.append(f"{item.get('backend', 'backend')}: {marker} — {item.get('status', '')}")
            for device in item.get("devices", []):
                lines.append(
                    f"  {device.get('manufacturer') or ''} {device.get('product') or 'unnamed HID'} "
                    f"VID {int(device.get('vendor_id') or 0):04X} PID {int(device.get('product_id') or 0):04X}"
                )
        summary = "\n".join(lines) or "No backend results returned."
        self.controller_source_status.setText(summary.replace("\n", " • "))
        self._add_event("controller_backend_diagnostics", {"results": diagnostics})
        QMessageBox.information(self, "Controller backend diagnostics", summary)

    def _controller_source_changed(self, _index: int = 0) -> None:
        if not hasattr(self, "controller_source_combo"):
            return
        data = self.controller_source_combo.currentData() or {"kind": "none"}
        self.controller_source_kind = data.get("kind", "none")
        self.controller_source_path = data.get("path")
        self.controller_source_info = dict(data.get("info") or {})
        if not hasattr(self, "sample_timer"):
            return
        self._start_controller_acquisition()

    def _has_measured_raw_hid(self) -> bool:
        return bool(
            self.controller_source_kind == "raw_hid"
            and self.controller_source_path
            and self.controller_connected
            and self.controller_metadata.get("evidence_class") == "measured-host-observed-raw-hid"
            and self.controller_ts
        )

    def _sync_hardware_controls(self) -> None:
        ready = self._has_measured_raw_hid() and not self._gui_resource_limit_triggered
        source_enabled = (
            not self.capture_active
            and not self.noise_test_active
            and not self.trace_capture_active
            and not self._gui_resource_limit_triggered
        )
        for control_name in ("controller_source_combo", "refresh_controller_button"):
            control = getattr(self, control_name, None)
            if control is not None and control.isEnabled() != source_enabled:
                control.setEnabled(source_enabled)
        if hasattr(self, "capture_button"):
            enabled = self.capture_active or ready
            if self.capture_button.isEnabled() != enabled:
                self.capture_button.setEnabled(enabled)
        for button in getattr(self, "guided_test_buttons", []):
            enabled = ready and not self.noise_test_active
            if button.isEnabled() != enabled:
                button.setEnabled(enabled)
        if hasattr(self, "noise_smoothing_tau_ms"):
            self.noise_smoothing_tau_ms.setEnabled(not self.noise_test_active)
        if hasattr(self, "start_trace_button"):
            enabled = ready and not self.trace_capture_active
            if self.start_trace_button.isEnabled() != enabled:
                self.start_trace_button.setEnabled(enabled)

    def _toggle_capture(self) -> None:
        self._stop_capture() if self.capture_active else self._start_capture()

    def _start_noise_test(self, capture_kind: str) -> None:
        if not self._has_measured_raw_hid():
            QMessageBox.information(
                self,
                "Live Raw HID reports required",
                "Select a named Raw HID device in Controller Lab and wait until live reports appear before starting this test.",
            )
            return
        if self.noise_test_active:
            return
        self.noise_test_active = True
        self._sync_hardware_controls()
        self.noise_test_kind = capture_kind
        self.noise_test_smoothing_tau_seconds = self.noise_smoothing_tau_ms.value() / 1000.0
        self.noise_test_deadline = time.monotonic() + (10.0 if capture_kind == "neutral" else 20.0)
        self.noise_test_start_timestamp_ns = time.perf_counter_ns()
        self.noise_capture_timestamps.clear()
        self.noise_capture_samples.clear()
        self.noise_capture_raw_reports.clear()
        if capture_kind == "neutral":
            instruction = "Leave every stick untouched for 10 seconds."
        else:
            instruction = "Use one stick: center → full deflection → center, then repeat with a quick reversal."
        self.noise_test_status.setText(f"RUNNING Raw HID {capture_kind} capture • {instruction}")
        self._add_event(
            "noise_attribution_started",
            {
                "capture_kind": capture_kind,
                "offline_smoothing_tau_seconds": self.noise_test_smoothing_tau_seconds,
            },
        )

    def _run_noise_wizard(self) -> None:
        if not self._has_measured_raw_hid():
            QMessageBox.information(
                self,
                "Live Raw HID reports required",
                "In Controller Lab, refresh devices, select the named HID entry, and wait for live reports before opening the test wizard. Verify the device identity independently.",
            )
            return
        if self.noise_test_active or self.noise_wizard is not None:
            return

        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setWindowTitle("Raw HID smoothing evidence wizard")
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)
        stack = QStackedWidget()
        layout.addWidget(stack, 1)

        intro = QWidget()
        intro_layout = QVBoxLayout(intro)
        intro_title = QLabel("Step 1 of 4 • Confirm the hardware setup")
        intro_title.setObjectName("Eyebrow")
        intro_layout.addWidget(intro_title)
        intro_text = QLabel(
            "This wizard runs only against the selected Raw HID device. It does not simulate input, inject noise, "
            "or modify the controller.\n\n"
            f"Selected source: {self.controller_source_combo.currentText()}\n"
            "Before starting: verify the selected VID/PID and product against your controller; HID descriptors cannot rule out a virtual device. Keep one controller connected, do not change USB ports or input modes, and "
            "close other tools that read the same controller.\n\n"
            "Step 2 leaves the sticks untouched for 10 seconds. Step 3 uses one stick: slowly center → full deflection "
            "→ center, then one quick reversal, for 20 seconds. The export contains every dedicated-test timestamp, "
            "normalized sample, and Raw HID report byte captured during each step. If no session is already recording, "
            "the wizard starts and saves one automatically in the local database. Step 4 compares the untouched raw "
            "measurements with a software-only smoother applied offline to the same samples at the selected time constant."
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
        review_export = QPushButton("Export + open results report")
        review_export.setEnabled(False)
        review_export.clicked.connect(self._export_noise_evidence)
        review_layout.addWidget(review_export, alignment=Qt.AlignmentFlag.AlignLeft)
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

        state = {"neutral_done": False, "movement_done": False, "results": {}}
        wizard_capture = {"started": False}
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
                review_export.setEnabled(bool(state["neutral_done"] and state["movement_done"]))

        def start_neutral() -> None:
            if self.noise_test_active:
                return
            if not self.capture_active:
                self._start_capture()
                wizard_capture["started"] = self.capture_active
            if not self.capture_active:
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
                self._sync_hardware_controls()
                self.noise_test_status.setText(
                    f"Capture interrupted after {len(self.noise_capture_samples):,} samples. Raw session data was retained; this partial phase is not scored."
                )
                self._add_event(
                    "noise_attribution_interrupted",
                    {"capture_kind": self.noise_test_kind, "sample_count": len(self.noise_capture_samples)},
                )
            self.noise_wizard = None
            if wizard_capture["started"] and self.capture_active:
                self._stop_capture()

        dialog.finished.connect(lambda _result: close_wizard())
        update_navigation()
        dialog.exec()

    def _finish_noise_test(self) -> None:
        if not self.noise_test_active:
            return
        window_timestamps = list(self.noise_capture_timestamps)
        window_samples = list(self.noise_capture_samples)
        window_reports = list(self.noise_capture_raw_reports)
        self.noise_test_active = False
        self._sync_hardware_controls()
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
            stationary_excursion=self.stationary_excursion.value(),
            reference_rate_hz=self._configured_reference_rate_hz(),
            smoothing_tau_seconds=self.noise_test_smoothing_tau_seconds,
        )
        result = self.noise_test_result
        device_name = result.get("device_metadata", {}).get("controller_name") or "HID"
        capture_key = f"{self.noise_test_kind} • {device_name} • {result.get('captured_utc', '')}"
        self.noise_test_results[capture_key] = result
        quality = result.get("capture_quality", {})
        stationary = result.get("stationary_check", {}).get("is_stationary", False)
        if self.noise_test_kind == "movement":
            stationarity_text = "movement protocol completed; stationary noise check does not apply"
        elif stationary is True:
            stationarity_text = "stationary check passed"
        elif stationary is False:
            stationarity_text = "movement exceeded stationary threshold"
        else:
            stationarity_text = "stationarity unavailable (below sample minimum)"
        tau_ms = result["smoothing_comparison"]["time_constant_ms"]
        if result["smoothing_comparison"]["basis"] == "stationary-noise-RMS":
            left_change = result["axes"]["lx"]["variation_change_percent"]
            up_change = result["axes"]["ly"]["variation_change_percent"]
            smoothing_text = (
                f"offline-filter RMS change at {tau_ms:.1f} ms: "
                f"LX {left_change:.1f}%, LY {up_change:.1f}%"
                if left_change is not None and up_change is not None
                else f"offline-filter comparison at {tau_ms:.1f} ms is unavailable"
            )
        elif self.noise_test_kind == "movement":
            smoothing_text = f"movement/filter tradeoff calculated at {tau_ms:.1f} ms; not a noise-only percentage"
        elif result["smoothing_comparison"]["basis"] == "variation-includes-unwanted-movement":
            smoothing_text = f"stationarity failed; filter change at {tau_ms:.1f} ms is not labeled noise reduction"
        else:
            smoothing_text = f"stationarity unavailable; filter change at {tau_ms:.1f} ms is not labeled noise reduction"
        self.noise_test_status.setText(
            f"Complete • {result['sample_count']} Raw HID samples • "
            f"{result['raw_hid_report_count']} reports • {result['sample_rate_hz']:.2f} reports/s • "
            f"{stationarity_text} • {smoothing_text} • integrity checks: {quality.get('label', 'review required')}."
        )
        self._add_event("noise_attribution_completed", result)
        if self.noise_wizard is not None:
            state = self.noise_wizard["state"]
            state[f"{self.noise_test_kind}_done"] = True
            state["results"][self.noise_test_kind] = result
            if self.noise_test_kind == "neutral":
                self.noise_wizard["neutral_status"].setText(
                    f"Complete • {result['sample_count']} samples • {result['sample_rate_hz']:.2f} reports/s • "
                    f"{'stationary check passed' if stationary else 'movement detected; review this capture'}."
                )
                self.noise_wizard["movement_start"].setEnabled(True)
            else:
                self.noise_wizard["movement_status"].setText(
                    f"Complete • {result['sample_count']} samples • {result['sample_rate_hz']:.2f} reports/s • "
                    f"integrity checks: {quality.get('label', 'review required')}."
                )
            self.noise_wizard["next"].setEnabled(True)
            neutral_result = state["results"].get("neutral")
            movement_result = state["results"].get("movement")
            review_lines = []
            if neutral_result:
                if neutral_result["smoothing_comparison"]["basis"] == "stationary-noise-RMS":
                    axis_notes = []
                    for axis in ("lx", "ly", "rx", "ry"):
                        metrics = neutral_result["axes"][axis]
                        raw = metrics["noise_rms"]
                        filtered = metrics["variation_rms_after_smoothing"]
                        change = metrics["variation_change_percent"]
                        if raw is not None and filtered is not None and change is not None:
                            axis_notes.append(
                                f"{axis.upper()} {raw:.6f} → {filtered:.6f} ({change:+.1f}%)"
                            )
                    review_lines.append("Neutral stationary noise RMS, raw → filtered: " + "; ".join(axis_notes))
                elif neutral_result["smoothing_comparison"]["basis"] == "variation-includes-unwanted-movement":
                    review_lines.append("Neutral capture was not stationary; do not interpret its filter change as noise reduction.")
                else:
                    review_lines.append("Neutral capture did not have enough samples to determine stationarity.")
            if movement_result:
                movement_axis = movement_result["axes"]["lx"]
                delta = movement_axis["smoothing_delta_rms"]
                if delta is not None:
                    review_lines.append(
                        f"Movement LX filter delta RMS: {delta:.6f} (includes intended motion and response lag)."
                    )
            captures = self.noise_test_results
            self.noise_wizard["review_status"].setText(
                "Evidence ready from the selected Raw HID device.\n"
                + "\n".join(review_lines)
                + f"\nThe {self.noise_test_smoothing_tau_seconds * 1000.0:.1f} ms setting is calculated offline; original reports are unchanged. "
                + f"{len(captures)} capture(s) are retained. Export opens the full explained report."
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
            report_path = Path(path).with_suffix(".html")
            write_noise_evidence_report(report_path, self.noise_test_result, captures=self.noise_test_results)
            webbrowser.open(report_path.resolve().as_uri())
            QMessageBox.information(
                self,
                "Evidence exported",
                f"JSON evidence saved to:\n{path}\n\nThe plain-language results report was opened:\n{report_path}",
            )

    def _check_trace_tool(self) -> None:
        result = check_sigrok_cli(self.trace_executable.text().strip() or "sigrok-cli")
        self.trace_tool_status.setText(
            ("AVAILABLE" if result.get("available") else "UNAVAILABLE")
            + f" • {result.get('message', '')}"
        )
        if not result.get("available"):
            self.trace_scan_output.setPlainText(
                "Electrical Trace setup\n"
                "1. Install PulseView/sigrok-cli for your analyzer or scope.\n"
                "2. Add the folder containing sigrok-cli.exe to PATH, or enter its full path above.\n"
                "3. Click Check sigrok, then Scan devices.\n"
                "4. Use the exact driver and channel names returned by Scan devices.\n\n"
                "RcmTool cannot invent an electrical trace when no instrument is connected."
            )

    def _scan_trace_devices(self) -> None:
        executable = self.trace_executable.text().strip() or "sigrok-cli"
        self.trace_scanned_drivers = []
        self.trace_scanned_executable = ""
        result = scan_sigrok(executable)
        if result.get("ok"):
            self.trace_scanned_drivers = [
                str(driver).casefold() for driver in result.get("available_drivers", [])
            ]
            self.trace_scanned_executable = str(result.get("executable", ""))
        prefix = "SCAN OK" if result.get("ok") else "SCAN FAILED"
        message = str(result.get("message", ""))
        if result.get("ok") and not self.trace_scanned_drivers:
            message += "; no non-demo analyzer was listed"
        self.trace_tool_status.setText(f"{prefix} • {message}")
        self.trace_scan_output.setPlainText(str(result.get("output", "")))

    def _trace_samplerate_hz(self) -> int:
        text = self.trace_samplerate.text().strip().lower().replace("hz", "")
        multiplier = 1
        if text.endswith("m"):
            multiplier = 1_000_000
            text = text[:-1]
        elif text.endswith("k"):
            multiplier = 1_000
            text = text[:-1]
        value = int(float(text) * multiplier)
        if value < 1 or value > 1_000_000_000:
            raise ValueError("Sample rate must be between 1 Hz and 1 GHz")
        return value

    def _start_trace_capture(self) -> None:
        if self.trace_capture_active or (self.trace_worker is not None and self.trace_worker.isRunning()):
            return
        if not self._has_measured_raw_hid():
            QMessageBox.information(
                self,
                "Live Raw HID reports required",
                "Select a named Raw HID device and confirm reports are arriving before starting a synchronized trace. Verify the device identity independently.",
            )
            return
        executable = self.trace_executable.text().strip() or "sigrok-cli"
        tool = check_sigrok_cli(executable)
        if not tool.get("available"):
            QMessageBox.information(self, "sigrok unavailable", str(tool.get("message", "sigrok-cli was not found")))
            return
        try:
            samplerate_hz = self._trace_samplerate_hz()
        except ValueError as exc:
            QMessageBox.information(self, "Invalid sample rate", str(exc))
            return
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        output_path = self.data_root / "electrical_traces" / f"{timestamp}_sigrok.csv"
        config = SigrokCaptureConfig(
            executable=str(tool["executable"]),
            driver=self.trace_driver.text(),
            samplerate_hz=samplerate_hz,
            duration_s=float(self.trace_duration.value()),
            output_path=output_path,
            channels=self.trace_channels.text(),
            triggers=self.trace_triggers.text(),
            wait_trigger=self.trace_wait_trigger.isChecked(),
        )
        try:
            command = config.command()
        except ValueError as exc:
            QMessageBox.information(self, "Real analyzer required", str(exc))
            return
        selected_driver = config.driver.split(":", 1)[0].strip().casefold()
        if self.trace_scanned_executable != str(tool["executable"]):
            QMessageBox.information(
                self,
                "Scan real analyzer first",
                "Click Scan devices with the selected sigrok-cli executable. Capture is enabled only for a non-demo driver actually listed by that scan.",
            )
            return
        if selected_driver not in self.trace_scanned_drivers:
            QMessageBox.information(
                self,
                "Analyzer not detected",
                "That driver was not listed by the latest device scan. Connect a supported physical analyzer, scan again, and use one of the listed drivers. The software demo is never accepted.",
            )
            return
        self.trace_capture_configuration = {
            "driver": config.driver.strip(),
            "channels": config.channels.strip(),
            "requested_sample_rate_hz": config.samplerate_hz,
            "driver_present_in_latest_sigrok_scan": True,
            "physical_device_identity_verified_by_application": False,
            "built_in_software_demo_allowed": False,
        }
        self.trace_capture_result = None
        self.trace_capture_active = True
        self._sync_hardware_controls()
        self.trace_capture_start_timestamp_ns = time.perf_counter_ns()
        self.trace_controller_timestamps.clear()
        self.trace_controller_samples.clear()
        self.trace_controller_raw_reports.clear()
        sync_method = "hardware-trigger-assisted" if config.wait_trigger and config.triggers.strip() else "host-start/finish-aligned"
        self.trace_capture_status.setText(
            f"RUNNING • {config.duration_s:g}s sigrok capture • synchronization: {sync_method}"
        )
        self._add_event(
            "electrical_trace_started",
            {
                "command": command,
                "synchronization_method": sync_method,
                "controller_source": self.controller_source_combo.currentText(),
            },
        )
        self.trace_worker = SigrokCaptureWorker(config, self)
        self.trace_worker.completed.connect(self._trace_capture_completed)
        self.trace_worker.failed.connect(self._trace_capture_failed)
        self.trace_worker.finished.connect(self.trace_worker.deleteLater)
        self.trace_worker.start()

    def _trace_capture_completed(self, process_result: dict) -> None:
        end_timestamp_ns = time.perf_counter_ns()
        self.trace_capture_active = False
        try:
            trace_result = analyze_sigrok_csv(Path(process_result["output_path"]))
            window = list(zip(
                self.trace_controller_timestamps,
                self.trace_controller_samples,
                self.trace_controller_raw_reports,
            ))
            sync_method = "hardware-trigger-assisted" if self.trace_wait_trigger.isChecked() and self.trace_triggers.text().strip() else "host-start/finish-aligned"
            trace_result["synchronization"] = {
                "method": sync_method,
                "controller_window_start_ns": self.trace_capture_start_timestamp_ns,
                "controller_window_end_ns": end_timestamp_ns,
                "limitation": (
                    "Host alignment does not establish electrical-to-USB causation. A hardware trigger is still "
                    "limited by the trigger wiring and instrument pretrigger configuration."
                ),
            }
            if window:
                hid_result = analyze_noise_capture(
                    timestamps_ns=[item[0] for item in window],
                    samples=[item[1] for item in window],
                    raw_report_hex=[item[2] for item in window],
                    capture_kind="electrical-trace-window",
                    source_label=self.controller_source_combo.currentText(),
                    device_metadata=self.controller_metadata,
                    stationary_excursion=self.stationary_excursion.value(),
                    reference_rate_hz=self._configured_reference_rate_hz(),
                )
                trace_result["controller_raw_hid"] = hid_result
            else:
                trace_result["controller_raw_hid"] = {"evidence_class": "no-raw-hid-window", "sample_count": 0}
            trace_result["attribution"] = "undetermined-until-electrical-and-raw-hid-signals-are-correlated"
            trace_result["capture_process"] = process_result
            trace_result["instrument_configuration"] = dict(self.trace_capture_configuration)
            self.trace_capture_result = trace_result
            self.trace_capture_status.setText(
                f"Complete • {trace_result['sample_count']} instrument samples • "
                f"{trace_result['controller_raw_hid'].get('sample_count', 0)} Raw HID samples • "
                f"instrument rate {trace_result.get('sample_rate_hz', 0.0):.3f} Hz • "
                f"{sync_method}. Export the evidence package for review."
            )
            self._add_event("electrical_trace_completed", trace_result)
        except Exception as exc:
            self.trace_capture_result = None
            self.trace_capture_status.setText(f"Trace was captured but could not be analyzed: {exc}")
        finally:
            self.trace_worker = None
            self._sync_hardware_controls()

    def _trace_capture_failed(self, message: str) -> None:
        self.trace_capture_active = False
        self.trace_capture_result = None
        self.trace_capture_status.setText(f"sigrok capture failed: {message}")
        self._add_event("electrical_trace_failed", {"message": message})
        self.trace_worker = None
        self._sync_hardware_controls()

    def _export_trace_evidence(self) -> None:
        if not self.trace_capture_result:
            QMessageBox.information(self, "Electrical trace", "Run a synchronized sigrok capture first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Electrical Trace Evidence",
            str(self.data_root / "electrical_trace_evidence.json"),
            "JSON (*.json)",
        )
        if path:
            Path(path).write_text(json.dumps(self.trace_capture_result, indent=2), encoding="utf-8")
            report_path = Path(path).with_suffix(".html")
            write_trace_evidence_report(report_path, self.trace_capture_result)
            webbrowser.open(report_path.resolve().as_uri())
            QMessageBox.information(
                self,
                "Trace evidence exported",
                f"JSON evidence saved to:\n{path}\n\nThe plain-language trace report was opened:\n{report_path}",
            )

    def _start_capture(self) -> None:
        if self.capture_active:
            return
        if not self._has_measured_raw_hid():
            QMessageBox.information(
                self,
                "Live Raw HID reports required",
                "Select a named Raw HID device and wait for live reports before recording. XInput/host polling is excluded; a virtual HID device still requires independent verification.",
            )
            return
        mode="hardware"
        self.session_id=self.db.create_session(
            "RcmTool capture",mode,__version__,
            {
                "timing_reference_mode":self.timing_reference_mode.currentData(),
                "configured_reference_rate_hz":self.expected_rate.value(),
                "host_timer_resolution_ns":self.host_timer_resolution_ns,
                "stationary_excursion_threshold":self.stationary_excursion.value(),
                "late_factor":self.late_factor.value(),
            },
        )
        self.capture_active=True
        self.capture_button.setText("Stop Recording")
        self._sync_hardware_controls()
        self._add_event("capture_started",{
            "mode":mode,
            "timing_reference_mode":self.timing_reference_mode.currentData(),
            "configured_reference_rate_hz":self.expected_rate.value(),
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
        if not self._gui_resource_limit_triggered:
            self.controller_source_combo.setEnabled(True)
            self.refresh_controller_button.setEnabled(True)
        self._sync_hardware_controls()

    def _audit_gui_resource_usage(self) -> None:
        """Record startup resource growth and stop capture before Windows exhausts GUI handles."""
        now = time.monotonic()
        in_startup_window = now < self._resource_startup_until
        audit_interval = 1.0 if in_startup_window else 5.0
        if now - self._last_gui_resource_audit < audit_interval:
            return
        self._last_gui_resource_audit = now
        counts = windows_gui_resource_counts()
        if not counts:
            return

        user_objects = counts["user_objects"]
        previous = self._last_gui_resource_count
        self._last_gui_resource_count = user_objects
        timers = self.findChildren(QTimer)
        widget_types = Counter(type(widget).__name__ for widget in self.findChildren(QWidget))
        if not self._startup_gui_snapshot_logged:
            support_log_event(
                "windows_gui_startup_snapshot",
                user_objects=user_objects,
                gdi_objects=counts["gdi_objects"],
                qt_widget_count=sum(widget_types.values()),
                qt_widget_types=dict(widget_types.most_common(12)),
                qt_timer_objects=len(timers),
                qt_timers_active=sum(timer.isActive() for timer in timers),
                top_level_widgets=len(QApplication.topLevelWidgets()),
                pages=list(NAV),
            )
            self._startup_gui_snapshot_logged = True
        if user_objects < 1000 and (previous is None or previous < 1000) and not in_startup_window:
            return

        support_log_event(
            "windows_gui_resource_snapshot",
            phase="startup" if in_startup_window else "runtime",
            user_objects=user_objects,
            user_object_delta=(user_objects - previous) if previous is not None else None,
            gdi_objects=counts["gdi_objects"],
            qt_widget_count=sum(widget_types.values()),
            qt_widget_types=dict(widget_types.most_common(12)),
            qt_timer_objects=len(timers),
            qt_timers_active=sum(timer.isActive() for timer in timers),
            top_level_widgets=len(QApplication.topLevelWidgets()),
            current_page=NAV[self.stack.currentIndex()] if hasattr(self, "stack") else "startup",
        )
        user_object_delta = (user_objects - previous) if previous is not None else 0
        rapid_growth = user_object_delta >= 1000
        resource_limit = user_objects >= 3500 or rapid_growth
        if resource_limit and not self._gui_resource_limit_triggered:
            self._gui_resource_limit_triggered = True
            was_capture_active = self.capture_active
            if self.controller_acquisition is not None:
                self.controller_acquisition.stop()
                self.controller_acquisition = None
            if self.noise_test_active:
                self.noise_test_active = False
                self.noise_test_status.setText(
                    "Stopped for Windows UI safety. This partial test is not scored; export only if clearly marked incomplete."
                )
                self._add_event(
                    "noise_attribution_interrupted",
                    {
                        "capture_kind": self.noise_test_kind,
                        "sample_count": len(self.noise_capture_samples),
                        "reason": "windows_gui_resource_safety_stop",
                    },
                )
            if self.noise_wizard is not None:
                self.noise_wizard["dialog"].reject()
            if self.capture_active:
                self._stop_capture()
            for timer_name in ("sample_timer", "ui_timer"):
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    timer.stop()
            if hasattr(self, "controller_source_combo"):
                self.controller_source_combo.setEnabled(False)
                self.refresh_controller_button.setEnabled(False)
            self._sync_hardware_controls()
            self.capture_button.setEnabled(False)
            self.hardware_status.setText("HARDWARE • stopped by UI-resource safety limit")
            support_log_event(
                "windows_gui_resource_safety_stop",
                user_objects=user_objects,
                user_object_delta=user_object_delta,
                rapid_growth=rapid_growth,
                stopped_capture=was_capture_active,
                page=NAV[self.stack.currentIndex()],
            )
            if hasattr(self, "error_banner"):
                self.error_banner.setText(
                    f"Safety stop at {user_objects:,} Windows UI objects"
                    + (f" ({user_object_delta:+,} since the last check). " if rapid_growth else ". ")
                    + "Controller reads and graph refresh are paused. Any active recording was finalized; existing database and support data are preserved. Close and reopen RcmTool before testing again."
                )
                self.error_banner.show()

    def _sample_tick(self) -> None:
        self._audit_gui_resource_usage()
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
                metadata = event_payload.get("metadata") or {}
                evidence_class = metadata.get("evidence_class")
                if evidence_class == "measured-host-observed-raw-hid":
                    self.controller_connected = False
                    message = "Raw HID interface opened; waiting for its first report."
                    self.hardware_status.setText("HARDWARE • waiting for first Raw HID report")
                    self.hardware_status.setObjectName("Muted")
                else:
                    self.controller_connected = False
                    message = "Non-Raw-HID source ignored; it cannot be used as Raw HID report evidence."
                self.controller_source_status.setText(
                    message + f" Source: {event_payload.get('source', 'hardware input')}"
                )
            elif event_name == "controller_disconnected":
                self.controller_connected = False
                self.controller_source_status.setText(
                    "Controller disconnected or stopped reporting. Check the cable/mode, then refresh devices."
                )
                self.hardware_status.setText("HARDWARE • selected Raw HID device stopped reporting")
                self.hardware_status.setObjectName("Warn")
                if self.noise_test_active:
                    sample_count = len(self.noise_capture_samples)
                    self.noise_test_active = False
                    self.noise_test_status.setText(
                        f"Interrupted by disconnect after {sample_count:,} samples. This partial phase is not scored; recorded session data is retained."
                    )
                    self._add_event(
                        "noise_attribution_interrupted",
                        {
                            "capture_kind": self.noise_test_kind,
                            "sample_count": sample_count,
                            "reason": "controller_disconnected",
                        },
                    )
                if self.noise_wizard is not None:
                    self.noise_wizard["dialog"].reject()
                if self.capture_active:
                    self._stop_capture()
                self.controller_view.set_state({}, "")
                last_device = self.controller_metadata.get("controller_name") or self.controller_source_info.get("product_string") or "Selected Raw HID device"
                self.controller_meta.setText(f"Last observed: {last_device} • disconnected; values below are not live")
                self.controller_axes_readout.setText(
                    "LX unavailable  •  LY unavailable  •  RX unavailable  •  RY unavailable  •  LT unavailable  •  RT unavailable"
                )
                self.axis_noise.setText(
                    "No live Raw HID stream. Previously captured samples remain available in the saved session."
                )
                self.button_capability.setText("No live decoded input; previous session data is retained.")
                self.controller_capability.setText("Selected Raw HID device stopped reporting; refresh or reconnect to resume.")
                self._sync_hardware_controls()
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
        metadata_changed = bool(metadata and metadata != self.controller_metadata)
        if metadata_changed:
            self.controller_metadata=dict(metadata)
        became_live = (
            self.controller_metadata.get("evidence_class") == "measured-host-observed-raw-hid"
            and not self.controller_connected
        )
        if self.controller_metadata.get("evidence_class") == "measured-host-observed-raw-hid":
            self.controller_connected = True
            if became_live:
                self.hardware_status.setText("HARDWARE • Raw HID reports received; identity unverified")
                self.hardware_status.setObjectName("Good")
        if (metadata_changed or became_live) and self.controller_connected:
            self._sync_hardware_controls()
        if duplicate_raw:
            self.duplicate_raw_reports += 1
        if self.baseline_active: self.baseline_controller_ts.append(int(timestamp_ns))
        if self.sweep_active and self.sweep_phase=="dwell":
            self.sweep_step_controller_ts.append(int(timestamp_ns))
            self.sweep_step_controller_samples.append(dict(sample))
        if self.capture_active and self.session_id:
            self.db.add_controller_sample(self.session_id,timestamp_ns,sample,source=f"{source} [{quality}]",raw_report_hex=raw_hex)
        if self.noise_test_active and timestamp_ns >= self.noise_test_start_timestamp_ns:
            self.noise_capture_timestamps.append(int(timestamp_ns))
            self.noise_capture_samples.append(dict(sample))
            self.noise_capture_raw_reports.append(raw_hex)
        if self.trace_capture_active and timestamp_ns >= self.trace_capture_start_timestamp_ns:
            self.trace_controller_timestamps.append(int(timestamp_ns))
            self.trace_controller_samples.append(dict(sample))
            self.trace_controller_raw_reports.append(raw_hex)

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
        recent_timing = recent_window_timing_metrics(
            timestamps,
            now_ns=time.perf_counter_ns(),
            window_s=1.0,
            stale_after_s=0.5,
            expected_interval_ms=expected_override,
            late_factor=self.late_factor.value(),
        )
        recent_intervals = [
            (after - before) / 1_000_000.0
            for before, after in zip(timestamps, timestamps[1:])
            if after > before and after >= (timestamps[-1] - 1_000_000_000 if timestamps else 0)
        ]
        recent_reference_ms = expected_override if expected_override is not None else self._median(recent_intervals)
        recent_report_count = recent_timing.sample_count
        recent_rate_available = recent_report_count >= 2
        raw_history = list(self.controller_raw_report_hex)[-len(timestamps):] if timestamps else []
        recent_payloads = [
            payload
            for timestamp_ns, payload in zip(timestamps, raw_history)
            if timestamps and timestamp_ns >= timestamps[-1] - 1_000_000_000
        ]
        recent_repeats = sum(
            1 for before, after in zip(recent_payloads, recent_payloads[1:])
            if before and after and before == after
        )

        nominal = 12_000_000.0  # Retained only for the legacy, non-visible report schema.
        osc_times: list[int] = []
        freqs: list[float] = []
        self.current_osc = oscillator_metrics([], nominal)
        self.current_corr = None
        t,o=self.current_timing,self.current_osc
        current_page = NAV[self.stack.currentIndex()] if hasattr(self, "stack") else "Dashboard"
        if current_page not in {"Dashboard", "Controller Lab"}:
            return

        osc_source="UNAVAILABLE"
        controller_samples_available = t.sample_count > 0
        evidence_class=str(self.controller_metadata.get("evidence_class") or "unavailable")
        rate_source="MEASURED" if evidence_class == "measured-host-observed-raw-hid" else "ESTIMATE"
        live_raw_hid = (
            self.controller_connected
            and evidence_class == "measured-host-observed-raw-hid"
            and controller_samples_available
        )

        if current_page == "Dashboard":
            if live_raw_hid and recent_rate_available:
                rate_text = f"{recent_timing.effective_rate_hz:,.1f} Hz"
                rate_note = f"Last 1 s • {recent_report_count:,} fresh reports received"
                interval_text = f"{recent_timing.mean_interval_ms:.3f} ms"
                interval_note = "Average gap in that same 1 s window"
                jitter_text = f"{recent_timing.rms_deviation_ms:.3f} ms"
                jitter_note = (
                    f"Variation around {recent_reference_ms:.3f} ms reference • "
                    f"range {recent_timing.peak_to_peak_jitter_ms:.3f} ms"
                )
                late_text = str(recent_timing.late_reports)
                late_note = (
                    f"estimated missing {recent_timing.missing_reports_estimate} • "
                    f"repeated payloads {recent_repeats} in last 1 s"
                )
            elif live_raw_hid:
                rate_text = "Waiting for fresh reports"
                rate_note = (
                    f"{recent_report_count} report in the last second; move a stick to measure its active rate"
                    if recent_report_count else "No report in the last 0.5 s; the controller may be idle"
                )
                interval_text = jitter_text = late_text = "Need fresh reports"
                interval_note = jitter_note = late_note = "Move a stick; these readings need at least two recent reports"
            else:
                rate_text = interval_text = jitter_text = late_text = "Unavailable"
                rate_note = "Select a named controller and move a stick"
                interval_note = jitter_note = late_note = "Requires fresh Raw HID reports"
            self.cards["rate"].set_value(
                rate_text,
                rate_note,
                source=rate_source,
            )
            self.cards["interval"].set_value(
                interval_text,
                interval_note,
                source=rate_source,
            )
            self.cards["jitter"].set_value(
                jitter_text,
                jitter_note,
                source="CALCULATED",
            )
            self.cards["late"].set_value(
                late_text,
                late_note,
                source="CALCULATED",
            )

        deviations=[value-reference_ms for value in intervals] if intervals else []
        ppm_values=[(f-nominal)/nominal*1e6 for f in freqs] if nominal>0 else []
        interval_elapsed=self._elapsed_seconds(interval_times,timestamps[0] if timestamps else None)
        osc_elapsed=self._elapsed_seconds(osc_times,osc_times[0] if osc_times else None)

        if current_page == "Dashboard":
            self.dashboard_timing_chart.set_series(
                [("interval ms",intervals[-500:],"#6AA2FF")] if live_raw_hid else [],
                x_values=interval_elapsed[-500:] if live_raw_hid else [],
                x_label="Elapsed controller capture time (s)",
            )
            dashboard_samples = list(self.controller_samples)[-500:] if live_raw_hid else []
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

        samples=list(self.controller_samples)[-800:] if current_page in {"Live Capture","Controller Lab","Stick Cleaner"} else []
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

        if current_page == "Stick Cleaner" and hasattr(self, "stick_cleaner"):
            self.stick_cleaner.update_from_lab(
                timestamps,
                list(self.controller_samples)[-800:],
                evidence_class=evidence_class,
                timing=t,
            )

        if current_page == "Controller Lab" and samples and self.controller_connected:
            last=samples[-1]
            visual_source=self.controller_sources[-1][0] if self.controller_sources else ""
            self._set_controller_visual(last, visual_source)
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
            elif detect_controller_family(self.controller_metadata, visual_source)=="xbox" and "buttons" in last:
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
            layout=detect_controller_layout(meta,source)
            layout_names = {
                "vader5pro": "Flydigi Vader 5 Pro",
                "dualsense": "PlayStation DualSense",
                "xbox": "Xbox-style",
                "generic": "standard gamepad",
            }
            identity=[str(name),f"Outline: {layout_names[layout]}",f"Input: {source}",f"Timing: {quality}"]
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
            corr_stimulus=[self.stim_freq.value() if self.instrument.output_enabled() else 0.0]*len(self.corr_time_axis)
            self.corr_osc_chart.set_series([("osc error ppm",corr_ppm,"#6DE0B1")],x_values=corr_elapsed,x_label="Elapsed correlated time (s)")
            self.corr_gamepad_chart.set_series([("report deviation ms",corr_game,"#6AA2FF")],x_values=corr_elapsed,x_label="Elapsed correlated time (s)")
            self.corr_stimulus_chart.set_series([("stimulus Hz",corr_stimulus,"#F0B862")],x_values=corr_elapsed,x_label="Elapsed correlated time (s)")

        if current_page == "Dashboard":
            history_note = (
                f"The retained history contains {t.sample_count:,} reports across {t.duration_s:.2f} s "
                f"(overall average {t.effective_rate_hz:.1f} reports/s)."
                if live_raw_hid else "No active controller stream; previous test data remains saved."
            )
            freshness_note = (
                f"The cards above use only fresh reports from the last second ({recent_report_count:,} reports)."
                if live_raw_hid and recent_rate_available
                else "The cards above need two fresh reports; move a stick if the controller is idle."
            )
            self.quality_label.setText(
                f"{'Connected' if live_raw_hid else 'Not connected'} • {history_note} {freshness_note} "
                "All report times are observed by this PC after USB; they are not internal firmware timestamps."
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

    def _configured_reference_rate_hz(self) -> float | None:
        if hasattr(self, "timing_reference_mode") and self.timing_reference_mode.currentData() == "configured":
            return float(self.expected_rate.value())
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
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
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
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
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
        path,_=QFileDialog.getSaveFileName(self,"Raw HID Session Report",str(self.data_root/"RcmTool_Report.html"),"HTML (*.html)")
        if not path: return
        timestamps=list(self.controller_ts)[-5000:]
        intervals=[(b-a)/1e6 for a,b in zip(timestamps,timestamps[1:]) if b>a]
        expected_override=self._timing_reference_ms(intervals)
        report_reference_ms=expected_override if expected_override is not None else self._median(intervals)
        deviations=[value-report_reference_ms for value in intervals]
        samples=list(self.controller_samples)[-1200:]
        timeline=self.db.list_events(self.session_id,limit=250)
        report_path = write_html_report(
            path,title="RcmTool Raw HID Session Report",
            controller_metrics=asdict(self.current_timing),
            oscillator_metrics=None,
            metadata={
                "session_id":self.session_id,
                "mode":"selected named Raw HID only",
                "app_version":__version__,
                "controller":self.controller_metadata,
                "evidence_class":self.controller_metadata.get("evidence_class", "unavailable"),
                "timing_reference_mode":self.timing_reference_mode.currentData(),
                "timing_reference_interval_ms":report_reference_ms,
                "configured_reference_rate_hz":self.expected_rate.value(),
                "host_timer_resolution_ns":self.host_timer_resolution_ns,
            },
            plots={
                "Raw HID report interval (ms)":intervals[-1200:],
                "Report interval deviation (ms)":deviations[-1200:],
                "Left stick X (normalized)": [float(item.get("lx", 0.0)) for item in samples],
                "Left stick Y (normalized)": [float(item.get("ly", 0.0)) for item in samples],
            },
            timeline=timeline,
            interpretation=(
                "Observed rate is the cadence of HID reports received by Windows after USB; it is not a guaranteed "
                "firmware polling rate. Timing jitter describes variation in those host arrival intervals, and Windows/USB "
                "scheduling can contribute. Repeated identical raw reports can simply mean the controls did not change; "
                "they are not proof of dropped input. Stick noise is measured downstream of the controller's sensor, ADC, "
                "and firmware, so this report cannot identify which stage introduced smoothing. A synchronized electrical "
                "trace upstream of the USB report is required for that attribution. No percent filtering score is inferred "
                "unless comparable paired measurements support it."
            ),
            limitations=[
                "Timestamps are host-arrival times after USB and include operating-system scheduling; they are not bus-level timestamps.",
                "A downstream controller report alone cannot distinguish sensor/electrical filtering from ADC, firmware, or host effects.",
                "For firmware attribution, use the Electrical Trace page with a physically synchronized upstream probe capture.",
            ]
        )
        webbrowser.open(report_path.resolve().as_uri())
        QMessageBox.information(self, "Report exported", f"The results report was saved and opened:\n{report_path}")

    def _apply_saved_settings(self) -> None:
        self.baseline_seconds.setValue(int(self.settings.value("baseline_seconds",60)))
        saved_reference=str(self.settings.value("timing_reference_mode","median"))
        reference_index=self.timing_reference_mode.findData(saved_reference)
        self.timing_reference_mode.setCurrentIndex(max(0,reference_index))
        self.expected_rate.setValue(float(self.settings.value("expected_rate",1000)))
        self.late_factor.setValue(float(self.settings.value("late_factor",1.5)))
        self.stationary_excursion.setValue(float(self.settings.value("stationary_excursion",0.02)))
        self.graph_refresh.setValue(int(self.settings.value("graph_refresh",200)))
        saved_skin=str(self.settings.value("controller_skin","auto"))
        skin_index=self.controller_skin_combo.findData(saved_skin)
        self.controller_skin_combo.setCurrentIndex(max(0,skin_index))

    def _save_settings(self) -> None:
        values={
            "theme":self.theme_combo.currentText(),"baseline_seconds":self.baseline_seconds.value(),
            "timing_reference_mode":self.timing_reference_mode.currentData(),
            "expected_rate":self.expected_rate.value(),"late_factor":self.late_factor.value(),
            "stationary_excursion":self.stationary_excursion.value(),
            "graph_refresh":self.graph_refresh.value(),
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
            if self.controller_connected and self.controller_samples:
                source = self.controller_sources[-1][0] if self.controller_sources else ""
                self._set_controller_visual(self.controller_samples[-1], source)
            else:
                self._set_controller_visual()

    def _reset_graphs(self) -> None:
        for name in ("dashboard_timing_chart", "dashboard_noise_chart"):
            chart = getattr(self, name, None)
            if chart is not None:
                chart.reset_view()

    def _first_run(self) -> None:
        dlg=WelcomeDialog(self)
        if dlg.exec()==QDialog.DialogCode.Accepted:
            self.settings.setValue("welcomed",True)

    def closeEvent(self,event:QCloseEvent) -> None:
        self._emergency_off()
        for timer_name in ("sweep_timer", "refresh_once_timer", "sample_timer", "ui_timer", "db_flush_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        if self.controller_acquisition: self.controller_acquisition.stop()
        self._disconnect_instrument(quiet=True)
        try:
            support_log_event("gamepad_signal_lab_stop", version=__version__)
        except Exception:
            LOGGER.exception("Failed to write application shutdown event")
        self.db.close(); event.accept()


def main() -> int:
    app=QApplication.instance() or QApplication([])
    app.setApplicationName("RcmTool")
    app.setOrganizationName("SensoredRooster")
    app.setStyle("Fusion")
    window=MainWindow()
    window.show()
    return app.exec()
