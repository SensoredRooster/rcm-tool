# RcmTool

RcmTool is the next-generation desktop application built on the original **RCM Tool** controller measurement code. It is a Windows laboratory workspace for measuring controller report timing, analog-input stability, oscillator/clock stability, controlled bench stimulus, sweep response, and synchronized timing relationships.

The application is measurement-focused. It does not inject game inputs, modify controller firmware, or manufacture precision that the connected hardware cannot provide.

## Current integrated capabilities

- Modern PySide6/Qt desktop UI with rounded panels, dark/light modes, high-DPI scaling, dashboard cards, live plots, and dedicated lab pages.
- Existing RCM controller acquisition through Windows XInput, SDL/pygame, Raw HID/hidapi, and WinMM fallback.
- Controller timing analysis: effective report rate, interval statistics, RMS timing deviation, peak-to-peak jitter, successive interval variation, P50/P90/P95/P99/P99.9, late reports, and estimated missing reports.
- Controller Lab with **Auto / Xbox / DualSense / Generic** visual modes. Auto uses backend + VID/PID + product identity conservatively; Xbox and DualSense remain manually selectable. Manual visual override never changes the trusted input mapping. Live sticks, triggers, button/D-pad state where decoded, and stationary analog-noise inspection remain separate from device/backend diagnostics.
- Live Capture controls for zoom, pan, crosshair inspection, pause-visualization-without-pausing-acquisition, display-only moving-average smoothing, PNG export, fullscreen inspection, raw-data viewing, report-interval histogram, and explicit unavailable labeling for latency when no device-origin timestamp exists.
- Oscillator Lab: frequency, error in Hz and ppm, frequency standard deviation/span, first-to-last-window drift, configurable sigma outlier count, period statistics, RMS period jitter, peak-to-peak jitter, cycle-to-cycle jitter, and one-sample-interval Allan deviation.
- A dedicated read-only VISA/SCPI measurement role for counters, oscilloscopes, and analyzers.
- A separate VISA/SCPI signal-generator role with output-off-by-default behavior.
- Interference Lab with explicit output enable, configurable software limits, and an EMERGENCY OUTPUT OFF control.
- Linear/logarithmic frequency and amplitude sweep planning with repetitions, settling time, dwell time, sequential/randomized ordering, event logging, selectable response heat maps, per-step isolated measurement windows, and forced output-off at completion.
- Synchronized controller and oscillator storage with descriptive correlation, shared cross-chart cursors, user markers, and timeline-event cursor positioning.
- SQLite/WAL session storage, experiment history/timeline, JSON export, controller CSV export, saved baseline JSON, saved-session vs saved-session comparison, live-reference comparison, and self-contained HTML engineering reports containing plots, sweep response, timeline, metadata, and limitations.
- Deterministic gamepad/oscillator simulation with configurable rate, random/periodic jitter, spikes, missing-report cadence, oscillator offset/jitter/drift, plus known stimulus-response relationships for validating sweep/correlation behavior.
- Automated tests plus Windows CI validation for unit tests, Qt desktop launch, full simulation workflow, portable build, standalone one-file EXE, and Inno Setup installer.

## Measurement integrity

Controller timestamps use Python's highest-resolution host monotonic clock available through time.perf_counter_ns. The meaning of those timestamps depends on the backend:

- **Raw HID** reports are timestamped when the application receives/drains the HID report. This is still a host-observed timestamp.
- **XInput, SDL, and WinMM** are host-polling APIs. Their timing includes operating-system scheduling and API buffering effects.
- Dedicated oscilloscopes, counters, logic analyzers, USB analyzers, or timing instruments are required when direct electrical or bus-level timing is needed.

The application labels data as simulated, measured, calculated, estimated, or unavailable rather than inventing unsupported values.

Dashboard cards, oscillator readouts, comparison metrics, and graphs include short hover definitions describing what each value means and whether it is measured or derived. Controller timing uses the measured median report interval as the default jitter/late-report reference; a configured reference rate can be selected explicitly in Settings. Time-series plots use elapsed timestamps rather than treating sample number as time.

Analog stick noise is only labeled as a stationary noise floor when all four stick axes remain within the configured stationary-excursion threshold for the analysis window. If movement exceeds that threshold, the noise-floor result is withheld instead of reporting motion as noise.

Generic VISA/SCPI measurement capability is probed at connection time. Directly unsupported values remain unavailable; for example, period displayed from frequency samples is explicitly labeled as a calculated reciprocal-period result rather than a direct period measurement.

## Install from source

The supported build target is 64-bit Windows.

