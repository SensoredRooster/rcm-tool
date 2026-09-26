"""Visual system for RcmTool."""
from __future__ import annotations

DARK_TOKENS = {
    "bg": "#080A0F",
    "sidebar": "#0B0E14",
    "topbar": "#0D1118",
    "card": "#111720",
    "metric": "#141C27",
    "chart_bg": "#0E141D",
    "chart_border": "#252F3D",
    "chart_text": "#B8C4D5",
    "chart_grid": "#1C2531",
    "chart_muted": "#7F8EA3",
    "stick_dot": "#69A7FF",
    "stick_ring": "#B2CEFF",
    "line_blue": "#6CA8FF",
    "line_green": "#69E2BA",
    "empty": "#8290A4",
}

LIGHT_TOKENS = {
    "bg": "#F5F7FB",
    "sidebar": "#FFFFFF",
    "topbar": "#FFFFFF",
    "card": "#FFFFFF",
    "metric": "#FFFFFF",
    "chart_bg": "#FAFBFD",
    "chart_border": "#DDE3EC",
    "chart_text": "#4D5C70",
    "chart_grid": "#E7EBF1",
    "chart_muted": "#718095",
    "stick_dot": "#3D72E8",
    "stick_ring": "#83A6EF",
    "line_blue": "#3F75E8",
    "line_green": "#178A67",
    "empty": "#6C7A8E",
}

PAINT: dict[str, str] = dict(DARK_TOKENS)


def set_paint_theme(name: str) -> None:
    PAINT.clear()
    PAINT.update(LIGHT_TOKENS if name == "Light" else DARK_TOKENS)


