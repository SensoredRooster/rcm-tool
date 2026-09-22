# RCM Tool

Tournament controller integrity and telemetry tool for Windows.

Reads axes only. Does not write to the controller.

Repository: https://github.com/SensoredRooster/rcm-tool

## Update

```powershell
git pull
python apply_rc_wiring.py
python apply_ui_pass.py
python apply_auto_export.py
python apply_pair_delta.py
python apply_injection.py
python -m unittest discover -v
python rcm_tool.py
```

Or **RCM Tool - Update and Start.bat**.

Single tests and pair tests auto-write `reports\*.json`. Then **Submit Reports.bat**, type `YES`.

## Injected self-test (lab only)

Protocol dropdown extras:

| Protocol | Adds on LX after read | Expected |
|---|---|---|
| Injected HF sine | 30 Hz, amp 0.06 | LX HF RMS rises; RY stays quiet |
| Injected slow sine | 0.5 Hz, amp 0.06 | RC trend absorbs it; HF RMS stays low |
| Injected rest tick | 25 Hz, amp 0.05 | LX HF RMS rises at rest |

Thumbs off. Filename contains `INJECTED_`. JSON has `noiseInjected: true` and `protocol.injection`. Do not treat these files as a pad screen.

If HF sine does not raise `lx_high_freq_rms`, the RC wiring is broken. If slow sine raises it a lot, tau is wrong.

## Pair delta

`unchanged` / `increased_at_rest` / `increased_high_freq` / `unsupported`. Change detector only. Not organic vs non-organic.

## RC filter

`tau = 0.05 s`. HF RMS = RMS(raw - trend). `reportVersion` 0.2.
