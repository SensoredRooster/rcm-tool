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
| `apply_rc_wiring.py` | Patches `controller_integrity.py` so capture stores filtered samples and reports include `rc_filter_metrics`. Safe to re-run. |
| `apply_ui_pass.py` | Second patch: progress bar, HF RMS column, export button re-enabled, live HID reads paused during capture. Run after `apply_rc_wiring.py`. Safe to re-run. |
| `test_controller_integrity.py` | Unit tests for classification helpers, HID parse, RC math. |
| `requirements.txt` | pygame-ce and hidapi pins. |
| `RCM Tool - Update and Start.bat` | `git pull`, both apply scripts, `pip install`, `python rcm_tool.py`. |
| `RCM Tool - Submit Reports.bat` | Shows `git status --short reports`, requires `YES`, commits only `reports\`, pushes `origin master`. |
| `HARDWARE_MEASUREMENTS.md` | Lab protocol for external sensor / USB analyzer work. Not required to run the app. |
| `reports\` | JSON outputs and `index.jsonl`. |
| `.gitignore` | Ignores `__pycache__/`, `*.py[cod]`, `.venv/`. Report JSON is not ignored. |

## 3. Install from a Git clone (preferred)

```powershell
git clone https://github.com/SensoredRooster/rcm-tool.git
cd rcm-tool
python -m pip install -r requirements.txt
python apply_rc_wiring.py
python apply_ui_pass.py
python -m unittest discover -v
python rcm_tool.py
```

Later sessions: same apply lines (they skip if already applied) or double-click **RCM Tool - Update and Start.bat**.

If apply prints `could not find block`, your `controller_integrity.py` does not match the script. Run:

```powershell
git checkout -- controller_integrity.py
python apply_rc_wiring.py
python apply_ui_pass.py
```

## 4. Install from Download ZIP

1. GitHub to Code to Download ZIP.
2. Extract to a writable folder such as `C:\RCM Tool`.
3. Install Python 3.10+ with PATH.
4. In that folder:

```powershell
python -m pip install -r .\requirements.txt
python apply_rc_wiring.py
python apply_ui_pass.py
python .\rcm_tool.py
```

ZIP users do not get `git pull` or Submit Reports unless they later clone or add the remote. For Grok review, clone + submit bat puts JSON on GitHub.

## 5. Wiring check (do this once)

After apply, `controller_integrity.py` must contain all of:

- `from rc_filter import`
- `REPORT_VERSION = "0.2"`
- `trend_filter = RCLowPassFilter`
- `rc_filter_metrics=`
- `self.progress = ttk.Progressbar`
- `testing = bool(self.test_thread`

If `REPORT_VERSION` is still `"0.1"`, reports will not have `rc_filter_metrics`.

## 6. Window map

Default size after the UI pass: **980x740**, minimum **900x660**.

**Section 1** dropdown + **Detect devices** rebuilds sources by probing XInput slots 0-3, WinMM devices, SDL joysticks, and HID devices whose usage page/usage or name looks like a gamepad.

| Entry | Meaning |
|---|---|
| Automatic | Tries XInput, SDL, Raw HID, then DirectInput on every `read()`. |
| XInput slot N | Official Xbox-compatible slot. |
| DirectInput device N | WinMM `joyGetPosEx`. Only listed if that id returned a packet during detect. |
| SDL device N | pygame-ce joystick. |
| Raw HID | hidapi path + DualSense-aware parser with generic 8-bit axis fallback. |

Select the connected line whose live bars move when you move that pad. With two pads, do not use Automatic.

**Section 2:** Neutral duration is **10.0 s**. Guided is **20.0 s**. During a run the UI shows remaining seconds and a progress bar. The bar is UI only.

Phase labels the file and JSON. It does not change math.

**Start single test** = one capture; you Export yourself. **Pair** = Before auto-save, prompt, After auto-save, comparison JSON if both usable.

Export is disabled until a test finishes (UI pass fix). While capture runs, Start, Pair, Phase, Protocol, Source, Detect, and Export are locked.

**Section 3** live axes: -1.0 to +1.0. While capture runs, live UI does **not** call `backend.read()`. Capture owns the device. After the test, live reads resume at 80 ms.

If live numbers stay `+0.0000`, do not start a test.

Axes seen lists axes that crossed +/-0.05 since the last source change.

**Section 4** table after a test:

- Signal RMS = RMS of raw axis
- Jitter RMS = RMS of raw minus RC trend
- HF RMS = `rc_filter_metrics` axis `high_freq_rms`
- Peak-to-peak = max raw minus min raw
- Threshold crossings = crossings of +/-0.02
- Active % = percent of samples with `abs(x) > 0.02`

## 7. Neutral hold

1. One controller. Close or deliberately keep remappers; record which.
2. Detect devices, select connected source, wiggle all four axes.
3. Protocol = Neutral hold. Thumbs off sticks.
4. Start single test. Do not bump the desk.
5. Export to reports.

`UNSUPPORTED` means no samples or sample rate under 20 Hz. Do not interpret RMS.

## 8. Guided movement

1. Same source as Neutral if you will compare.
2. Protocol = Guided movement (20 s).
3. Confirm the dialog. Slow left stick left/right then up/down, then right stick. No shake.
4. Export.

If you shake the stick you manufacture a REVIEW. That is operator error.

## 9. Before + After

Same protocol both sides. The app does not change the controller.

1. Pair button starts Before.
2. Change only the thing under test.
3. OK. After runs. Comparison JSON includes `highFreqRmsDelta` when both phases are usable.

## 10. Sampling, filter, JSON

`POLL_INTERVAL_SECONDS = 0.004`. Achieved rate is `samples / elapsed`. Classification requires >= 20 Hz.

RC filter in `rc_filter.py`:

- `alpha = 1 - exp(-dt / tau)`
- `smoothed[n] = alpha * raw[n] + (1 - alpha) * smoothed[n-1]`
- `tau = 0.05 s`
- `cutoff_hz = 1 / (2 * pi * tau)` approximately 3.18 Hz

`dt` is the real gap between accepted samples.

Residual = raw - filtered. `lx_high_freq_rms` is RMS of that residual on LX.

After wiring each sample has `t_ms`, `lx`, `ly`, `rx`, `ry`, and `lx_filtered` through `ry_filtered`.

Report fields for review: `reportVersion` 0.2, `protocol.jitterEstimator`, `protocol.rcFilterTauSeconds` 0.05, `protocol.inputInjected` false, `protocol.noiseInjected` false, `rc_filter_metrics`, `result.classification`, `result.review_reasons`, `result.sample_rate_hz`, `result.samples`, `result.hid_reports`, `sha256`.

`sha256` is the hash of the payload before the `sha256` field is inserted. Do not edit a report and keep the old hash.

## 11. Classification thresholds

Any protocol: `samples == 0` on an axis or `sample_rate_hz < 20` -> `unsupported`.

Guided: `jitter_rms >= 0.015` or `jitter_peak_to_peak >= 0.06` on an axis -> `review`, else `pass`.

Neutral: `nonzero_stationary_percent >= 5.0` or `rms >= 0.015` or `deadzone_crossings >= 10` -> `review`, else `pass`.

These are initial screens, not a published tournament law. Compare the same pad, PC, and backend.

## 12. Noise vs extra high-frequency energy

You cannot prove intent from one RMS.

Minimum set to say extra high-frequency energy showed up:

1. Neutral HF RMS matches other runs of that pad on that backend.
2. Guided path was slow.
3. Guided LX/LY HF RMS is repeatably much larger than Neutral on the same source.
4. RX/RY stay near Neutral if only the aim stick was supposed to move.
5. Three repeats. One spike is a bump.

If only one backend is loud, suspect that path, not the Hall sensor.

Motion slower than about 3 Hz sits inside the RC trend and will not inflate HF RMS. Do not mix different `tau` values in one comparison.

## 13. Export and submit

Files: `reports\<UTC>_<phase>_<source-slug>.json` plus a line in `reports\index.jsonl`.

Submit bat: prints `git status --short reports`, you type `YES`, `git add reports` only, commit message `Add controller test reports`, `git push origin master`. If `user.name` / `user.email` are missing it prompts.

Then tell Grok the filename.

If push fails the commit is still local. Fix GitHub auth. Do not re-run apply scripts for a push failure.

## 14. Troubleshooting

- Window never opens: `python -c "import tkinter"` then repair Tcl/Tk.
- `No module named pygame`: `pip install -r requirements.txt`.
- Detect list empty: `joy.cpl`, USB direct, Xbox mode, Detect again.
- Live zeros: wrong dropdown row. Try SDL, Raw HID, then the XInput slot `joy.cpl` uses.
- Guided REVIEW after shaking: re-run slowly, discard the shaken file.
- `reportVersion` 0.1: `python apply_rc_wiring.py`.
- Export button dead after a test: `python apply_ui_pass.py`.
- apply `could not find block`: `git checkout -- controller_integrity.py` then apply RC then UI.
- `git pull` rejected: stash or commit reports first.
- DualSense weird axes: Raw HID. Parser offsets Sony report ids `0x31` and `0x11`.

Win+R, `joy.cpl`, Properties. If axes do not move there, RCM Tool cannot invent them.

## 15. Tests

```powershell
python -m unittest discover -v
```

No controller required. Working directory must be the repo folder.

## 16. Scope limits

Windows first. `main()` warns on other OS; XInput/WinMM will not load.

Axis maps are not universal. A fightstick slider can appear as RY.

Bluetooth vs USB changes sample rate and HF RMS. Record the connection in the filename.

Reports may contain product strings, VID/PID, timestamps, and full sample arrays.

Not in this tree: signed envelopes, server ingest, per-vendor calibration UI, DualSense IMU metadata.
