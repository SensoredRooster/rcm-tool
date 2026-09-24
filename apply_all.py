"""Run every local wiring script in order. Safe to re-run."""

from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = (
    "apply_rc_wiring.py",
    "apply_ui_pass.py",
    "apply_auto_export.py",
    "apply_pair_delta.py",
    "apply_injection.py",
    "apply_after_noise.py",
    "apply_hid_signal.py",
    "apply_theme.py",
    # Stick Cleaner is now part of the tracked Qt UI. Do not rewrite ui.py
    # during launch; doing so dirties every checkout and can duplicate hooks.
)


def main() -> None:
    for name in SCRIPTS:
        path = ROOT / name
        if not path.exists():
            raise SystemExit(f"missing {name}")
        print(f"=== {name} ===")
        runpy.run_path(str(path), run_name="__main__")
    print("all wiring applied")


if __name__ == "__main__":
    main()
