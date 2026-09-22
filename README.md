# RCM Tool

Tournament controller integrity and telemetry tool for Windows.

RCM Tool **reads** controller axes. It does **not** write to the controller, does **not** inject stick motion into a game, and does **not** load scripts onto a dongle.

Repository: https://github.com/SensoredRooster/rcm-tool

## Update an existing clone

```powershell
git pull
python apply_rc_wiring.py
python apply_ui_pass.py
python apply_auto_export.py
python apply_pair_delta.py
python -m unittest discover -v
python rcm_tool.py
```

Or **RCM Tool - Update and Start.bat** (runs all four apply scripts).

## What a finished test does

A single Neutral or Guided test writes `reports\*.json` by itself. Pair mode writes Before, After, and `*_before_after_comparison.json`.

Then **RCM Tool - Submit Reports.bat**, type `YES`, tell Grok the filename.

## Pair delta (Before + After)

The comparison JSON includes `pairDelta`. Labels:

| pair_delta | Meaning |
|---|---|
| `unchanged` | After HF RMS stayed inside the pair floor |
| `increased_at_rest` | Neutral After was louder with sticks untouched (floor 0.003) |
| `increased_high_freq` | Guided After added HF energy on a slow path (floor 0.005) |
| `unsupported` | Bad capture or mixed protocols |

This is a **change detector**. The JSON field `not_a_verdict` says it is not organic vs non-organic. Repeat the pair three times before you treat the sign as a pattern.

Run a Neutral pair and a Guided pair separately if you want both questions answered.

Implementation: `pair_delta.py`. Wiring: `apply_pair_delta.py`.

## RC filter

`tau = 0.05 s`. `alpha = 1 - exp(-dt / tau)`. HF RMS = RMS of raw minus RC trend. `reportVersion` must be `0.2`.

## Classification screens (single test)

Unsupported: no samples or rate under 20 Hz.

Guided review: jitter_rms >= 0.015 or jitter peak-to-peak >= 0.06.

Neutral review: active % >= 5, rms >= 0.015, or crossings >= 10.

## Files

`rcm_tool.py`, `controller_integrity.py` (unwired on GitHub; apply scripts patch it locally), `rc_filter.py`, `pair_delta.py`, `apply_rc_wiring.py`, `apply_ui_pass.py`, `apply_auto_export.py`, `apply_pair_delta.py`, `test_controller_integrity.py`, `test_pair_delta.py`.
