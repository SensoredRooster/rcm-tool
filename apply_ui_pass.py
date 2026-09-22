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

    TARGET.write_text(text, encoding="utf-8")
    print("partial-note: use repo file from artifacts if this truncated")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
