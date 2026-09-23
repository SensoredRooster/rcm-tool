"""Desktop theme aligned with the Tester Share portal."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# Portal palette from rcm-tool-share login card
BG = "#070B14"
SURFACE = "#101628"
CARD = "#161D30"
LINE = "#2A3550"
TEXT = "#F4F7FF"
MUTED = "#8B97B3"
ACCENT = "#3B82F6"
ACCENT_HOVER = "#4F8CFF"
ACCENT_DIM = "#1E3A8A"
CYAN = "#5EC8FF"
WARN = "#F5A524"
TRACK = "#1A2340"
ON_ACCENT = "#F8FBFF"


def apply_rcm_theme(root: tk.Tk) -> None:
    root.configure(bg=BG)
    root.option_add("*Font", "{Segoe UI} 10")
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, fieldbackground=CARD, bordercolor=LINE)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 22))
    style.configure("Accent.TLabel", background=BG, foreground=CYAN, font=("Segoe UI Semibold", 9))
    style.configure(
        "TLabelframe",
        background=SURFACE,
        foreground=TEXT,
        bordercolor=LINE,
        relief="flat",
        padding=12,
    )
    style.configure(
        "TLabelframe.Label",
        background=SURFACE,
        foreground=CYAN,
        font=("Segoe UI Semibold", 8),
    )
    style.configure(
        "TButton",
        background=CARD,
        foreground=TEXT,
        bordercolor=LINE,
        focusthickness=0,
        padding=(16, 9),
        font=("Segoe UI Semibold", 10),
    )
    style.map(
        "TButton",
        background=[("active", ACCENT_DIM), ("disabled", "#0C1220")],
        foreground=[("disabled", "#5C6B7A")],
    )
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground=ON_ACCENT,
        bordercolor=ACCENT,
        padding=(18, 10),
        font=("Segoe UI Semibold", 10),
    )
    style.map(
        "Accent.TButton",
        background=[("active", ACCENT_HOVER), ("disabled", ACCENT_DIM)],
        foreground=[("disabled", "#9BB7E8")],
    )
    style.configure(
        "TCombobox",
        fieldbackground=CARD,
        background=CARD,
        foreground=TEXT,
        arrowcolor=CYAN,
        bordercolor=LINE,
        padding=6,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", CARD)],
        foreground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT)],
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=ACCENT,
        troughcolor=TRACK,
        bordercolor=TRACK,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        thickness=8,
    )
    style.configure(
        "Treeview",
        background=CARD,
        fieldbackground=CARD,
        foreground=TEXT,
        bordercolor=LINE,
        rowheight=28,
        font=("Cascadia Mono", 9),
    )
    style.configure(
        "Treeview.Heading",
        background=SURFACE,
        foreground=MUTED,
        bordercolor=LINE,
        font=("Segoe UI Semibold", 8),
        relief="flat",
    )
    style.map("Treeview", background=[("selected", ACCENT_DIM)], foreground=[("selected", TEXT)])
    style.configure("TSeparator", background=LINE)
