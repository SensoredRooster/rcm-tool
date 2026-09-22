# RCM Tool

Tournament controller integrity and telemetry tool for Windows.

RCM Tool **reads** controller axes. It does **not** write to the controller, does **not** inject stick motion into a game, and does **not** load scripts onto a dongle. Every number in a report is computed from the sample stream that Windows (or Raw HID) handed the app during that capture.

Repository: https://github.com/SensoredRooster/rcm-tool

## 0. What this tool is and is not

| It is | It is not |
|---|---|
| A capture + screening app | An aim-assist detector that outputs cheating: yes |
| A way to compare Neutral vs Guided, Before vs After | A firmware flasher |
| A hash-linked JSON exporter | A game overlay |
| Operator-driven | Automatic proof of intent |

PASS / REVIEW / UNSUPPORTED are screening labels. A human still decides what they mean for a specific pad, backend, and baseline.

## 1. Machine requirements

- Windows 10 or Windows 11. First-party backends are XInput (`xinput1_4.dll` / `xinput9_1_0.dll` / `xinput1_3.dll`) and WinMM DirectInput (`winmm.dll`).
- Python 3.10 or newer, 64-bit, with Add Python to PATH checked at install. https://www.python.org/downloads/
- Git, if you use the `.bat` update/submit flow. https://git-scm.com/download/win
- Packages in `requirements.txt`: `pygame-ce>=2.5,<3` (imported as `pygame`) and `hidapi>=0.14,<1` (imported as `hid`).
- A writable folder. Reports go to `reports\` next to the scripts. Do not run from a read-only ZIP preview.

Always-used stdlib: `ctypes`, `hashlib`, `json`, `math`, `os`, `platform`, `re`, `statistics`, `threading`, `time`, `tkinter`, `dataclasses`, `datetime`, `pathlib`.

If `tkinter` is missing, the Windows Python installer was customized without Tcl/Tk. Re-run the official installer, choose Modify, enable tcl/tk and IDLE.

## 2. Files and what each one does

| File | Role |
|---|---|
| `rcm_tool.py` | Entry point. Calls `controller_integrity.main()`. |
| `controller_integrity.py` | UI, backends, capture, classification, JSON export. On GitHub this file starts unwired. Local apply scripts patch it. |
| `rc_filter.py` | First-order RC low-pass and `build_rc_filter_metrics`. |
| `apply_rc_wiring.py` | RC filter into capture and JSON. Safe to re-run. |
| `apply_ui_pass.py` | Progress bar, HF RMS column, live reads paused during capture. Run after RC wiring. |
| `apply_auto_export.py` | Writes the JSON as soon as a single test finishes. You do not click Export. Run after the UI pass. |
| `test_controller_integrity.py` | Unit tests. |
| `requirements.txt` | pygame-ce and hidapi pins. |
| `RCM Tool - Update and Start.bat` | `git pull`, all three apply scripts, `pip install`, start. |
| `RCM Tool - Submit Reports.bat` | Shows pending files in `reports\`, requires `YES`, commits only that folder, pushes `origin master`. |
| `HARDWARE_MEASUREMENTS.md` | External lab protocol. Not required to run the app. |
| `reports\` | Auto-written JSON and `index.jsonl`. |
| `.gitignore` | Ignores `__pycache__/`, `*.py[cod]`, `.venv/`. Report JSON is not ignored. |

## 3. Install from a Git clone (preferred)

```powershell
git clone https://github.com/SensoredRooster/rcm-tool.git
cd rcm-tool
python -m pip install -r requirements.txt
python apply_rc_wiring.py
python apply_ui_pass.py
python apply_auto_export.py
python -m unittest discover -v
python rcm_tool.py
```

Or double-click **RCM Tool - Update and Start.bat**.

If apply prints `could not find block`:

```powershell
git checkout -- controller_integrity.py
python apply_rc_wiring.py
python apply_ui_pass.py
python apply_auto_export.py
```

## 4. Install from Download ZIP

```powershell
python -m pip install -r .\requirements.txt
python apply_rc_wiring.py
python apply_ui_pass.py
python apply_auto_export.py
python .\rcm_tool.py
```

For Grok review use a git clone so Submit Reports can push JSON.

## 5. Wiring check (do this once)

`controller_integrity.py` must contain:

- `from rc_filter import`
- `REPORT_VERSION = "0.2"`
- `trend_filter = RCLowPassFilter`
- `rc_filter_metrics=`
- `self.progress = ttk.Progressbar`
- `testing = bool(self.test_thread`
- `saved = self.write_report(result, show_message=False)`

## 6. Window map

Default size after the UI pass: **980x740**, minimum **900x660**.

**Section 1** Detect devices, pick the connected source whose live bars move.

**Section 2:** Neutral = 10.0 s. Guided = 20.0 s. Progress bar is UI only.

**Start single test** captures and **writes `reports\*.json` by itself** when the timer ends. Status line shows `Test complete. Saved <filename>`.

**Run Before + After pair** already auto-saved both phases and the comparison file.

An Export button may still be visible from older UI wiring. You do not need it. Do not click it unless you want a second copy of the same result.

While capture runs: Start, Pair, Phase, Protocol, Source, and Detect are locked.

**Section 3** live axes: -1.0 to +1.0. During capture the UI does not call `backend.read()`.

If live numbers stay `+0.0000`, do not start a test.

**Section 4** table: Signal RMS, Jitter RMS, HF RMS, peak-to-peak, threshold crossings, active %.

## 7. Neutral hold

1. Detect devices, select connected source, wiggle all four axes.
2. Protocol = Neutral hold. Thumbs off.
3. Start single test. Wait. File is already in `reports\`.

`UNSUPPORTED` means no samples or rate under 20 Hz. Do not interpret RMS.

## 8. Guided movement

Same source as Neutral if you will compare. Slow sticks only. File writes when 20 s ends.

Shaking the stick manufactures a REVIEW.

## 9. Before + After

Same protocol both sides. Pair button. Change only the thing under test between phases. Comparison JSON includes `highFreqRmsDelta` when both phases are usable.

## 10. Sampling, filter, JSON

`POLL_INTERVAL_SECONDS = 0.004`. Classification requires >= 20 Hz.

RC: `alpha = 1 - exp(-dt / tau)`, `tau = 0.05 s`, cutoff about 3.18 Hz. Residual = raw - filtered. `lx_high_freq_rms` is RMS of that residual.

`reportVersion` must be `0.2`. `sha256` is hashed before that field is inserted.

## 11. Classification thresholds

Unsupported: no samples or `sample_rate_hz < 20`.

Guided review: `jitter_rms >= 0.015` or `jitter_peak_to_peak >= 0.06`.

Neutral review: `nonzero_stationary_percent >= 5.0` or `rms >= 0.015` or `deadzone_crossings >= 10`.

## 12. Noise vs extra high-frequency energy

You cannot prove intent from one RMS. Compare Neutral vs Guided on the same pad, PC, and backend. Repeat three times.

Motion slower than about 3 Hz stays in the RC trend and will not inflate HF RMS.

## 13. After the file is written

Status line names the file. Open reports folder if you want to look. Then **RCM Tool - Submit Reports.bat**, type `YES`, tell Grok the filename.

## 14. Troubleshooting

- No JSON after a test: `python apply_auto_export.py`
- `reportVersion` 0.1: `python apply_rc_wiring.py` then capture again
- apply `could not find block`: checkout `controller_integrity.py` and run all three apply scripts in order
- Live zeros: wrong dropdown. Try SDL, Raw HID, then the XInput slot `joy.cpl` uses
- Window never opens: repair Tcl/Tk

Win+R, `joy.cpl`. If axes do not move there, RCM Tool cannot invent them.

## 15. Tests

```powershell
python -m unittest discover -v
```

## 16. Scope limits

Windows first. Reports may contain product strings, VID/PID, timestamps, and full sample arrays.
