"""Wire injected self-test into controller_integrity.py. Run after RC wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "controller_integrity.py"


def apply_one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
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
        "from pathlib import Path\n",
        "from pathlib import Path\nfrom injection import apply_injection, classify_injection, describe as describe_injection, is_injected, PROTOCOL_LABELS\n",
        "import injection",
    )

    text = apply_one(
        text,
        '            values=("Neutral hold", "Guided movement"),\n',
        '            values=("Neutral hold", "Guided movement", "Injected HF sine", "Injected slow sine", "Injected rest tick"),\n',
        "protocol combo recipes",
    )

    text = apply_one(
        text,
        '''    def _select_protocol(self) -> None:
        if self.protocol_var.get() == "Guided movement":
            self.current_protocol = "guided-movement"
            self.current_duration = MOTION_TEST_DURATION_SECONDS
        else:
            self.current_protocol = "neutral-stick"
            self.current_duration = TEST_DURATION_SECONDS
''',
        '''    def _select_protocol(self) -> None:
        label = self.protocol_var.get()
        if label == "Guided movement":
            self.current_protocol = "guided-movement"
            self.current_duration = MOTION_TEST_DURATION_SECONDS
        elif label in PROTOCOL_LABELS:
            self.current_protocol = PROTOCOL_LABELS[label]
            self.current_duration = TEST_DURATION_SECONDS
        else:
            self.current_protocol = "neutral-stick"
            self.current_duration = TEST_DURATION_SECONDS
''',
        "select injected protocols",
    )

    text = apply_one(
        text,
        '''            sample = self.backend.read()
            if sample:
                now = time.monotonic()
                dt = 0.0 if last_sample_time is None else now - last_sample_time
                last_sample_time = now
                filtered = trend_filter.update(sample, dt)
''',
        '''            sample = self.backend.read()
            if sample:
                now = time.monotonic()
                dt = 0.0 if last_sample_time is None else now - last_sample_time
                last_sample_time = now
                if is_injected(self.current_protocol):
                    sample = apply_injection(sample, now - started, self.current_protocol)
                filtered = trend_filter.update(sample, dt)
''',
        "inject after read before RC",
    )

    text = apply_one(
        text,
        '''    if result.protocol == "guided-movement":
''',
        '''    if is_injected(result.protocol):
        return classify_injection(result.protocol, getattr(result, "rc_filter_metrics", None))
    if result.protocol == "guided-movement":
''',
        "classify injected self-test",
    )

    text = apply_one(
        text,
        '''                "inputInjected": False,
                "noiseInjected": False,
''',
        '''                "inputInjected": is_injected(result.protocol),
                "noiseInjected": is_injected(result.protocol),
                "injection": describe_injection(result.protocol),
''',
        "report injection flags",
    )

    text = apply_one(
        text,
        '''        destination = reports_directory() / f"{timestamp}_{phase_slug}_{controller_slug}.json"
''',
        '''        injected_tag = "INJECTED_" if is_injected(result.protocol) else ""
        destination = reports_directory() / f"{timestamp}_{injected_tag}{phase_slug}_{controller_slug}.json"
''',
        "INJECTED filename tag",
    )

    text = apply_one(
        text,
        '''        self._select_protocol()
        self.phase_var.set("Before - default")
        self.current_test_phase = "Before - default"
''',
        '''        self._select_protocol()
        if is_injected(self.current_protocol):
            messagebox.showinfo(
                "Injected self-test",
                "Injected recipes check the tool. They are not part of Before + After.\\n\\n"
                "Use Start single test with thumbs off. Switch Protocol to Neutral hold or Guided movement before pairing.",
            )
            self.pair_mode = False
            return
        self.phase_var.set("Before - default")
        self.current_test_phase = "Before - default"
''',
        "block pair on injected protocol",
    )

    text = apply_one(
        text,
        '''        if self.current_protocol != "guided-movement":
            return True
''',
        '''        if is_injected(self.current_protocol):
            return messagebox.askokcancel(
                "Injected self-test",
                "This adds a known sine to LX in software after read(). It does not write to the controller.\\n\\n"
                "Thumbs off. The filename will contain INJECTED_. Do not treat it as a pad screen.\\n\\n"
                "Click OK to begin.",
            )
        if self.current_protocol != "guided-movement":
            return True
''',
        "confirm injected self-test",
    )

    TARGET.write_text(text, encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