~~~powershell
git clone https://github.com/SensoredRooster/rcm-tool.git
cd rcm-tool
python -m pip install -r requirements.txt
python -m unittest discover -v
python gamepad_signal_lab.py
~~~

The compatibility entry point also works:

~~~powershell
python rcm_tool.py
~~~

If PySide6 is not installed, the compatibility entry point falls back to the original Tk RCM capture bench.

## First-run workflow

1. Launch RcmTool.
2. Leave Simulation selected initially.
3. Start Capture and verify the timing and oscillator dashboard.
4. Run a baseline.
5. Review Controller Lab, Oscillator Lab, Correlation, Experiments, Compare, and Reports.
6. For a physical gamepad, choose Real controller under Settings.
7. For physical clock measurement, select a VISA resource in Oscillator Lab and connect it as a measurement instrument.
8. For controlled stimulus, select a VISA resource in Interference Lab and explicitly connect it as a generator. Generator output remains OFF until the user enables it.

## Oscillator and clock measurements

The generic measurement adapter is intentionally read-only. Connecting it does not send generator output commands.

It tries common SCPI frequency queries such as:

~~~text
MEAS:FREQ?
MEASure:FREQuency?
FETCh:FREQuency?
READ:FREQuency?
~~~

It also tries common duty-cycle queries. Instrument command sets differ, so model-specific drivers can be added behind the same adapter interface without rewriting the analysis, database, or UI.

Calculated oscillator metrics include:

- mean, minimum, and maximum frequency
- frequency standard deviation and span
- first-to-last-window frequency drift in Hz and ppm
- configurable sigma-based frequency outlier count
- error in Hz
- error in ppm
- mean period
- period standard deviation
- RMS period jitter
- peak-to-peak period jitter
- cycle-to-cycle RMS and peak
- one-sample-interval Allan deviation when enough samples exist

Software calculations are only as accurate as the values and timing supplied by the physical measurement hardware.

## Controlled stimulus safety

Measurement instruments and signal sources are separate roles.

- Generator output defaults to **OFF**.
- A VISA resource is not accepted as a generator unless an OUTP OFF command succeeds.
- Discovery never turns output on.
- Frequency, amplitude, and offset are validated against configured software limits before enable or sweep.
- When supported, generator state is read back after configuration and at sweep result points; requested values and instrument-reported values are recorded separately.
- The UI exposes **EMERGENCY OUTPUT OFF**.
- Sweep completion forces the source OFF.
- Closing the application attempts to force output OFF and close the source.

Default software limits are conservative starting values; they are not electrical ratings for any particular controller, coupling network, scope, or generator. Use limits appropriate to the actual bench hardware.

## Baselines, sweeps, and correlation

A baseline captures the current controller timing and oscillator statistics as the reference for the active session. It can be saved to JSON, promoted to the active comparison reference, and compared with live measurements including absolute and percentage change.

The sweep engine supports:

- linear or logarithmic frequency spacing
- amplitude grids
- repeated sweeps
- configurable settling and dwell periods
- sequential or deterministic randomized ordering
- isolated per-step measurement windows
- requested-vs-instrument-reported configuration capture
- selectable response heat maps for gamepad jitter, oscillator jitter, polling-rate deviation, clock frequency deviation, late reports, and analog noise
- timestamped experiment events

Correlation is descriptive. A coefficient or visual time alignment can show that values moved together; it does not establish causation. The Correlation page keeps stimulus, oscillator, and controller plots aligned on the same experiment timeline, supports synchronized measurement cursors across charts, allows user markers, and lets timeline-event selection position the common cursor near that event.

## Data storage

RcmTool uses SQLite with WAL journaling. Sessions contain controller samples, oscillator samples, controller backend-specific fields such as button/D-pad state where available, oscillator measurements, and experiment events. Hardware/controller metadata such as VID, PID, HID path, interface, firmware release, battery/power status, and connection method are shown only when the active backend can actually provide them.

Exports include:

- session JSON
- controller CSV
- engineering HTML report

Raw samples/timestamps remain independent of the summary statistics.

## Build the Windows app

Local build:

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
~~~

Portable folder output:

~~~text
dist\GamepadSignalLab\GamepadSignalLab.exe
~~~

Standalone one-file output:

~~~text
dist\standalone\GamepadSignalLab.exe
~~~

Installer definition:

~~~text
installer\GamepadSignalLab.iss
~~~

