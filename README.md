# RCM Tool

Windows controller integrity prototype. It **reads** stick axes. It does **not** write to the controller, inject into a game, or flash firmware.

Repository: https://github.com/SensoredRooster/rcm-tool

Working prototype: capture, RC high-frequency RMS, auto-export JSON, Before/After pair-delta, injected self-test, 2026 dark lab UI.

## 1. Install once

```powershell
git clone https://github.com/SensoredRooster/rcm-tool.git
cd rcm-tool
python -m pip install -r requirements.txt
python apply_all.py
python -m unittest discover -v
python rcm_tool.py
```

Later: **RCM Tool - Update and Start.bat**.

If apply says `could not find block`:

```powershell
git checkout -- controller_integrity.py
python apply_all.py
```

## 2. UI

Dark graphite (`#0E1116`) with mint accent (`#3DDC97`). Segoe UI / Cascadia Mono. Start is the mint button. Theme lives in `rcm_theme.py` and is applied last by `apply_theme.py`.

Windows ttk still owns some native combo chrome. The rest of the window is themed.

## 3. Session order

Tool check first: Injected HF sine, then Injected slow sine, thumbs off. Files tagged `INJECTED_`.

Then Neutral Before+After pair. Then Guided pair. Pair is blocked while an Injected protocol is selected.

## 4. Pair delta

`unchanged` / `increased_at_rest` / `increased_high_freq` / `unsupported`. Change detector only. Not organic vs non-organic.

## 5. RC filter

`tau = 0.05 s`. HF RMS = RMS(raw minus trend). `reportVersion` 0.2.

## 6. After a run

JSON writes itself. **Submit Reports.bat**, type YES. Do not submit `INJECTED_` files as a pad screen.

## Support, diagnostics, and tester sharing

RCMTool includes a local-first diagnostics system plus two isolated Cloudflare services.

### Support diagnostics

- Worker: `https://rcm-tool-support.sensoredrooster-com.workers.dev`
- Upload endpoint: `https://rcm-tool-support.sensoredrooster-com.workers.dev/upload`
- R2 bucket: `rcm-tool-support-logs`
- The Support & Diagnostics window creates redacted ZIP bundles and sends them only after explicit confirmation.
- `RCM_SUPPORT_UPLOAD_URL` is supported as a development override.

### Tester Share

- Portal: `https://rcm-tool-share.sensoredrooster-com.workers.dev`
- R2 bucket: `rcm-tool-share`
- Open it from **Support & Diagnostics → Tester Share**.
- Folders: `Releases`, `Tester Uploads`, `Screenshots`, `Bug Reports`, `Logs`, `Archived`

Testers can browse/download and upload to tester-facing folders. Admin access can upload releases, mark **Latest**, delete files, and manage archived content.

See [docs/SUPPORT.md](docs/SUPPORT.md) and [docs/TESTER_SHARE.md](docs/TESTER_SHARE.md).
