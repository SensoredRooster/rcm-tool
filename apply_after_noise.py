"""Add After-only noise so Before + After is not the same capture."""

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
        "from injection import apply_injection, classify_injection, describe as describe_injection, is_injected, PROTOCOL_LABELS\n",
        "from injection import apply_injection, classify_injection, describe as describe_injection, is_injected, PROTOCOL_LABELS, AFTER_NOISE_LABELS\n",
        "import after noise labels",
    )
    text = apply_one(
        text,
        "        self.current_protocol = \"neutral-stick\"\n",
        "        self.current_protocol = \"neutral-stick\"\n        self.after_noise_protocol = \"\"\n",
        "init after noise protocol",
    )
    text = apply_one(
        text,
        '        ttk.Label(controls, text="Protocol:").grid(row=0, column=3, sticky="e", **padding)\n',
        '        ttk.Label(controls, text="Protocol:").grid(row=0, column=3, sticky="e", **padding)\n',
        "protocol label exists",
    )
    text = apply_one(
        text,
        "            values=(\"Neutral hold\", \"Guided movement\", \"Injected HF sine\", \"Injected slow sine\", \"Injected rest tick\"),\n",
        "            values=(\"Neutral hold\", \"Guided movement\", \"Injected HF sine\", \"Injected slow sine\", \"Injected rest tick\"),\n",
        "protocol values exist",
    )
    marker = "        self.start_button = ttk.Button(controls, text=\"Start single test\""
    insert = '''        ttk.Label(controls, text="After noise:").grid(row=1, column=0, sticky="e", **padding)
        self.after_noise_var = tk.StringVar(value="None")
        self.after_noise_combo = ttk.Combobox(
            controls,
            textvariable=self.after_noise_var,
            values=tuple(AFTER_NOISE_LABELS.keys()),
            state="readonly",
            width=22,
        )
        self.after_noise_combo.grid(row=1, column=1, sticky="w", **padding)
        ttk.Label(controls, text="After only — not written to the pad", style="Muted.TLabel").grid(row=1, column=2, columnspan=2, sticky="w", **padding)
'''
    if "after_noise_combo" not in text and marker in text:
        print("apply after noise combo")
        text = text.replace(marker, insert + marker, 1)
    else:
        print("skip after noise combo: already applied or marker missing")

    text = apply_one(
        text,
        "                if is_injected(self.current_protocol):\n                    sample = apply_injection(sample, now - started, self.current_protocol)\n",
        "                noise_protocol = self.current_protocol\n                if getattr(self, \"after_noise_protocol\", \"\") and \"After\" in str(getattr(self, \"current_test_phase\", \"\")):\n                    noise_protocol = self.after_noise_protocol\n                if is_injected(noise_protocol):\n                    sample = apply_injection(sample, now - started, noise_protocol)\n",
        "after phase uses after noise",
    )
    text = apply_one(
        text,
        "        self._select_protocol()\n        if is_injected(self.current_protocol):\n",
        '''        self._select_protocol()
        self.after_noise_protocol = AFTER_NOISE_LABELS.get(getattr(self, "after_noise_var", tk.StringVar(value="None")).get(), "")
        if is_injected(self.current_protocol):
''',
        "capture after noise on pair start",
    )
    TARGET.write_text(text, encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
