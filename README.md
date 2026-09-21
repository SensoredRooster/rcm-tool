# RCM Tool

RCM Tool is a Windows controller-certification prototype for tournament use. It measures neutral-stick behavior without injecting input into a game, then exports a JSON screening report.

It is designed to answer one basic question first: **what controller input source is Windows exposing, and is that source returning data?**

## Download and run from the GitHub ZIP

1. Open the [RCM Tool repository](https://github.com/SensoredRooster/rcm-tool).
2. Select **Code → Download ZIP**.
3. Extract the ZIP to a normal folder, such as `C:\RCM Tool`.
4. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/). During installation, enable **Add Python to PATH**.
5. Open PowerShell in the extracted folder.
6. Run:

```powershell
python .\rcm_tool.py
```

The app uses only Python's standard library in this prototype; no `pip install` step is required.

## Connect and select the controller

1. Connect the controller by USB or pair it over Bluetooth.
2. Put it into the mode expected by Windows. Xbox mode is usually called **XInput**. Other devices may use **DirectInput**, **DInput**, or a vendor mode.
3. Start RCM Tool.
4. At the top of the window, use **Detect devices**.
5. Open the input-source list. It will show:
   - `Automatic` — scans XInput first, then DirectInput.
   - `XInput slot 0` through `XInput slot 3` — Windows Xbox-compatible slots.
   - `DirectInput device N` — generic Windows joystick devices that responded during detection.
6. Select the entry marked **connected**.
7. Move each stick. The `LX`, `LY`, `RX`, and `RY` values should change. If they stay at zero, the selected source is not the source receiving your controller input.

The status line tells you exactly what RCM Tool is reading, for example `XInput slot 1 is connected` or `DirectInput device 0 is connected`.

## Run a test

1. Select the connected input source.
2. Leave both sticks untouched.
3. Click **Start neutral test**.
4. Do not touch the controller for the 10-second test.
5. Review the RMS noise, peak-to-peak movement, threshold crossings, and stationary activity percentage.
6. Click **Export report** to save the JSON report.

`PASS` means the initial screening thresholds were not exceeded. `REVIEW` means one or more measurements exceeded those thresholds. `UNSUPPORTED` means the test did not capture a complete controller sample. These are screening results, not automatic proof of misconduct.

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

If it works in `joy.cpl` but still does not appear in either list, it likely uses a vendor-specific HID protocol. That requires the next capture backend; the current prototype does not claim to support every controller.

## Development checks

Run the automated tests from the project folder:

```powershell
python -m unittest discover -v
```

## Scope and interpretation

Stick noise varies with controller model, firmware, wear, temperature, deadzone configuration, and connection mode. Tournament deployment should establish controller-specific baselines, retain the raw sample stream or a signed digest, and require human review before any sanction.

The current app supports Windows XInput and the legacy Windows DirectInput joystick API. Raw HID, SDL, DualSense metadata, guided calibration, server verification, and signed report envelopes are planned additions.
