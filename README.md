# RCM Tool

RCM Tool is a Windows controller-certification prototype for tournament use. It measures neutral-stick behavior without injecting input into a game, then exports a JSON screening report.

It is designed to answer one basic question first: **what controller input source is Windows exposing, and is that source returning data?**

## Update an existing clone

From the `rcm-tool` folder:

```powershell
git pull
python apply_rc_wiring.py
python -m unittest discover -v
python rcm_tool.py
```

`apply_rc_wiring.py` is safe to re-run. After the first successful apply, later runs print `skip ... already applied`.

**RCM Tool - Update and Start.bat** now runs `git pull` and `apply_rc_wiring.py` before launching the app.

## Download and run from the GitHub ZIP

1. Open the [RCM Tool repository](https://github.com/SensoredRooster/rcm-tool).
2. Select **Code → Download ZIP**.
3. Extract the ZIP to a normal folder, such as `C:\\RCM Tool`.
4. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/). During installation, enable **Add Python to PATH**.
5. Open PowerShell in the extracted folder.
6. Install the broad SDL controller backend:

```powershell
python -m pip install -r .\\requirements.txt
python apply_rc_wiring.py
```

7. Run:

```powershell
python .\\rcm_tool.py
```

For a double-click workflow, use **RCM Tool - Update and Start.bat**. After testing, use **RCM Tool - Submit Reports.bat**. The submit file shows the pending report files and asks for confirmation before committing and pushing them to GitHub.

The SDL and Raw HID dependencies are required for broad controller coverage. The built-in XInput and DirectInput readers remain available if they are not installed. The packages are `pygame-ce` and `hidapi`; the application imports them through the `pygame` and `hid` module names.

See [HARDWARE_MEASUREMENTS.md](HARDWARE_MEASUREMENTS.md) for the before/after raw sensor and USB HID measurement plan.

## Connect and select the controller

1. Connect the controller by USB or pair it over Bluetooth.
2. Put it into the mode expected by Windows. Xbox mode is usually called **XInput**. Other devices may use **DirectInput**, **DInput**, or a vendor mode.
3. Start RCM Tool.
4. At the top of the window, use **Detect devices**.
5. Open the input-source list. It will show:
   - `Automatic` — scans XInput first, then DirectInput.
   - `XInput slot 0` through `XInput slot 3` — Windows Xbox-compatible slots.
   - `DirectInput device N` — generic Windows joystick devices that responded during detection.
   - `SDL device N` — SDL's broad controller layer, including many USB, Bluetooth, Xbox, PlayStation, Switch, arcade, and third-party devices.
   - `Raw HID - ...` — direct USB/Bluetooth HID discovery, including controller variants that do not register cleanly through DirectInput or SDL.
6. Select the entry marked **connected**.
7. Move each stick. The `LX`, `LY`, `RX`, and `RY` values should change. If they stay at zero, the selected source is not the source receiving your controller input.

The status line tells you exactly what RCM Tool is reading, for example `XInput slot 1 is connected` or `DirectInput device 0 is connected`.

## Run a single test

1. Select the connected input source.
2. Leave both sticks untouched.
3. Click **Start neutral test**.
4. Do not touch the controller for the 10-second test.
5. Review the RMS noise, peak-to-peak movement, threshold crossings, and stationary activity percentage.
6. Click **Export to reports** to save the JSON report and captured timestamped samples in the repository's `reports` folder.

`PASS` means the initial screening thresholds were not exceeded. `REVIEW` means one or more measurements exceeded those thresholds. `UNSUPPORTED` means the test did not capture a complete or fast-enough sample stream. These are screening results, not automatic proof of misconduct.

## Run the paired Before + After test

Use **Run Before + After pair** when comparing a default controller state with a modified state:

1. Select the controller input source and verify the live axes.
2. Leave the phase set to **Before - default**.
3. Click **Run Before + After pair**.
4. RCM Tool runs the selected protocol and automatically saves the Before report.
5. Apply the controller setting or hardware change being evaluated.
6. Click **OK** in the prompt. RCM Tool automatically runs the After phase.
7. RCM Tool saves the After report and creates a `before_after_comparison.json` report only when both phases return usable data.

The Before and After phases use the same duration, sampling loop, thresholds, protocol, and input source. RCM Tool does **not** add noise, inject input, or modify the controller between phases. The only intended difference is the operator-applied controller configuration or hardware state.

For **Guided movement**, `Jitter RMS` is the RMS of `raw - RC_filtered` after a first-order digital RC low-pass (`tau = 0.05 s`, cutoff ≈ 3.18 Hz). The filter is frame-rate independent: `alpha = 1 - exp(-dt / tau)`. Implementation is in `rc_filter.py`. After wiring, each exported sample includes `lx_filtered` … `ry_filtered`, and the report has an `rc_filter_metrics` block with `lx_high_freq_rms` through `ry_high_freq_rms`. That is a measurement of the captured stream, not proof of where the jitter originated.

## If the app says no controller is detected

First confirm that Windows itself sees the device:

1. Press `Win + R`.
2. Enter `joy.cpl`.
3. Press Enter.
4. Confirm the controller appears and open **Properties** to see whether its axes move.

If the controller appears in `joy.cpl` but not in RCM Tool:

- Try the controller's Xbox/XInput mode if it has one.
- Reconnect it directly by USB instead of through a hub.
- For Bluetooth, remove and pair the controller again.
- Click **Detect devices** after reconnecting.
- Select the specific XInput slot or DirectInput device instead of Automatic.
- Close software that may exclusively claim the controller, such as remappers or virtual-controller tools.

If it works in `joy.cpl` but still does not appear in the list, it may use a vendor-specific HID protocol or a driver that hides the device from user-mode APIs. Save the controller model and connection mode so a device-specific mapping can be added.

## Development checks

Run the automated tests from the project folder:

```powershell
python -m unittest discover -v
```

## Scope and interpretation

Stick noise varies with controller model, firmware, wear, temperature, deadzone configuration, and connection mode. Tournament deployment should establish controller-specific baselines, retain the raw sample stream or a signed digest, and require human review before any sanction.

The current app supports Windows XInput, the legacy Windows DirectInput joystick API, SDL, and Raw HID discovery with a DualSense-compatible parser and generic fallback. Vendor-specific axis mappings, DualSense metadata, guided calibration, server verification, and signed report envelopes are planned additions. No universal API can guarantee identical mappings for every controller, so unsupported devices are reported rather than silently misclassified.
