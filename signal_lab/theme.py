"""Visual system for RcmTool."""
from __future__ import annotations

DARK_TOKENS = {
    "bg": "#070A10",
    "sidebar": "#0A0F18",
    "topbar": "#090E16",
    "card": "#0E1622",
    "metric": "#101A29",
    "chart_bg": "#0C141F",
    "chart_border": "#23334A",
    "chart_text": "#A9B8CB",
    "chart_grid": "#1C293B",
    "chart_muted": "#7E91AA",
    "stick_dot": "#5D93FF",
    "stick_ring": "#9EC0FF",
    "line_blue": "#6AA2FF",
    "line_green": "#6DE0B1",
    "empty": "#8FA1B8",
}

LIGHT_TOKENS = {
    "bg": "#F3F6FA",
    "sidebar": "#FFFFFF",
    "topbar": "#FFFFFF",
    "card": "#FFFFFF",
    "metric": "#FFFFFF",
    "chart_bg": "#F7FAFE",
    "chart_border": "#D5DFEB",
    "chart_text": "#536278",
    "chart_grid": "#E3E9F0",
    "chart_muted": "#708197",
    "stick_dot": "#2767D8",
    "stick_ring": "#5B86C5",
    "line_blue": "#2767D8",
    "line_green": "#08794D",
    "empty": "#66778D",
}

PAINT: dict[str, str] = dict(DARK_TOKENS)


def set_paint_theme(name: str) -> None:
    PAINT.clear()
    PAINT.update(LIGHT_TOKENS if name == "Light" else DARK_TOKENS)


DARK = """
QWidget {
    background: #070A10;
    color: #EAF0FA;
    font-family: "Segoe UI Variable", "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QDialog, QScrollArea, QAbstractScrollArea::viewport {
    background: #070A10;
}
QLabel, QCheckBox, QRadioButton { background: transparent; }
QFrame#Sidebar {
    background: #0A0F18;
    border-right: 1px solid #1B2637;
}
QFrame#Topbar {
    background: #090E16;
    border-bottom: 1px solid #1A2637;
}
QFrame#Card, QFrame#SectionCard {
    background: #0E1622;
    border: 1px solid #202E42;
    border-radius: 18px;
}
QFrame#MetricCard {
    background: #101A29;
    border: 1px solid #26364D;
    border-radius: 16px;
}
QFrame#MetricCard:hover {
    background: #122033;
    border-color: #3A5273;
}
QLabel#Brand {
    color: #F5F8FF;
    font-size: 18pt;
    font-weight: 800;
    letter-spacing: 0.2px;
}
QLabel#Eyebrow {
    color: #70D6FF;
    font-size: 8pt;
    font-weight: 750;
    letter-spacing: 0.8px;
}
QLabel#Title {
    color: #F5F8FF;
    font-size: 22pt;
    font-weight: 750;
}
QLabel#PageTitle {
    color: #F4F7FD;
    font-size: 19pt;
    font-weight: 720;
}
QLabel#Metric {
    color: #F8FAFF;
    font-size: 16pt;
    font-weight: 760;
}
QLabel#Muted { color: #8FA1B8; }
QLabel#Good { color: #69E4A8; font-weight: 650; }
QLabel#Warn { color: #F4C76A; font-weight: 650; }
QLabel#LiveRate {
    color: #C9D7EA;
    font-size: 10pt;
    font-weight: 650;
    padding-right: 8px;
}
QLabel#InfoBadge {
    min-width: 20px; max-width: 20px;
    min-height: 20px; max-height: 20px;
    border-radius: 10px;
    background: #18263A;
    border: 1px solid #31445F;
    color: #8EB9F4;
    font-size: 8pt;
    font-weight: 800;
}
QLabel#SourceChip {
    padding: 2px 7px;
    border-radius: 7px;
    background: #162238;
    border: 1px solid #2C4161;
    color: #9FC1F2;
    font-size: 7pt;
    font-weight: 750;
}
QLabel#SectionHint {
    color: #75889F;
    font-size: 9pt;
}
QPushButton {
    min-height: 36px;
    padding: 4px 14px;
    border: 1px solid #2B3C56;
    border-radius: 12px;
    background: #121D2D;
    color: #EDF3FD;
    font-weight: 620;
}
QPushButton:hover { background: #19283D; border-color: #466184; }
QPushButton:pressed { background: #0D1623; }
QPushButton:checked, QPushButton#Primary {
    background: #2E6AE8;
    border-color: #6D9BFF;
    color: white;
}
QPushButton#Danger {
    background: #4F1E28;
    border-color: #9A3B4A;
    color: #FFECEF;
}
QPushButton#Danger:hover { background: #662632; }
QPushButton#Nav {
    min-height: 38px;
    text-align: left;
    padding-left: 14px;
    border: 0;
    border-left: 3px solid transparent;
    border-radius: 11px;
    background: transparent;
    color: #93A5BC;
}
QPushButton#Nav:hover { background: #101A28; color: #FFFFFF; }
QPushButton#Nav:checked {
    background: #16243A;
    color: #FFFFFF;
    border-left: 3px solid #67A0FF;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    min-height: 36px;
    background: #0A111B;
    border: 1px solid #26374F;
    border-radius: 11px;
    padding: 3px 10px;
    selection-background-color: #2F6BFF;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #5B86C5;
}
QComboBox::drop-down { border: 0; width: 28px; }
QProgressBar {
    min-height: 10px; max-height: 10px;
    border: 0; border-radius: 5px;
    background: #182337;
    text-align: center;
}
QProgressBar::chunk { background: #4D83F5; border-radius: 5px; }
QTableWidget {
    background: #0B121D;
    alternate-background-color: #0F1927;
    border: 1px solid #223149;
    border-radius: 14px;
    gridline-color: #1C293C;
}
QHeaderView::section {
    background: #121D2C;
    color: #A6B6CA;
    border: 0;
    border-bottom: 1px solid #283850;
    padding: 9px;
    font-weight: 650;
}
QToolTip {
    background: #111B2A;
    color: #F5F8FF;
    border: 1px solid #415774;
    border-radius: 7px;
    padding: 8px;
}
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: #2C3D57; min-height: 32px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 9px; background: transparent; }
QScrollBar::handle:horizontal { background: #2C3D57; min-width: 32px; border-radius: 4px; }
QCheckBox { spacing: 8px; }
"""

