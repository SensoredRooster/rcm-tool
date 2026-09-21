# Hardware measurement plan

RCM Tool can measure two separate layers:

```text
stick sensor electrical output -> oscilloscope
                                      |
controller firmware/USB stack -> raw USB HID reports -> RCM Tool
```

## Before/after test

In RCM Tool, use **Run Before + After pair**. It runs both captures with the same test conditions and pauses only so the operator can apply the change:

1. **Before**: controller in its factory/default configuration.
2. **After**: controller in the configuration being evaluated.
3. Leave the stick untouched for the neutral capture.
4. Repeat the same slow sweep and endpoint movement for both captures.
5. Record controller model, connection mode, VID/PID, firmware/configuration, probe channel, scope settings, and timestamps.

RCM Tool does not generate noise or alter the controller signal in either phase. It measures the controller as found in the Before state and then as found in the After state.

RCM Tool records normalized samples and, when the Raw HID source is selected, timestamped HID reports in the exported JSON. The HID report stream is useful for checking report cadence, report IDs, raw byte changes, and the relationship between analog movement and USB output.

## Oscilloscope capture

The scope side should record at least:

- center voltage at rest;
- minimum and maximum sensor voltage;
- peak-to-peak noise at rest;
- RMS noise at rest;
- dominant noise frequency, if measurable;
- response time from physical movement to voltage change;
- the same values after the configuration change.

Use a high-impedance probe. Verify the scope ground/reference before connecting it to a controller, and use a differential probe when the measured node is not safely ground-referenced. Do not short sensor, battery, or USB power lines while probing a custom controller.

The oscilloscope driver is intentionally not hard-coded yet. The next implementation needs the scope manufacturer/model, connection method (USB, LAN, or serial), and whether it exports CSV, waveform files, or SCPI readings.