DARK = """
QWidget {
    background: #080A0F;
    color: #EEF3FA;
    font-family: "Segoe UI Variable", "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QDialog, QScrollArea, QAbstractScrollArea::viewport, QWidget#PageSurface {
    background: #080A0F;
}
QLabel, QCheckBox, QRadioButton { background: transparent; }

QFrame#Sidebar {
    background: #0B0E14;
    border-right: 1px solid #1C222D;
}
QFrame#Topbar {
    background: #0D1118;
    border-bottom: 1px solid #1C2430;
}
QFrame#Card, QFrame#SectionCard {
    background: #111720;
    border: 1px solid #242D39;
    border-radius: 22px;
}
QFrame#SectionCard:hover {
    border-color: #303C4D;
}
QFrame#MetricCard {
    background: #141C27;
    border: 1px solid #273342;
    border-radius: 20px;
}
QFrame#MetricCard:hover {
    background: #172130;
    border-color: #3B4B60;
}

QLabel#Brand {
    color: #FFFFFF;
    font-size: 20pt;
    font-weight: 800;
    letter-spacing: 0.3px;
}
QLabel#PageTitle {
    color: #F7F9FD;
    font-size: 20pt;
    font-weight: 760;
}
QLabel#PageSubtitle {
    color: #8997AA;
    font-size: 10pt;
}
QLabel#CardTitle {
    color: #F2F5FA;
    font-size: 11pt;
    font-weight: 720;
}
QLabel#Eyebrow {
    color: #70CBFF;
    font-size: 8pt;
    font-weight: 760;
    letter-spacing: 0.9px;
}
QLabel#Title {
    color: #F7F9FD;
    font-size: 23pt;
    font-weight: 760;
}
QLabel#Metric {
    color: #FAFCFF;
    font-size: 17pt;
    font-weight: 760;
}
QLabel#Muted { color: #8997AA; }
QLabel#Good { color: #6DE4B2; font-weight: 670; }
QLabel#Warn { color: #F6C96A; font-weight: 670; }
QLabel#LiveRate { color: #D6DFEC; font-weight: 650; }

QLabel#InfoBadge {
    min-width: 20px; max-width: 20px;
    min-height: 20px; max-height: 20px;
    border-radius: 10px;
    background: #1B2430;
    border: 1px solid #36465A;
    color: #AFCBEE;
    font-size: 8pt;
    font-weight: 800;
}
QLabel#SourceChip {
    padding: 3px 8px;
    border-radius: 8px;
    background: #182334;
    border: 1px solid #304560;
    color: #A8C8F3;
    font-size: 7pt;
    font-weight: 760;
}
QLabel#SectionHint {
    color: #758397;
    font-size: 9pt;
}
QLabel#StatusPill {
    padding: 7px 10px;
    border-radius: 10px;
    background: #101D18;
    border: 1px solid #244638;
    color: #7BE6B7;
    font-size: 8pt;
    font-weight: 700;
}
QLabel#StatusPillSecondary {
    padding: 7px 10px;
    border-radius: 10px;
    background: #121925;
    border: 1px solid #273446;
    color: #AAB9CC;
    font-size: 8pt;
    font-weight: 660;
}

QPushButton {
    min-height: 38px;
    padding: 5px 15px;
    border: 1px solid #2A3545;
    border-radius: 13px;
    background: #141B25;
    color: #EEF3FA;
    font-weight: 640;
}
QPushButton:hover {
    background: #192433;
    border-color: #465A74;
}
QPushButton:pressed { background: #0F151E; }
QPushButton:disabled {
    background: #10141B;
    border-color: #1C232D;
    color: #596575;
}
QPushButton:checked, QPushButton#Primary {
    background: #5C7CFA;
    border-color: #86A0FF;
    color: white;
}
QPushButton#Primary:hover {
    background: #6B88FF;
    border-color: #9AAFFF;
}
QPushButton#Danger {
    background: #4A2028;
    border-color: #8D3A49;
    color: #FFEFF2;
}
QPushButton#Danger:hover { background: #5C2732; }

QPushButton#Nav {
    min-height: 42px;
    text-align: left;
    padding-left: 15px;
    border: 1px solid transparent;
    border-radius: 13px;
    background: transparent;
    color: #8F9CAF;
}
QPushButton#Nav:hover {
    background: #111722;
    border-color: #1E2734;
    color: #F3F6FA;
}
QPushButton#Nav:checked {
    background: #171F2C;
    color: #FFFFFF;
    border-color: #2B3950;
    border-left: 3px solid #6D8BFF;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    min-height: 38px;
    background: #0D1219;
    border: 1px solid #293646;
    border-radius: 12px;
    padding: 4px 11px;
    selection-background-color: #5C7CFA;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #38495E;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #6E8BFF;
}
QComboBox::drop-down { border: 0; width: 30px; }

QProgressBar {
    min-height: 10px; max-height: 10px;
    border: 0;
    border-radius: 5px;
    background: #171F2B;
    text-align: center;
}
QProgressBar::chunk {
    background: #5C7CFA;
    border-radius: 5px;
}

QTableWidget {
    background: #0D1219;
    alternate-background-color: #111823;
    border: 1px solid #263240;
    border-radius: 16px;
    gridline-color: #1C2530;
}
QHeaderView::section {
    background: #151D28;
    color: #AAB7C9;
    border: 0;
    border-bottom: 1px solid #2A3646;
    padding: 10px;
    font-weight: 670;
}

QToolTip {
    background: #151C26;
    color: #F4F7FB;
    border: 1px solid #405168;
    border-radius: 8px;
    padding: 8px;
}

QScrollBar:vertical { width: 9px; background: transparent; }
QScrollBar::handle:vertical {
    background: #2B3748;
    min-height: 34px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 9px; background: transparent; }
QScrollBar::handle:horizontal {
    background: #2B3748;
    min-width: 34px;
    border-radius: 4px;
}
QCheckBox { spacing: 8px; }
"""

