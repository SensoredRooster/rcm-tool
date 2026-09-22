"""Second-pass UI and capture-performance patches for controller_integrity.py.

Run after apply_rc_wiring.py. Safe to re-run.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "controller_integrity.py"


def apply_one(text: str, old: str, new: str, label: str) -> str:
    if new.strip() and new in text and old not in text:
        print(f"skip {label}: already applied")
        return text
    if old not in text:
        raise SystemExit(f"could not find block: {label}")
    print(f"apply {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")

    text = apply_one(
        text,
        '        self.geometry("900x620")\n        self.minsize(760, 520)\n',
        '        self.geometry("980x740")\n        self.minsize(900, 660)\n        self.test_started_monotonic = 0.0\n',
        "window size + test clock",
    )

    if "self.progress = ttk.Progressbar" in text:
        print("skip test progress bar: already applied")
    else:
        text = apply_one(
            text,
            '        ttk.Label(controls, textvariable=self.duration_var).grid(row=0, column=0, sticky="w", **padding)\n',
            '        ttk.Label(controls, textvariable=self.duration_var).grid(row=0, column=0, sticky="w", **padding)\n'
            '        self.progress = ttk.Progressbar(controls, orient="horizontal", length=220, mode="determinate", maximum=100)\n'
            '        self.progress.grid(row=3, column=0, columnspan=5, sticky="ew", padx=12, pady=(0, 8))\n'
            '        self.progress["value"] = 0\n',
            "test progress bar",
        )

    text = apply_one(
        text,
        '        columns = ("axis", "rms", "jitter", "peak", "crossings", "active")\n'
        '        self.tree = ttk.Treeview(results, columns=columns, show="headings", height=8)\n'
        '        headings = {"axis": "Axis", "rms": "Signal RMS", "jitter": "Jitter RMS", "peak": "Peak-to-peak", "crossings": "Threshold crossings", "active": "Active while stationary"}\n'
        '        widths = {"axis": 70, "rms": 105, "jitter": 105, "peak": 125, "crossings": 160, "active": 175}\n',
        '        columns = ("axis", "rms", "jitter", "hfrms", "peak", "crossings", "active")\n'
        '        self.tree = ttk.Treeview(results, columns=columns, show="headings", height=6)\n'
        '        headings = {"axis": "Axis", "rms": "Signal RMS", "jitter": "Jitter RMS", "hfrms": "HF RMS", "peak": "Peak-to-peak", "crossings": "Threshold crossings", "active": "Active % "}\n'
        '        widths = {"axis": 58, "rms": 92, "jitter": 92, "hfrms": 88, "peak": 110, "crossings": 150, "active": 90}\n',
        "results table HF RMS column",
    )

    text = apply_one(
        text,
        '            self.tree.insert("", "end", values=(\n'
        '                axis.upper(),\n'
        '                f"{metrics.rms:.5f}",\n'
        '                f"{metrics.jitter_rms:.5f}",\n'
        '                f"{metrics.peak_to_peak:.5f}",\n'
        '                metrics.deadzone_crossings,\n'
        '                f"{metrics.nonzero_stationary_percent:.2f}%",\n'
        '            ))\n',
        '            hf = result.rc_filter_metrics.get(f"{axis}_high_freq_rms", metrics.jitter_rms)\n'
        '            self.tree.insert("", "end", values=(\n'
        '                axis.upper(),\n'
        '                f"{metrics.rms:.5f}",\n'
        '                f"{metrics.jitter_rms:.5f}",\n'
        '                f"{hf:.5f}",\n'
        '                f"{metrics.peak_to_peak:.5f}",\n'
        '                metrics.deadzone_crossings,\n'
        '                f"{metrics.nonzero_stationary_percent:.2f}%",\n'
        '            ))\n',
        "populate HF RMS cells",
    )

    text = apply_one(
        text,
        '        self.start_button.configure(state="normal")\n'
        '        self.pair_button.configure(state="normal")\n'
        '        self.phase_combo.configure(state="readonly")\n'
        '        self.source_combo.configure(state="readonly")\n'
        '        self.refresh_button.configure(state="normal")\n',
        '        self.start_button.configure(state="normal")\n'
        '        self.pair_button.configure(state="normal")\n'
        '        self.phase_combo.configure(state="readonly")\n'
        '        self.protocol_combo.configure(state="readonly")\n'
        '        self.source_combo.configure(state="readonly")\n'
        '        self.refresh_button.configure(state="normal")\n'
        '        if self.latest_result is not None:\n'
        '            self.export_button.configure(state="normal")\n'
        '        self.progress["value"] = 0\n',
        "re-enable export and protocol after test",
    )

    text = apply_one(
        text,
        '        self.phase_combo.configure(state="disabled")\n'
        '        self.source_combo.configure(state="disabled")\n'
        '        self.refresh_button.configure(state="disabled")\n',
        '        self.phase_combo.configure(state="disabled")\n'
        '        self.protocol_combo.configure(state="disabled")\n'
        '        self.source_combo.configure(state="disabled")\n'
        '        self.refresh_button.configure(state="disabled")\n'
        '        self.export_button.configure(state="disabled")\n'
        '        self.test_started_monotonic = time.monotonic()\n'
        '        self.progress["value"] = 0\n',
        "lock protocol during capture",
    )

    text = apply_one(
        text,
        '        sample = self.backend.read()\n'
        '        if sample:\n'
        '            self.current_values.update(sample)\n'
        '            for axis, label in self.value_labels.items():\n'
        '                label.configure(text=f"{self.current_values[axis]:+.4f}")\n'
        '                self.axis_bars[axis]["value"] = (self.current_values[axis] + 1.0) * 50.0\n'
        '                if abs(self.current_values[axis]) >= 0.05:\n'
        '                    self.axes_seen.add(axis.upper())\n'
        '        status = getattr(self.backend, "status", None)\n'
        '        if callable(status):\n'
        '            self.input_status_var.set(status())\n'
        '            has_input = sample is not None or (\n'
        '                isinstance(self.backend, AutomaticControllerBackend) and self.backend.active is not None\n'
        '            )\n'
        '            if has_input:\n'
        '                if self.axes_seen:\n'
        '                    seen = ", ".join(sorted(self.axes_seen))\n'
        '                    self.axes_seen_var.set(f"Axes seen: {seen}")\n'
        '                    self.live_help_var.set("Input detected. Move every stick direction once. If an axis never appears, select SDL or Raw HID instead of DirectInput.")\n'
        '                else:\n'
        '                    self.live_help_var.set("Input connection found. Move each stick to verify its axes.")\n'
        '        self.after(100, self._refresh_live_values)\n',
        '        testing = bool(self.test_thread and self.test_thread.is_alive())\n'
        '        if testing:\n'
        '            elapsed = time.monotonic() - self.test_started_monotonic\n'
        '            fraction = min(100.0, 100.0 * elapsed / max(self.current_duration, 0.001))\n'
        '            self.progress["value"] = fraction\n'
        '            remaining = max(0.0, self.current_duration - elapsed)\n'
        '            self.duration_var.set(f"Remaining: {remaining:.1f} s")\n'
        '            self.after(50, self._refresh_live_values)\n'
        '            return\n'
        '        sample = self.backend.read()\n'
        '        if sample:\n'
        '            self.current_values.update(sample)\n'
        '            for axis, label in self.value_labels.items():\n'
        '                label.configure(text=f"{self.current_values[axis]:+.4f}")\n'
        '                self.axis_bars[axis]["value"] = (self.current_values[axis] + 1.0) * 50.0\n'
        '                if abs(self.current_values[axis]) >= 0.05:\n'
        '                    self.axes_seen.add(axis.upper())\n'
        '        status = getattr(self.backend, "status", None)\n'
        '        if callable(status):\n'
        '            self.input_status_var.set(status())\n'
        '            has_input = sample is not None or (\n'
        '                isinstance(self.backend, AutomaticControllerBackend) and self.backend.active is not None\n'
        '            )\n'
        '            if has_input:\n'
        '                if self.axes_seen:\n'
        '                    seen = ", ".join(sorted(self.axes_seen))\n'
        '                    self.axes_seen_var.set(f"Axes seen: {seen}")\n'
        '                    self.live_help_var.set("Input detected. Move every stick direction once. If an axis never appears, select SDL or Raw HID instead of DirectInput.")\n'
        '                else:\n'
        '                    self.live_help_var.set("Input connection found. Move each stick to verify its axes.")\n'
        '        self.after(80, self._refresh_live_values)\n',
        "pause live HID reads during capture",
    )

    TARGET.write_text(text, encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
