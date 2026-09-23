# Gamepad Signal Lab

Gamepad Signal Lab is the next-generation desktop application built on the original **RCM Tool** controller measurement code. It is a Windows laboratory workspace for measuring controller report timing, analog-input stability, oscillator/clock stability, controlled bench stimulus, sweep response, and synchronized timing relationships.

The application is measurement-focused. It does not inject game inputs, modify controller firmware, or manufacture precision that the connected hardware cannot provide.

## Current integrated capabilities

- Modern PySide6/Qt desktop UI with rounded panels, dark/light modes, high-DPI scaling, dashboard cards, live plots, and dedicated lab pages.
- Existing RCM controller acquisition through Windows XInput, SDL/pygame, Raw HID/hidapi, and WinMM fallback.
- Controller timing analysis: effective report rate, interval statistics, RMS timing deviation, peak-to-peak jitter, successive interval variation, P50/P90/P95/P99/P99.9, late reports, and estimated missing reports.
- Live stick visualization and analog stability inspection.
- Oscillator Lab: frequency, error in Hz and ppm, period statistics, RMS period jitter, peak-to-peak jitter, cycle-to-cycle jitter, drift visualization, and one-sample-interval Allan deviation.
- A dedicated read-only VISA/SCPI measurement role for counters, oscilloscopes, and analyzers.
- A separate VISA/SCPI signal-generator role with output-off-by-default behavior.
- Interference Lab with explicit output enable, configurable software limits, and an EMERGENCY OUTPUT OFF control.
- Linear/logarithmic sweep planning with repetitions, dwell time, event logging, and forced output-off at completion.
- Synchronized controller and oscillator storage with descriptive correlation.
- SQLite/WAL session storage, JSON export, controller CSV export, and self-contained HTML engineering reports.
- Deterministic gamepad/oscillator simulation so the workflow can be exercised without physical hardware.
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
- frequency standard deviation
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
- The UI exposes **EMERGENCY OUTPUT OFF**.
- Sweep completion forces the source OFF.
- Closing the application attempts to force output OFF and close the source.

Default software limits are conservative starting values; they are not electrical ratings for any particular controller, coupling network, scope, or generator. Use limits appropriate to the actual bench hardware.

## Baselines, sweeps, and correlation

A baseline captures the current controller timing and oscillator statistics as the reference for the active session.

The sweep engine supports:

- linear or logarithmic frequency spacing
- repeated sweeps
- configurable dwell
- deterministic step ordering
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

Portable output:

~~~text
dist\GamepadSignalLab\GamepadSignalLab.exe
~~~

Installer definition:

~~~text
installer\GamepadSignalLab.iss
~~~

The GitHub Actions workflow at .github/workflows/build-windows.yml runs the tests on Windows, creates the PyInstaller application, builds the Inno Setup installer, and uploads both as workflow artifacts.

## Tests

~~~powershell
python -m unittest discover -v
~~~

The suite covers the legacy RCM measurement code plus the new timing calculations, ppm calculations, deterministic simulation, correlation, instrument safety behavior, sweep construction, and SQLite persistence.

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
