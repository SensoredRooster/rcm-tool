"""2026 dark lab theme for the RCM Tool window."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#0E1116"
SURFACE = "#161B22"
CARD = "#1C2330"
LINE = "#2A3340"
TEXT = "#E8EDF4"
MUTED = "#8B9BB0"
ACCENT = "#3DDC97"
ACCENT_DIM = "#1F6F4A"
WARN = "#F5A524"
TRACK = "#243042"


def apply_rcm_theme(root: tk.Tk) -> None:
    root.configure(bg=BG)
    root.option_add("*Font", "{Segoe UI Variable} 10")
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
    style.configure("Accent.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 9))
    style.configure(
        "TLabelframe",
        background=SURFACE,
        foreground=TEXT,
        bordercolor=LINE,
        relief="flat",
        padding=10,
    )
    style.configure(
        "TLabelframe.Label",
        background=SURFACE,
        foreground=MUTED,
        font=("Segoe UI", 9),
    )
    style.configure(
        "TButton",
        background=CARD,
        foreground=TEXT,
        bordercolor=LINE,
        focusthickness=0,
        padding=(14, 8),
        font=("Segoe UI Semibold", 10),
    )
    style.map(
        "TButton",
        background=[("active", ACCENT_DIM), ("disabled", "#12161C")],
        foreground=[("disabled", "#5C6B7A")],
    )
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="#062016",
        bordercolor=ACCENT,
        padding=(16, 8),
        font=("Segoe UI Semibold", 10),
    )
    style.map("Accent.TButton", background=[("active", "#64E8B0"), ("disabled", ACCENT_DIM)])
    style.configure(
        "TCombobox",
        fieldbackground=CARD,
        background=CARD,
        foreground=TEXT,
        arrowcolor=ACCENT,
        bordercolor=LINE,
        padding=4,
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
        rowheight=26,
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
