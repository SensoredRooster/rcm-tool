# RcmTool

RcmTool is a beginner-friendly Windows app for answering one practical
question: **what is this controller actually sending to this PC?**

It measures controller reports, timing, stick noise, repeated reports, and
movement settling. It also provides a cautious **smoothing estimate**. That
estimate describes how smooth the signal looks after it reaches Windows; it
does not pretend to read a hidden firmware setting.

The application is measurement-focused. It does not inject game inputs, modify controller firmware, or manufacture precision that the connected hardware cannot provide.

## Active test workflow

The primary UI is intentionally simple:

- **Test:** Connect/select the controller and press **START CONTROLLER TEST**. RcmTool handles session recording, saving, and analysis automatically.
- **Results:** Shows the latest test, live timing/stick summary, saved session activity, and optional exports.
- **Support:** Provides **Send Diagnostics**, **Tester Share**, and an Advanced Support dialog for logs, health data, bundles, and repository links.
- **Settings:** Open from the gear button in the top-right. These controls are optional and are no longer a main navigation destination.
- **Advanced Controller Tools:** Button learning/mapping and technical Raw HID details are available from the Test page without cluttering the normal test flow.

The guided test automatically starts the underlying recording session when needed,
captures untouched-stick behavior for 10 seconds, then walks the tester through a
20-second movement/settling phase. The session is stopped and saved automatically,
and the app returns to **Results** when the test completes.

## Filtering and smoothing, in plain English

Filtering (also called smoothing) makes a stick signal change more gently.
It can hide tiny unwanted wiggles, but too much of it can make a stick feel
soft or delayed.

- **Less filtering:** sharper response, but more visible jitter.
- **More filtering:** calmer response, but potentially more delay.
- **Offline smoother in RcmTool:** a copy of the recorded data is smoothed
  for comparison. Your controller and game input are never changed.
- **Smoothing estimate:** a relative indicator based on neighboring samples and
  repeated Raw HID reports. It is shown as low, moderate, or high and includes
  a confidence value when the capture is suitable.

The estimate is intentionally conservative. A Raw HID capture cannot prove
whether smoothness came from the sensor, analog circuit, controller firmware,
USB transport, driver, remapper, or game.

## Measurement integrity

The primary UI is intentionally focused on the controller question: Raw HID
noise, report timing jitter, movement/settling behavior, and optional upstream
trace capture. The local SQLite database, raw samples, testing timeline,
support logs, and app data are retained. Older lab implementations remain in
the codebase for compatibility, but their pages are not constructed or shown
in the focused UI.

Controller timestamps use Python's highest-resolution host monotonic clock available through time.perf_counter_ns. The meaning of those timestamps depends on the backend:

- **Raw HID** reports are timestamped when the application receives/drains the HID report. This is still a host-observed timestamp after USB.
- The focused UI does not start XInput/SDL/WinMM polling. A named HID interface may still be software-emulated; VID/PID and product strings do not prove physical authenticity.
- Dedicated oscilloscopes, counters, logic analyzers, USB analyzers, or timing instruments are required when direct electrical or bus-level timing is needed.

The application labels data as measured, calculated, estimated, or unavailable rather than inventing unsupported values.

Dashboard cards and graphs include short definitions describing what each value means. Controller timing uses the measured median report interval as the default jitter/late-report reference; a configured reference rate can be selected explicitly in Settings. Time-series plots use elapsed timestamps rather than treating sample number as time.

Noise reports list capture-integrity checks separately (duration, sample count, timestamp order, and raw-report-byte coverage); they do not collapse them into a quality or firmware-confidence percentage. A configured-rate comparison is shown only when that reference mode is explicitly selected.

Analog stick noise is only labeled as a stationary noise floor when all four stick axes remain within the configured stationary-excursion threshold for the analysis window. If movement exceeds that threshold, the noise-floor result is withheld instead of reporting motion as noise.

Raw HID alone cannot distinguish filtering in the sensor, analog circuit, ADC, firmware, or host. The report's high-frequency energy percentage is a descriptive signal metric, not a percent estimate of firmware smoothing or a confidence score. Firmware attribution requires a properly connected upstream trace and a valid synchronization method.

## Install from source

The supported build target is 64-bit Windows with Python 3.12 through 3.14.

~~~powershell
git clone https://github.com/SensoredRooster/rcm-tool.git
cd rcm-tool
python -m pip install -r requirements.txt
python -m unittest discover -v
python gamepad_signal_lab.py
~~~

`gamepad_signal_lab.py` is the canonical supported desktop entry point. The compatibility entry point also works:

~~~powershell
python rcm_tool.py
~~~

If PySide6 is not installed, `rcm_tool.py` explicitly falls back to the original Tk RCM capture bench. That legacy bench is retained for compatibility only; new features and release packaging target the PySide6 application.

Runtime dependencies are pinned in `requirements.txt` from the validated Windows environment. Development and release tooling is pinned in `requirements-dev.txt`; update both files together when intentionally upgrading the supported environment.

## First-run workflow

