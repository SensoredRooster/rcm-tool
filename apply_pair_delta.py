"""Wire pair-delta classification into comparison JSON. Run last."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "controller_integrity.py"

IMPORT_OLD = "from pathlib import Path\n"
IMPORT_NEW = "from pathlib import Path\nfrom pair_delta import classify_pair_delta\n"

CMP_OLD = '''            "beforeReport": before_path.name if before_path else None,
            "afterReport": after_path.name,
            "metrics": deltas,
'''

CMP_NEW = '''            "beforeReport": before_path.name if before_path else None,
            "afterReport": after_path.name,
            "metrics": deltas,
            "pairDelta": classify_pair_delta(
                before.protocol,
                after.protocol,
                before.classification,
                after.classification,
                getattr(before, "rc_filter_metrics", None),
                getattr(after, "rc_filter_metrics", None),
            ),
'''

MSG_OLD = '''            comparison_text = f"\nComparison saved to:\n{comparison_path}" if comparison_path else ""
'''

MSG_NEW = '''            pair_label = ""
            if comparison_path:
                try:
                    import json as _json
                    pair_label = _json.loads(comparison_path.read_text(encoding="utf-8")).get("pairDelta", {}).get("pair_delta", "")
                except Exception:
                    pair_label = ""
            comparison_text = f"\nComparison saved to:\n{comparison_path}" if comparison_path else ""
            if pair_label:
                comparison_text += f"\nPair delta: {pair_label}"
'''


def apply_one(text: str, old: str, new: str, label: str) -> str:
    if new in text or "Pair delta:" in text and label == "pair delta in completion dialog":
        print(f"skip {label}: already applied")
        return text
    if old not in text:
        print(f"skip {label}: block not found (file already customized)")
        return text
    print(f"apply {label}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    text = apply_one(text, IMPORT_OLD, IMPORT_NEW, "import pair_delta")
    text = apply_one(text, CMP_OLD, CMP_NEW, "comparison pairDelta field")
    text = apply_one(text, MSG_OLD, MSG_NEW, "pair delta in completion dialog")
    TARGET.write_text(text, encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
