# Exported controller reports

RCM Tool writes each exported test into this folder as a timestamped JSON file and appends a summary line to `index.jsonl`.

The reports are intended for comparing controller behavior and tuning tournament review thresholds. They may contain controller product names, vendor/product IDs, timestamps, and measured input metrics. Review the data before committing or sharing it outside the tournament team.

The app saves reports here automatically when the tester clicks **Export to reports**. Saving locally does not push files to GitHub; a team member must review, commit, and push them explicitly.