1. Launch RcmTool.
2. Plug the controller in by USB. RcmTool scans Raw HID devices automatically; if more than one controller-like interface is present, choose the correct named device on **Test**.
3. Move a stick or press a button once so live Raw HID reports are confirmed.
4. Press **START CONTROLLER TEST**.
5. Leave the controller still when instructed, then perform the simple one-stick movement step.
6. RcmTool records, saves, and analyzes the session automatically, then opens **Results**.
7. Export a report only when you need to share or inspect the technical data.

The Raw HID selector reads the selected interface's USB HID reports and labels timestamps as **measured at host arrival**. If no device is found, the app does not substitute simulated or XInput values.

## Retained compatibility modules

Older oscillator, instrument-output, sweep, baseline, comparison, and correlation code remains in the repository for compatibility with prior data and reports. These modules are not constructed or exposed in the focused desktop workflow. The active tool does not use generated stimulus or simulations as controller evidence.

## Data storage

RcmTool uses SQLite with WAL journaling. Sessions contain controller samples, oscillator samples, controller backend-specific fields such as button/D-pad state where available, oscillator measurements, and experiment events. Hardware/controller metadata such as VID, PID, HID path, interface, firmware release, battery/power status, and connection method are shown only when the active backend can actually provide them.

Exports include:

- session JSON
- controller CSV
- engineering HTML report

Raw samples/timestamps remain independent of the summary statistics.

## Support data and privacy

Runtime telemetry is local-only under `%LOCALAPPDATA%\\RCMTool\\logs` until the user chooses **Send Diagnostics to Developer**. The upload flow creates a redacted bundle first and shows its session and scope before asking for confirmation. The uploaded bundle contains the health manifest and redacted support logs; it intentionally excludes certification reports, raw controller samples, and raw HID captures. A local bundle can always be created and shared manually instead.

## Build the Windows app

Local build:

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
~~~

Portable folder output:

~~~text
dist\RcmTool\RcmTool.exe
~~~

Standalone one-file output:

~~~text
dist\standalone\RcmTool.exe
~~~

Installer definition:

~~~text
installer\GamepadSignalLab.iss
~~~

The GitHub Actions workflow at `.github/workflows/test.yml` runs compilation, Ruff, Pyright, and the unit tests. The release build script targets only the canonical PySide6 entry point; the legacy Tk bench is not bundled into release artifacts.

## Tests

~~~powershell
python -m unittest discover -v
~~~

The suite covers timing calculations, ppm/period/jitter calculations, oscillator drift/outlier statistics, controller metadata extraction, correlation, SCPI readback and output safety, sweep construction, report generation, saved-session retrieval, and SQLite/CSV/JSON persistence. The legacy compatibility bench remains covered by its existing tests but is not the release product.

## Project layout

~~~text
gamepad_signal_lab.py
signal_lab/
  analysis.py       timing, jitter, ppm, Allan deviation, correlation
  controller.py     adapter over existing RCM controller backends
  oscillator.py     background physical frequency acquisition
  instruments.py    separate VISA/SCPI measurement and generator roles
  storage.py        SQLite and exports
  sweep.py          reproducible sweep planning
  reporting.py      engineering HTML reports
  ui.py             PySide6 desktop application
controller_integrity.py
installer/GamepadSignalLab.iss
scripts/build_windows.ps1
~~~

## Legacy RCM Tool components

The original controller integrity functionality remains in the repository, including its controller backends, RC-filter analysis, before/after pair comparisons, tester-share/support integration, and prior report format. It is a compatibility/experimental surface, not the canonical RcmTool product boundary.

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

RcmTool does not use 1 kHz as a measurement ceiling in configured-reference analysis. That control accepts **1 Hz through 100 kHz**, covering 8 kHz and higher-rate controllers. Physical hardware validation is backend- and device-dependent; only Raw HID arrival timing is treated as a measured host-observed stream, while XInput, SDL, and WinMM remain host-poll estimates. Hardware-specific rate claims should be reported only for devices validated in `HARDWARE_MEASUREMENTS.md`.

At very high physical rates, usable fidelity still depends on the controller, USB transport, backend, Windows scheduling, and timing source. Raw-HID arrival timing is preferred when available; host-poll backends remain labeled as estimates.


## Performance architecture

The live measurement path is designed to keep visualization and persistence work from becoming artificial timing bottlenecks:

- controller timestamps are captured by the acquisition backend before GUI rendering or SQLite persistence
- raw controller/oscillator rows are buffered and persisted in batched SQLite `executemany()` writes instead of one SQL statement per report
- buffered rows are force-flushed at capture stop, export, session reads, and application close
- hidden pages do not rebuild graph datasets or schedule chart repaints; navigating to a live page triggers an immediate refresh
- controller metadata is only copied when it actually changes
- the hardware queue drain is sized for high-rate controller testing without treating 1 kHz or 8 kHz as a software ceiling

These optimizations do not downsample or discard captured raw reports. Effective polling rate and timing metrics continue to come from the observed timestamps.
