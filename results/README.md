# Guided test results

RcmTool saves every finished guided test here automatically, so testers and developers can open the same files.

Each test writes three files, named with the date, time, and controller:

- `..._report.html` is the readable report. It covers both captures: report rate, capture checks, stationary check, smoothing estimate, and per-axis noise.
- `..._results.json` holds every computed result from both captures, plus the controller details and the session ID.
- `..._raw-reports.csv.gz` holds every raw report from both captures: host arrival time, decoded stick, trigger, button, and D-pad values, and the raw HID bytes.

The packaged app saves to `Documents\RcmTool Results` instead, because it has no repository folder next to it.

These files are git-ignored so a fresh test never blocks the updater. To share them with developers, run **RCM Tool - Submit Reports.bat** in the repository root. It lists the files, asks you to type `YES`, then commits and pushes them.
