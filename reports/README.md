# Exported controller reports

RCM Tool writes each exported test into this folder as a timestamped JSON file and appends a summary line to `index.jsonl`. Each JSON file contains the summary metrics plus the timestamped normalized sample stream captured during the test.

Reports include:

- raw axis samples (`lx`, `ly`, `rx`, `ry`) and RC-filtered trend samples (`lx_filtered`, …);
- `rc_filter_metrics` with `tau_seconds` and per-axis `*_high_freq_rms` (RMS of raw minus RC trend);
- SHA-256 of the canonical JSON payload.

The reports are intended for comparing controller behavior and tuning tournament review thresholds. They may contain controller product names, vendor/product IDs, timestamps, and measured input metrics. Review the data before committing or sharing it outside the tournament team.

The app saves reports here automatically when the tester clicks **Export to reports**. Double-click **RCM Tool - Submit Reports.bat** in the repository root when the tester is ready to review and push the reports to GitHub.
