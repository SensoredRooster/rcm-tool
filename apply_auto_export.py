"""Auto-write JSON when a single test finishes. Run after apply_ui_pass.py."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "controller_integrity.py"

OLD = """        self._enable_controls()
        self.status_var.set("Test complete")
"""

NEW = """        saved = self.write_report(result, show_message=False)
        self._enable_controls()
        self.status_var.set(f"Test complete. Saved {saved.name}")
"""


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    if NEW in text:
        print("skip auto-export: already applied")
        return
    if OLD not in text:
        raise SystemExit("could not find block: single-test auto-export")
    print("apply single-test auto-export")
    TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
