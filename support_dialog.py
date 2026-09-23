from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk

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
    window.title("RCM Tool — Support & Diagnostics")
    window.geometry("720x520")
    window.minsize(640, 460)
    window.transient(parent)

    frame = ttk.Frame(window, padding=18)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Support & Diagnostics", font=("Segoe UI", 18, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="Diagnostics stay local unless you explicitly choose Send Diagnostics to Developer.",
        wraplength=660,
    ).pack(anchor="w", pady=(4, 14))

    status = tk.Text(frame, height=13, wrap="word", font=("Consolas", 10))
    status.pack(fill="both", expand=True)
    snapshot = health_snapshot()
    status.insert("1.0", json.dumps(snapshot, indent=2))
    status.configure(state="disabled")

    session = ttk.Label(frame, text=f"Session ID: {SESSION_ID}")
    session.pack(anchor="w", pady=(10, 8))

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

    ttk.Button(buttons, text="Create Support Bundle", command=download_bundle).grid(row=0, column=0, padx=(0, 8), pady=4)
    ttk.Button(buttons, text="Send Diagnostics to Developer", command=send_bundle).grid(row=0, column=1, padx=8, pady=4)
    ttk.Button(buttons, text="Open Logs Folder", command=open_logs_folder).grid(row=1, column=0, padx=(0, 8), pady=4)
    ttk.Button(buttons, text="Report Issue on GitHub", command=report_issue).grid(row=1, column=1, padx=8, pady=4)
    ttk.Button(buttons, text="Open Repository", command=open_repository).grid(row=1, column=2, padx=8, pady=4)
    ttk.Button(buttons, text="Close", command=window.destroy).grid(row=0, column=2, padx=8, pady=4)
