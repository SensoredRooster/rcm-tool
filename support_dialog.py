from __future__ import annotations

import json
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from rcm_theme import ACCENT, BG, CARD, LINE, MUTED, TEXT, apply_rcm_theme
from support import (
    SESSION_ID,
    create_support_bundle,
    health_snapshot,
    open_logs_folder,
    open_repository,
    report_issue,
    upload_support_bundle,
)


def open_support_center(parent: tk.Misc) -> None:
    window = tk.Toplevel(parent)
    window.title("RCMTool — Support")
    window.geometry("720x560")
    window.minsize(640, 480)
    window.configure(bg=BG)
    window.transient(parent)
    apply_rcm_theme(window)

    frame = ttk.Frame(window, padding=22)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="PRIVATE SESSION", style="Accent.TLabel").pack(anchor="w")
    ttk.Label(frame, text="Support & Diagnostics", style="Title.TLabel").pack(anchor="w", pady=(2, 0))
    ttk.Label(
        frame,
        text="Stays on this PC until you send a bundle. No upload without confirmation.",
        style="Muted.TLabel",
        wraplength=660,
    ).pack(anchor="w", pady=(6, 16))

    status = tk.Text(
        frame,
        height=13,
        wrap="word",
        font=("Cascadia Mono", 10),
        bg=CARD,
        fg=TEXT,
        insertbackground=TEXT,
        relief="flat",
        highlightthickness=1,
        highlightbackground=LINE,
        padx=12,
        pady=12,
    )
    status.pack(fill="both", expand=True)
    snapshot = health_snapshot()
    status.insert("1.0", json.dumps(snapshot, indent=2))
    status.configure(state="disabled")

    ttk.Label(frame, text=f"Session ID  ·  {SESSION_ID}", style="Muted.TLabel").pack(anchor="w", pady=(12, 10))

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")

    def download_bundle() -> None:
        try:
            bundle = create_support_bundle()
            messagebox.showinfo("Support bundle created", f"Created:\n{bundle}", parent=window)
        except Exception as exc:
            messagebox.showerror("Support bundle failed", str(exc), parent=window)

    def send_bundle() -> None:
        if not messagebox.askyesno(
            "Send diagnostics?",
            "Create and send a redacted diagnostic bundle to the RCM Tool developer now?\n\n"
            "No upload occurs unless you confirm this action.",
            parent=window,
        ):
            return
        try:
            result = upload_support_bundle()
            messagebox.showinfo(
                "Diagnostics sent",
                f"Upload completed successfully.\nHTTP status: {result.get('status')}",
                parent=window,
            )
        except Exception as exc:
            messagebox.showerror(
                "Upload failed",
                f"{exc}\n\nYou can still create a local support bundle and attach it manually.",
                parent=window,
            )

    ttk.Button(buttons, text="Create bundle", command=download_bundle).grid(row=0, column=0, padx=(0, 8), pady=4)
    ttk.Button(buttons, text="Send to developer", command=send_bundle, style="Accent.TButton").grid(row=0, column=1, padx=8, pady=4)
    ttk.Button(buttons, text="Close", command=window.destroy).grid(row=0, column=2, padx=8, pady=4)
    ttk.Button(buttons, text="Logs folder", command=open_logs_folder).grid(row=1, column=0, padx=(0, 8), pady=4)
    ttk.Button(buttons, text="GitHub issue", command=report_issue).grid(row=1, column=1, padx=8, pady=4)
    ttk.Button(buttons, text="Repository", command=open_repository).grid(row=1, column=2, padx=8, pady=4)
    ttk.Button(
        buttons,
        text="Tester Share",
        command=lambda: webbrowser.open("https://rcm-tool-share.sensoredrooster-com.workers.dev"),
        style="Accent.TButton",
    ).grid(row=2, column=0, padx=(0, 8), pady=4)