LIGHT = """
QWidget {
    background: #F3F6FA;
    color: #182235;
    font-family: "Segoe UI Variable", "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QDialog, QScrollArea, QAbstractScrollArea::viewport { background: #F3F6FA; }
QLabel, QCheckBox, QRadioButton { background: transparent; }
QFrame#Sidebar { background: #FFFFFF; border-right: 1px solid #D8E0EA; }
QFrame#Topbar { background: #FFFFFF; border-bottom: 1px solid #D8E0EA; }
QFrame#Card, QFrame#SectionCard { background: #FFFFFF; border: 1px solid #D9E2ED; border-radius: 18px; }
QFrame#MetricCard { background: #FFFFFF; border: 1px solid #D5DFEB; border-radius: 16px; }
QFrame#MetricCard:hover { background: #F7FAFE; border-color: #ABC0DC; }
QLabel#Brand { color: #132039; font-size: 18pt; font-weight: 800; letter-spacing: 0.2px; }
QLabel#Eyebrow { color: #2469C5; font-size: 8pt; font-weight: 750; letter-spacing: 0.8px; }
QLabel#Title { color: #132039; font-size: 22pt; font-weight: 750; }
QLabel#PageTitle { color: #132039; font-size: 19pt; font-weight: 720; }
QLabel#Metric { color: #111B2C; font-size: 16pt; font-weight: 760; }
QLabel#Muted { color: #66778D; }
QLabel#Good { color: #08794D; font-weight: 650; }
QLabel#Warn { color: #A15C00; font-weight: 650; }
QLabel#LiveRate { color: #3A4A63; font-size: 10pt; font-weight: 650; padding-right: 8px; }
QLabel#InfoBadge { min-width:20px; max-width:20px; min-height:20px; max-height:20px; border-radius:10px; background:#EDF3FB; border:1px solid #C8D7EA; color:#336AAB; font-size:8pt; font-weight:800; }
QLabel#SourceChip { padding:2px 7px; border-radius:7px; background:#EFF5FD; border:1px solid #C7D8EF; color:#315D96; font-size:7pt; font-weight:750; }
QLabel#SectionHint { color:#708197; font-size:9pt; }
QPushButton { min-height:36px; padding:4px 14px; border:1px solid #C7D3E2; border-radius:12px; background:#FFFFFF; color:#172238; font-weight:620; }
QPushButton:hover { background:#EDF3FC; border-color:#AFC2DC; }
QPushButton:pressed { background:#E2EAF6; }
QPushButton:checked, QPushButton#Primary { background:#2767D8; border-color:#2767D8; color:white; }
QPushButton#Danger { background:#A82E3D; border-color:#A82E3D; color:white; }
QPushButton#Danger:hover { background:#8E2432; }
QPushButton#Nav { min-height:38px; text-align:left; padding-left:14px; border:0; border-left:3px solid transparent; border-radius:11px; background:transparent; color:#596B82; }
QPushButton#Nav:hover { background:#F0F4FA; color:#172033; }
QPushButton#Nav:checked { background:#E8F0FC; color:#153A72; border-left:3px solid #2767D8; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit { min-height:36px; background:#FFFFFF; border:1px solid #C9D5E4; border-radius:11px; padding:3px 10px; selection-background-color:#2767D8; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color:#5B86C5; }
QComboBox::drop-down { border: 0; width: 28px; }
QProgressBar { min-height:10px; max-height:10px; border:0; border-radius:5px; background:#E1E7F0; }
QProgressBar::chunk { background:#2767D8; border-radius:5px; }
QTableWidget { background:#FFFFFF; alternate-background-color:#F7F9FC; border:1px solid #D6E0EB; border-radius:14px; gridline-color:#E3E9F0; }
QHeaderView::section { background:#EFF3F8; color:#536278; border:0; border-bottom:1px solid #D8E0EB; padding:9px; font-weight:650; }
QToolTip { background:#FFFFFF; color:#172238; border:1px solid #AFC0D5; border-radius:7px; padding:8px; }
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: #C7D3E2; min-height: 32px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 9px; background: transparent; }
QScrollBar::handle:horizontal { background: #C7D3E2; min-width: 32px; border-radius: 4px; }
QCheckBox { spacing: 8px; }
"""
