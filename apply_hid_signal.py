"""Wire real HID timing/quantization metrics into reports."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "controller_integrity.py"


def apply_one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"skip {label}: already applied")
        return text
    if old not in text:
        print(f"skip {label}: block not found (file already customized)")
        return text
    print(f"apply {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    text = apply_one(
        text,
        "from rc_filter import (",
        "from hid_signal import build_hid_signal_metrics, compare_hid_signal\nfrom rc_filter import (",
        "import hid_signal",
    )
    text = apply_one(
        text,
        "    rc_filter_metrics: dict[str, float] = field(default_factory=dict)\n",
        "    rc_filter_metrics: dict[str, float] = field(default_factory=dict)\n    hid_signal_metrics: dict = field(default_factory=dict)\n",
        "TestResult hid_signal field",
    )
    text = apply_one(
        text,
        "            rc_filter_metrics=build_rc_filter_metrics(captured_samples, RC_FILTER_TAU_SECONDS),\n",
        "            rc_filter_metrics=build_rc_filter_metrics(captured_samples, RC_FILTER_TAU_SECONDS),\n            hid_signal_metrics=build_hid_signal_metrics(captured_samples, captured_hid_reports),\n",
        "construct hid_signal_metrics",
    )
    text = apply_one(
        text,
        '            "rc_filter_metrics": result.rc_filter_metrics,\n',
        '            "rc_filter_metrics": result.rc_filter_metrics,\n            "hid_signal_metrics": result.hid_signal_metrics,\n',
        "report hid_signal_metrics",
    )
    text = apply_one(
        text,
        "            \"afterRcFilterMetrics\": after.rc_filter_metrics,\n",
        "            \"afterRcFilterMetrics\": after.rc_filter_metrics,\n            \"beforeHidSignal\": getattr(before, \"hid_signal_metrics\", {}),\n            \"afterHidSignal\": getattr(after, \"hid_signal_metrics\", {}),\n            \"hidSignalDelta\": compare_hid_signal(\n                getattr(before, \"hid_signal_metrics\", {}),\n                getattr(after, \"hid_signal_metrics\", {}),\n            ),\n",
        "comparison hid signal",
    )
    TARGET.write_text(text, encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