LIGHT = """
QWidget {
    background: #F5F7FB;
    color: #192334;
    font-family: "Segoe UI Variable", "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QDialog, QScrollArea, QAbstractScrollArea::viewport, QWidget#PageSurface { background: #F5F7FB; }
QLabel, QCheckBox, QRadioButton { background: transparent; }

QFrame#Sidebar { background: #FFFFFF; border-right: 1px solid #E1E5EC; }
QFrame#Topbar { background: #FFFFFF; border-bottom: 1px solid #E1E5EC; }
QFrame#Card, QFrame#SectionCard { background: #FFFFFF; border: 1px solid #DFE5ED; border-radius: 22px; }
QFrame#SectionCard:hover { border-color: #CBD5E1; }
QFrame#MetricCard { background: #FFFFFF; border: 1px solid #DCE3EC; border-radius: 20px; }
QFrame#MetricCard:hover { background: #FBFCFE; border-color: #C5D0DE; }

QLabel#Brand { color: #142033; font-size: 20pt; font-weight: 800; letter-spacing: 0.3px; }
QLabel#PageTitle { color: #172236; font-size: 20pt; font-weight: 760; }
QLabel#PageSubtitle { color: #708095; font-size: 10pt; }
QLabel#CardTitle { color: #1B273A; font-size: 11pt; font-weight: 720; }
QLabel#Eyebrow { color: #386FD8; font-size: 8pt; font-weight: 760; letter-spacing: 0.9px; }
QLabel#Title { color: #172236; font-size: 23pt; font-weight: 760; }
QLabel#Metric { color: #111B2B; font-size: 17pt; font-weight: 760; }
QLabel#Muted { color: #718095; }
QLabel#Good { color: #147653; font-weight: 670; }
QLabel#Warn { color: #A35C00; font-weight: 670; }
QLabel#LiveRate { color: #45566D; font-weight: 650; }

QLabel#InfoBadge {
    min-width:20px; max-width:20px; min-height:20px; max-height:20px;
    border-radius:10px; background:#EFF3F9; border:1px solid #D1DCE9;
    color:#456A9C; font-size:8pt; font-weight:800;
}
QLabel#SourceChip {
    padding:3px 8px; border-radius:8px; background:#F0F5FC;
    border:1px solid #CCD9EA; color:#3D6599; font-size:7pt; font-weight:760;
}
QLabel#SectionHint { color:#718095; font-size:9pt; }
QLabel#StatusPill {
    padding:7px 10px; border-radius:10px; background:#ECF8F2;
    border:1px solid #C9E9DA; color:#146845; font-size:8pt; font-weight:700;
}
QLabel#StatusPillSecondary {
    padding:7px 10px; border-radius:10px; background:#F1F4F8;
    border:1px solid #D9E1EB; color:#55667D; font-size:8pt; font-weight:660;
}

QPushButton {
    min-height:38px; padding:5px 15px; border:1px solid #CDD6E2;
    border-radius:13px; background:#FFFFFF; color:#192334; font-weight:640;
}
QPushButton:hover { background:#F5F8FC; border-color:#AFC0D4; }
QPushButton:pressed { background:#EBF0F6; }
QPushButton:disabled { background:#F2F4F7; border-color:#E0E5EB; color:#9BA6B4; }
QPushButton:checked, QPushButton#Primary { background:#4F6FEA; border-color:#4F6FEA; color:white; }
QPushButton#Primary:hover { background:#5B79EC; }
QPushButton#Danger { background:#B33C4A; border-color:#B33C4A; color:white; }
QPushButton#Danger:hover { background:#9D3441; }

QPushButton#Nav {
    min-height:42px; text-align:left; padding-left:15px; border:1px solid transparent;
    border-radius:13px; background:transparent; color:#66758A;
}
QPushButton#Nav:hover { background:#F5F7FA; border-color:#E4E9EF; color:#1C2A40; }
QPushButton#Nav:checked {
    background:#EDF2FF; color:#264A9E; border-color:#D6E0FB;
    border-left:3px solid #5578EE;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    min-height:38px; background:#FFFFFF; border:1px solid #CDD7E3;
    border-radius:12px; padding:4px 11px; selection-background-color:#4F6FEA;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color:#B6C4D5; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color:#6B87E8; }
QComboBox::drop-down { border:0; width:30px; }

QProgressBar { min-height:10px; max-height:10px; border:0; border-radius:5px; background:#E7EBF1; }
QProgressBar::chunk { background:#4F6FEA; border-radius:5px; }

QTableWidget {
    background:#FFFFFF; alternate-background-color:#FAFBFD;
    border:1px solid #DDE4EC; border-radius:16px; gridline-color:#E8ECF1;
}
QHeaderView::section {
    background:#F3F6F9; color:#59697F; border:0;
    border-bottom:1px solid #DEE5ED; padding:10px; font-weight:670;
}

QToolTip { background:#FFFFFF; color:#1B2738; border:1px solid #BAC8D8; border-radius:8px; padding:8px; }
QScrollBar:vertical { width:9px; background:transparent; }
QScrollBar::handle:vertical { background:#C5CFDB; min-height:34px; border-radius:4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { height:9px; background:transparent; }
QScrollBar::handle:horizontal { background:#C5CFDB; min-width:34px; border-radius:4px; }
QCheckBox { spacing:8px; }
"""
