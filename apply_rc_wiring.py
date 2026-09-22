"""Apply RC-filter wiring to controller_integrity.py. Safe to re-run."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "controller_integrity.py"

IMPORT_BLOCK = """from typing import Optional, Protocol

from rc_filter import (
    DEFAULT_TAU_SECONDS as RC_FILTER_TAU_SECONDS,
    RCLowPassFilter,
    STICK_AXES,
    build_rc_filter_metrics,
    rc_filter_series,
    residual_rms,
)
"""

OLD_IMPORT = "from typing import Optional, Protocol\n"

OLD_VERSION = "REPORT_VERSION = \"0.1\"\n"
NEW_VERSION = "REPORT_VERSION = \"0.2\"\n"
