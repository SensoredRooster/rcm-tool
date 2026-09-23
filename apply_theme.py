"""Apply the Tester Share portal theme. Run last."""

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
        "from pathlib import Path\n",
        "from pathlib import Path\nfrom rcm_theme import apply_rcm_theme\n",
        "import theme",
    )
    text = apply_one(
        text,
        "        self._build_ui()\n        self.after(100, self._refresh_live_values)\n",
        "        self._build_ui()\n        apply_rcm_theme(self)\n        self.after(100, self._refresh_live_values)\n",
        "apply theme after build",
    )
    text = apply_one(
        text,
        '        ttk.Label(header, text="RCM Tool", font=("Segoe UI", 18, "bold")).pack(anchor="w")\n'
        "        ttk.Label(\n"
        "            header,\n"
        '            text="Controller certification • reads input only; never injects input into a game",\n'
        '        ).pack(anchor="w")\n',
        '        ttk.Label(header, text="PRIVATE SESSION", style="Accent.TLabel").pack(anchor="w")\n'
        '        ttk.Label(header, text="RCMTool", style="Title.TLabel").pack(anchor="w")\n'
        "        ttk.Label(\n"
        "            header,\n"
        '            text="Controller integrity  ·  read-only capture  ·  testers",\n'
        '            style="Muted.TLabel",\n'
        '        ).pack(anchor="w", pady=(4, 0))\n',
        "header typography",
    )
    text = apply_one(
        text,
        '        self.start_button = ttk.Button(controls, text="Start single test", command=self.start_test)\n',
        '        self.start_button = ttk.Button(controls, text="Start single test", command=self.start_test, style="Accent.TButton")\n',
        "accent start button",
    )
    TARGET.write_text(text, encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
