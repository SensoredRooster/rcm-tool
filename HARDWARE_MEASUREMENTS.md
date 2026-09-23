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

## Noise attribution evidence

The modern Controller Lab provides two hardware-only evidence captures under
**Noise Attribution Evidence**:

1. **Neutral noise**: leave every stick untouched for 10 seconds. This reports
   stationary RMS noise, peak-to-peak range, distinct output levels, adjacent
   report changes, and consecutive duplicate Raw HID payloads.
2. **Movement / settling**: move one stick center → full deflection → center,
   then repeat with a quick reversal for 20 seconds. This reports the same
   host-observed stream metrics plus the residual from a documented slow trend.

The test forces display smoothing to one sample and uses stored acquisition
samples. The display moving average and the slow-trend residual are analysis
layers; neither is sent to the controller or applied to stored input.

These captures cannot identify firmware filtering by themselves. Raw HID is
already downstream of the controller firmware and USB stack. To attribute a
low-pass response to firmware, capture the stick sensor/electrical signal with
an oscilloscope or logic analyzer at the same time and compare its step response
and noise spectrum with the exported Raw HID evidence. Without that upstream
trace, report only a host-observed smoothing signature, not a firmware claim.

## Validation matrix

Use this matrix when making hardware-specific performance claims. Configured
reference values are analysis settings only; they are not evidence that a
physical controller achieves the same rate.

| Measurement path | Evidence class | What can be claimed | Required record |
|---|---|---|---|
| Raw HID | Measured host-observed | Arrival cadence for the named device, USB path, and Windows host | Controller model, VID/PID, firmware, HID backend, host, capture/export |
| XInput / SDL / WinMM | Host-poll estimate | API-observed timing only; not controller bus timing | API/backend, polling configuration, host, capture/export |
| VISA counter/scope/analyzer | Instrument-measured | Values reported by the connected instrument and its command dialect | Instrument model, resource, identity, timeout, command set |
| Generator stimulus response | Experiment-specific | Observed response under the recorded source settings and safety limits | Generator identity, waveform, frequency/amplitude/offset, readback, DUT setup |

The application should label reports with the evidence class above. Do not
generalize a single-device Raw HID result into a controller-family or USB-host
guarantee without additional captures.

Guided movement estimates high-frequency jitter as the residual after a first-order RC low-pass (`tau = 0.05 s`, `alpha = 1 - exp(-dt / tau)`). The comparison report records Before-to-After `highFreqRmsDelta` as well as the older `jitterRmsDelta` field (now the same RC residual).

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