The GitHub Actions workflow at .github/workflows/build-windows.yml runs the unit tests, an offscreen Qt window smoke test, and a full no-hardware simulation workflow smoke covering capture, baseline, reference selection, sweep, correlation/timeline activity, SQLite persistence, CSV/JSON exports, HTML reporting, and emergency output-off. It then creates a portable folder build, a true one-file standalone RcmTool.exe, and the Inno Setup installer, and uploads all three as workflow artifacts.

## Tests

~~~powershell
python -m unittest discover -v
~~~

The suite covers the legacy RCM measurement code plus timing calculations, configurable late-report detection, ppm/period/jitter calculations, oscillator drift/outlier statistics, deterministic simulation, controller metadata extraction, correlation, SCPI readback and output safety, sweep construction, report generation, saved-session retrieval, and SQLite/CSV/JSON persistence. Windows CI also performs both the offscreen Qt launch smoke and the full simulated laboratory workflow before packaging.

## Project layout

~~~text
gamepad_signal_lab.py
signal_lab/
  analysis.py       timing, jitter, ppm, Allan deviation, correlation
  controller.py     adapter over existing RCM controller backends
  oscillator.py     background physical frequency acquisition
  instruments.py    separate VISA/SCPI measurement and generator roles
  simulation.py     deterministic virtual gamepad and oscillator
  storage.py        SQLite and exports
  sweep.py          reproducible sweep planning
  reporting.py      engineering HTML reports
  ui.py             PySide6 desktop application
controller_integrity.py
installer/GamepadSignalLab.iss
scripts/build_windows.ps1
~~~

## Legacy RCM Tool components

The original controller integrity functionality remains in the repository, including its controller backends, RC-filter analysis, before/after pair comparisons, tester-share/support integration, and prior report format.

## Important limitations

This software has not been electrically calibrated against every oscilloscope, counter, signal generator, controller, USB host controller, or VISA implementation. Generic SCPI commands are a compatibility layer, not a guarantee for every model. Add model-specific adapters and calibration metadata for laboratory claims requiring traceable accuracy.

Host-side gamepad timing can contain USB scheduling, driver/API buffering, Windows scheduling latency, Python runtime effects, and measurement-loop delay. Reports should distinguish those effects from controller-generated timing variation.

## Existing support infrastructure

The existing RCM support and tester-share infrastructure remains in place and is surfaced directly inside the Qt application under **Support & Diagnostics**.

The tester support flow includes:

- rotating JSONL runtime telemetry under `%LOCALAPPDATA%\\RCMTool\\logs`
- session heartbeat and session IDs
- uncaught Python and worker-thread exception logging
- RcmTool experiment/event telemetry mirrored into the support log
- a local health snapshot
- explicit **Create Redacted Bundle**
- explicit-confirm **Send Diagnostics to Developer**
- **Open Logs Folder**
- **Report GitHub Issue**
- **Open Repository**
- **Tester Share**
- token/password/cookie/credential redaction
- the dedicated `rcm-tool-support` Cloudflare Worker and private `rcm-tool-support-logs` R2 bucket

Nothing is uploaded automatically. Support bundles intentionally exclude raw HID captures and raw controller sample streams; the tester explicitly chooses when to create or send diagnostics.

See:

- docs/SUPPORT.md
- docs/TESTER_SHARE.md

The original Cloudflare workflows remain alongside the Windows application build workflow.


## High-rate controller support

RcmTool does not use 1 kHz as a measurement ceiling. The configured-reference and simulation controls accept **1 Hz through 100 kHz**, covering 8 kHz and higher-rate controllers. Effective polling rate is still calculated from observed report timestamps; selecting a reference value never makes the application report that rate unless the captured timing supports it.

At very high physical rates, usable fidelity still depends on the controller, USB transport, backend, Windows scheduling, and timing source. Raw-HID arrival timing is preferred when available; host-poll backends remain labeled as estimates.


## Performance architecture

The live measurement path is designed to keep visualization and persistence work from becoming artificial timing bottlenecks:

- controller timestamps are captured by the acquisition backend before GUI rendering or SQLite persistence
- raw controller/oscillator rows are buffered and persisted in batched SQLite `executemany()` writes instead of one SQL statement per report
- buffered rows are force-flushed at capture stop, export, session reads, and application close
- hidden pages do not rebuild graph datasets or schedule chart repaints; navigating to a live page triggers an immediate refresh
- controller metadata is only copied when it actually changes
- the hardware queue drain and simulation batching are sized for high-rate controller testing without treating 1 kHz or 8 kHz as a software ceiling

These optimizations do not downsample or discard captured raw reports. Effective polling rate and timing metrics continue to come from the observed timestamps.
