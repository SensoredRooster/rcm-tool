# RCM Tool

An early tournament-certification prototype for measuring controller neutral-stick behavior. RCM Tool captures controller telemetry without injecting input into a game, runs a fixed neutral-stick test, and exports a JSON report with a SHA-256 payload hash.

## Run

Windows with Python 3.10+:

```powershell
python .\controller_integrity.py
```

The first version reads Windows XInput, scans all four XInput controller slots, and falls back to the Windows DirectInput joystick API for generic gamepads. Keep both sticks untouched during the 10-second test, then export the report. The app displays whether a supported backend is unavailable, no controller is detected, or a controller is connected.

If the app says no controller is detected, put the controller into Xbox/XInput mode, reconnect it over USB, or confirm it appears in Windows' game-controller panel. Vendor-specific HID devices may still need a dedicated backend.

## Scope and interpretation

The initial classification is a conservative screening signal, not proof of misconduct. Stick noise varies by controller, firmware, wear, temperature, and connection mode. A tournament deployment should establish controller-specific baselines, retain the raw sample stream or a signed digest, and require human review before any sanction.

The capture layer is intentionally separated from the metrics and reporting code so Raw HID, SDL, DualSense, and other backends can be added without rewriting the test logic.

## Planned next steps

1. Add Raw HID capture and controller metadata.
2. Add a guided calibration profile with repeated neutral, slow-sweep, and endpoint tests.
3. Add CSV/raw sample export and a signed report envelope.
4. Add a server verifier and tournament policy configuration.
5. Add controller-specific baselines and replayable fixtures for automated tests.
