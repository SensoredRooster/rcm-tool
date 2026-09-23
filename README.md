# Gamepad Signal Lab

Gamepad Signal Lab is the next-generation desktop application built on the original **RCM Tool** controller measurement code. It is a Windows laboratory workspace for measuring controller report timing, analog-input stability, oscillator/clock stability, controlled bench stimulus, sweep response, and synchronized timing relationships.

The application is measurement-focused. It does not inject game inputs, modify controller firmware, or manufacture precision that the connected hardware cannot provide.

## Current integrated capabilities

- Modern PySide6/Qt desktop UI with rounded panels, dark/light modes, high-DPI scaling, dashboard cards, live plots, and dedicated lab pages.
- Existing RCM controller acquisition through Windows XInput, SDL/pygame, Raw HID/hidapi, and WinMM fallback.
- Controller timing analysis: effective report rate, interval statistics, RMS timing deviation, peak-to-peak jitter, successive interval variation, P50/P90/P95/P99/P99.9, late reports, and estimated missing reports.
- Live stick visualization, button/D-pad display where decoded by the active backend, trigger display, and stationary analog-noise inspection.
- Live Capture controls for zoom, pan, crosshair inspection, pause-visualization-without-pausing-acquisition, display-only moving-average smoothing, PNG export, fullscreen inspection, raw-data viewing, report-interval histogram, and explicit unavailable labeling for latency when no device-origin timestamp exists.
- Oscillator Lab: frequency, error in Hz and ppm, frequency standard deviation/span, first-to-last-window drift, configurable sigma outlier count, period statistics, RMS period jitter, peak-to-peak jitter, cycle-to-cycle jitter, and one-sample-interval Allan deviation.
- A dedicated read-only VISA/SCPI measurement role for counters, oscilloscopes, and analyzers.
- A separate VISA/SCPI signal-generator role with output-off-by-default behavior.
- Interference Lab with explicit output enable, configurable software limits, and an EMERGENCY OUTPUT OFF control.
- Linear/logarithmic frequency and amplitude sweep planning with repetitions, settling time, dwell time, sequential/randomized ordering, event logging, selectable response heat maps, per-step isolated measurement windows, and forced output-off at completion.
- Synchronized controller and oscillator storage with descriptive correlation.
- SQLite/WAL session storage, experiment history/timeline, JSON export, controller CSV export, saved baseline JSON, and self-contained HTML engineering reports containing plots, sweep response, timeline, metadata, and limitations.
- Deterministic gamepad/oscillator simulation with configurable rate, random/periodic jitter, spikes, missing-report cadence, oscillator offset/jitter/drift, plus known stimulus-response relationships for validating sweep/correlation behavior.
- Automated tests and Windows CI packaging for a portable app and Inno Setup installer.

## Measurement integrity

Controller timestamps use Python's highest-resolution host monotonic clock available through time.perf_counter_ns. The meaning of those timestamps depends on the backend:

- **Raw HID** reports are timestamped when the application receives/drains the HID report. This is still a host-observed timestamp.
- **XInput, SDL, and WinMM** are host-polling APIs. Their timing includes operating-system scheduling and API buffering effects.
- Dedicated oscilloscopes, counters, logic analyzers, USB analyzers, or timing instruments are required when direct electrical or bus-level timing is needed.

The application labels data as simulated, measured, calculated, estimated, or unavailable rather than inventing unsupported values.

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

1. Launch Gamepad Signal Lab.
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
- gamepad/clock response heat maps
- timestamped experiment events

Correlation is descriptive. A coefficient or visual time alignment can show that values moved together; it does not establish causation.

## Data storage

Gamepad Signal Lab uses SQLite with WAL journaling. Sessions contain controller samples, oscillator samples, and experiment events.

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

The GitHub Actions workflow at .github/workflows/build-windows.yml runs the tests and an offscreen Qt smoke test on Windows, creates both a portable folder build and a true one-file standalone GamepadSignalLab.exe, builds the Inno Setup installer, and uploads all three as workflow artifacts.

## Tests

~~~powershell
python -m unittest discover -v
~~~

The suite covers the legacy RCM measurement code plus timing calculations, configurable late-report detection, ppm/period/jitter calculations, oscillator drift/outlier statistics, deterministic simulation, correlation, SCPI readback and output safety, sweep construction, report generation, and SQLite/CSV/JSON persistence. Windows CI also performs an offscreen Qt window smoke test before packaging.

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

The existing RCM support and tester-share infrastructure remains in place. See:

- docs/SUPPORT.md
- docs/TESTER_SHARE.md

The original Cloudflare workflows remain alongside the new Windows application build workflow.
