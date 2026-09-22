# RCM Tool

Windows controller integrity prototype. It **reads** stick axes. It does **not** write to the controller, inject into a game, or flash firmware.

Repository: https://github.com/SensoredRooster/rcm-tool

This tree is a working prototype: capture, RC high-frequency RMS, auto-export JSON, Before/After pair-delta, and an in-app injected self-test that checks the tool itself.

## 1. Install once

- Windows 10/11
- Python 3.10+ 64-bit with PATH and Tcl/Tk
- Git

```powershell
git clone https://github.com/SensoredRooster/rcm-tool.git
cd rcm-tool
python -m pip install -r requirements.txt
python apply_all.py
python -m unittest discover -v
python rcm_tool.py
```

Later sessions: double-click **RCM Tool - Update and Start.bat** (pull + apply_all + start).

If an apply script says `could not find block`:

```powershell
git checkout -- controller_integrity.py
python apply_all.py
```

`controller_integrity.py` on GitHub is the unwired base. Apply scripts patch it locally. That is expected.

## 2. What the prototype does

| Piece | Role |
|---|---|
| Neutral hold (10 s) | Thumbs off. Baseline rest noise. |
| Guided movement (20 s) | Slow stick path. HF residual on motion. |
| Before + After pair | Same protocol both sides. Writes a comparison JSON with `pairDelta`. |
| Injected HF / slow / rest | Software sine added after `read()`, before RC. Tool check only. |
| Auto-export | Every finished test writes `reports\\*.json`. No Export click required. |

Numbers come from the sample stream Windows handed the app. The app does not invent axes.

## 3. Session order (do not skip)

**First, after every update, on this PC — tool check, not a pad verdict**

1. Detect devices. Pick the source whose live bars move.
2. Protocol = Injected HF sine. Thumbs off. Start single test. Pass: LX HF RMS rises, RY stays quiet. Fail: stop. RC wiring is broken.
3. Protocol = Injected slow sine. Thumbs off. Pass: HF RMS stays low. Fail: tau is wrong.
4. Optional: Injected rest tick.

Those files contain `INJECTED_` and `noiseInjected: true`. Do not score a pad from them.

**Then the pad screen — injection off**

5. Protocol = Neutral hold. Run Before + After pair. Thumbs off both sides. Change only one thing between phases.
6. Protocol = Guided movement. Pair again. Slow sticks only. No shake.

Pair is blocked while an Injected protocol is selected. Switch back to Neutral or Guided first.

## 4. Pair delta labels

Written on `*_before_after_comparison.json` and shown in the completion dialog.

| pair_delta | Meaning |
|---|---|
| unchanged | After HF RMS stayed inside the floor |
| increased_at_rest | Neutral After louder with sticks untouched (floor 0.003) |
| increased_high_freq | Guided After added HF energy (floor 0.005) |
| unsupported | Bad capture, mixed protocols, or injected pair |

This is a change detector. It is not organic vs non-organic. Repeat a pair three times before you treat the sign as a pattern.

## 5. RC filter

- alpha = 1 - exp(-dt / tau)
- tau = 0.05 s (about 3.18 Hz cutoff)
- HF RMS = RMS(raw minus filtered trend)
- reportVersion must be 0.2

Motion slower than about 3 Hz rides inside the trend and will not inflate HF RMS.

## 6. After a run

Status line: Test complete. Saved filename.

Then **RCM Tool - Submit Reports.bat**, type YES, tell Grok the filename.

Do not submit INJECTED_ files as if they were a pad screen.

## 7. Files

`rcm_tool.py`, `controller_integrity.py` (patched locally), `rc_filter.py`, `pair_delta.py`, `injection.py`, `apply_all.py`, `apply_rc_wiring.py`, `apply_ui_pass.py`, `apply_auto_export.py`, `apply_pair_delta.py`, `apply_injection.py`, `test_*.py`.

## 8. Troubleshooting

- Live axes stay +0.0000: wrong dropdown. Try SDL, Raw HID, then the XInput slot joy.cpl uses.
- No JSON after a test: `python apply_auto_export.py`
- reportVersion 0.1: `python apply_rc_wiring.py` then capture again
- Pair starts on an injected protocol: pull latest and run `apply_injection.py`
- joy.cpl axes do not move: the app cannot invent them
