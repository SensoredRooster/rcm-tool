"""Wire Stick Cleaner into signal_lab/ui.py. Safe to re-run."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "signal_lab" / "ui.py"


def apply_one(text: str, old: str, new: str, label: str) -> str:
    # Some replacements deliberately include the original anchor at the end
    # (for example, inserting a refresh hook immediately before an existing
    # block).  In those cases checking ``old not in text`` can never succeed,
    # so each updater run inserts another copy.  The full replacement being
    # present is enough to identify an already-wired block.
    if new.strip() and new in text:
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
        "from .theme import DARK, LIGHT\nfrom .widgets import ControllerView, HeatMapWidget, LineChart, MetricCard\n",
        "from .stick_cleaner_page import StickCleanerPage\nfrom .theme import DARK, LIGHT\nfrom .widgets import ControllerView, HeatMapWidget, LineChart, MetricCard\n",
        "import StickCleanerPage",
    )
    text = apply_one(
        text,
        'NAV = [\n    "Dashboard", "Live Capture", "Controller Lab", "Electrical Trace", "Oscillator Lab",\n',
        'NAV = [\n    "Dashboard", "Live Capture", "Controller Lab", "Stick Cleaner", "Electrical Trace", "Oscillator Lab",\n',
        "NAV Stick Cleaner",
    )
    text = apply_one(
        text,
        'FOCUS_NAV = ("Dashboard", "Controller Lab", "Electrical Trace", "Reports", "Support", "Settings")\n',
        'FOCUS_NAV = ("Dashboard", "Controller Lab", "Stick Cleaner", "Electrical Trace", "Reports", "Support", "Settings")\n',
        "FOCUS_NAV Stick Cleaner",
    )
    text = apply_one(
        text,
        "self._dashboard_page, self._live_page, self._controller_page, self._trace_page, self._oscillator_page,",
        "self._dashboard_page, self._live_page, self._controller_page, self._stick_cleaner_page, self._trace_page, self._oscillator_page,",
        "stack builder",
    )
    text = apply_one(
        text,
        "        layout.addStretch(1)\n        return self._scroll(w)\n\n    def _trace_page(self) -> QWidget:\n",
        "        layout.addStretch(1)\n        return self._scroll(w)\n\n    def _stick_cleaner_page(self) -> QWidget:\n        self.stick_cleaner = StickCleanerPage()\n        return self._scroll(self.stick_cleaner)\n\n    def _trace_page(self) -> QWidget:\n",
        "page method",
    )
    text = apply_one(
        text,
        'samples=list(self.controller_samples)[-800:] if current_page in {"Live Capture","Controller Lab"} else []',
        'samples=list(self.controller_samples)[-800:] if current_page in {"Live Capture","Controller Lab","Stick Cleaner"} else []',
        "refresh samples",
    )
    text = apply_one(
        text,
        '        if current_page == "Controller Lab" and samples:\n',
        '        if current_page == "Stick Cleaner" and hasattr(self, "stick_cleaner"):\n'
        "            self.stick_cleaner.update_from_lab(\n"
        "                timestamps,\n"
        "                list(self.controller_samples)[-800:],\n"
        '                evidence_class=evidence_class,\n'
        "                timing=t,\n"
        "            )\n\n"
        '        if current_page == "Controller Lab" and samples:\n',
        "refresh hook",
    )
    TARGET.write_text(text, encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
