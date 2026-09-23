"""2026 visual system for Gamepad Signal Lab."""
from __future__ import annotations

DARK = """
QWidget { background: #080B12; color: #EEF2FF; font-family: "Segoe UI Variable", "Segoe UI"; font-size: 10pt; }
QMainWindow, QDialog { background: #080B12; }
QFrame#Sidebar { background: #0C111C; border-right: 1px solid #1F2A3D; }
QFrame#Topbar { background: #0A0F18; border-bottom: 1px solid #1F2A3D; }
QFrame#Card { background: #111827; border: 1px solid #243047; border-radius: 16px; }
QLabel#Eyebrow { color: #70D6FF; font-size: 8pt; font-weight: 700; }
QLabel#Title { font-size: 22pt; font-weight: 700; }
QLabel#PageTitle { font-size: 18pt; font-weight: 700; }
QLabel#Metric { font-size: 22pt; font-weight: 700; }
QLabel#Muted { color: #95A3B8; }
QLabel#Good { color: #66E3A4; font-weight: 600; }
QLabel#Warn { color: #F6C66A; font-weight: 600; }
QPushButton { min-height: 34px; padding: 4px 13px; border: 1px solid #2A3954; border-radius: 11px; background: #141D2D; color: #EEF2FF; font-weight: 600; }
QPushButton:hover { background: #19263B; border-color: #3D5277; }
QPushButton:pressed { background: #0D1522; }
QPushButton:checked, QPushButton#Primary { background: #2F6BFF; border-color: #5B8CFF; color: white; }
QPushButton#Danger { background: #5B1C24; border-color: #A83B49; color: #FFE9EC; }
QPushButton#Nav { text-align: left; padding-left: 14px; border: 0; background: transparent; color: #9DAAC0; }
QPushButton#Nav:hover { background: #121A28; color: #FFFFFF; }
QPushButton#Nav:checked { background: #17243A; color: #FFFFFF; border-left: 3px solid #5E9BFF; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit { min-height: 34px; background: #0C121D; border: 1px solid #26354D; border-radius: 10px; padding: 3px 9px; selection-background-color: #2F6BFF; }
QComboBox::drop-down { border: 0; width: 28px; }
QProgressBar { min-height: 10px; max-height: 10px; border: 0; border-radius: 5px; background: #1A2436; text-align: center; }
QProgressBar::chunk { background: #4B83FF; border-radius: 5px; }
QTableWidget { background: #0E1521; alternate-background-color: #111A29; border: 1px solid #243047; border-radius: 12px; gridline-color: #202C40; }
QHeaderView::section { background: #141D2D; color: #9FAEC3; border: 0; border-bottom: 1px solid #29364D; padding: 8px; font-weight: 600; }
QTabBar::tab { background: #101827; color: #91A0B7; border-radius: 9px; padding: 8px 14px; margin-right: 4px; }
QTabBar::tab:selected { background: #1A2942; color: white; }
QToolTip { background: #151E2D; color: white; border: 1px solid #31415D; padding: 6px; }
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: #2A3954; min-height: 28px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

LIGHT = """
QWidget { background: #F5F7FB; color: #182033; font-family: "Segoe UI Variable", "Segoe UI"; font-size: 10pt; }
QMainWindow, QDialog { background: #F5F7FB; }
QFrame#Sidebar { background: #FFFFFF; border-right: 1px solid #D9E0EA; }
QFrame#Topbar { background: #FFFFFF; border-bottom: 1px solid #D9E0EA; }
QFrame#Card { background: #FFFFFF; border: 1px solid #DCE3ED; border-radius: 16px; }
QLabel#Eyebrow { color: #216BCE; font-size: 8pt; font-weight: 700; }
QLabel#Title { font-size: 22pt; font-weight: 700; }
QLabel#PageTitle { font-size: 18pt; font-weight: 700; }
QLabel#Metric { font-size: 22pt; font-weight: 700; }
QLabel#Muted { color: #64748B; }
QLabel#Good { color: #087A4E; font-weight: 600; }
QLabel#Warn { color: #A25B00; font-weight: 600; }
QPushButton { min-height: 34px; padding: 4px 13px; border: 1px solid #C9D3E1; border-radius: 11px; background: #FFFFFF; color: #182033; font-weight: 600; }
QPushButton:hover { background: #EDF3FC; }
QPushButton:checked, QPushButton#Primary { background: #2767D8; border-color: #2767D8; color: white; }
QPushButton#Danger { background: #A82E3D; border-color: #A82E3D; color: white; }
QPushButton#Nav { text-align: left; padding-left: 14px; border: 0; background: transparent; color: #56657B; }
QPushButton#Nav:hover { background: #F0F4FA; color: #172033; }
QPushButton#Nav:checked { background: #E8F0FC; color: #153A72; border-left: 3px solid #2767D8; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit { min-height: 34px; background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 10px; padding: 3px 9px; }
QProgressBar { min-height: 10px; max-height: 10px; border: 0; border-radius: 5px; background: #E4E9F1; }
QProgressBar::chunk { background: #2767D8; border-radius: 5px; }
QTableWidget { background: #FFFFFF; alternate-background-color: #F8FAFD; border: 1px solid #D8E0EB; border-radius: 12px; gridline-color: #E4E9F1; }
QHeaderView::section { background: #EFF3F8; color: #536278; border: 0; border-bottom: 1px solid #D8E0EB; padding: 8px; font-weight: 600; }
"""
