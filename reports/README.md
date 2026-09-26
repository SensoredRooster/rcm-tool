# Exported controller reports

The legacy Tk capture bench (`controller_integrity.py`) writes each exported test into this folder as a timestamped JSON file and appends a summary line to `index.jsonl`. The RcmTool desktop app stores its sessions in its local SQLite database instead.

Each JSON file (`reportVersion` `0.1`) contains:

- raw axis samples (`lx`, `ly`, `rx`, `ry`) with capture-relative `t_ms` timestamps;
- the raw HID reports drained during the capture, when the source is Raw HID;
- per-axis metrics, including the high-frequency jitter residual from a slow EMA (alpha 0.12);
- SHA-256 of the canonical JSON payload (hash is computed before the `sha256` field is inserted).

Single tests are written when the tester clicks **Export to reports**. Before + After pair mode writes files automatically and may also write `*_before_after_comparison.json`.

Report files are git-ignored so fresh local tests never block the updater's clean-tree check. Double-click **RCM Tool - Submit Reports.bat** in the repository root to review `git status --short --ignored reports` and, after typing `YES`, stage them explicitly, then commit and push only this folder.

Reports may contain controller product names, vendor/product IDs, timestamps, and measured input metrics. Review the data before sharing it outside the tournament team.
