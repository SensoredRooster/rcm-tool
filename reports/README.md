# Exported controller reports

RCM Tool writes each exported test into this folder as a timestamped JSON file and appends a summary line to `index.jsonl`.

After `apply_rc_wiring.py` has been run on that machine, each JSON file contains:

- raw axis samples (`lx`, `ly`, `rx`, `ry`) and RC-filtered trend samples (`lx_filtered` ... `ry_filtered`);
- `rc_filter_metrics` with `tau_seconds`, `cutoff_hz`, and per-axis `*_high_freq_rms` / `*_high_freq_peak_to_peak`;
- SHA-256 of the canonical JSON payload (hash is computed before the `sha256` field is inserted).

`reportVersion` must be `0.2` for those RC fields to exist. If you see `0.1`, run `python apply_rc_wiring.py` and capture again.

Single tests are written when the tester clicks **Export to reports**. Before + After pair mode writes files automatically and may also write `*_before_after_comparison.json`.

Double-click **RCM Tool - Submit Reports.bat** in the repository root to review `git status --short reports` and, after typing `YES`, commit and push only this folder.

Reports may contain controller product names, vendor/product IDs, timestamps, and measured input metrics. Review the data before sharing it outside the tournament team.
