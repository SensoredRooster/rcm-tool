# RCM Tool Support & Diagnostics

RCM Tool includes a local-first support system intended for tester and field diagnostics.

## Included support functions

- Rotating JSONL runtime telemetry under `%LOCALAPPDATA%\RCMTool\logs`
- One-second session heartbeat
- Session IDs for correlating tester reports
- Uncaught Python and worker-thread exception capture
- Local health snapshot with Python/Windows/platform state, free disk space, and controller backend availability
- **Support & Diagnostics** window in the main application
- **Create Support Bundle** ZIP
- **Send Diagnostics to Developer** with explicit user confirmation
- **Open Logs Folder**
- **Report Issue on GitHub**
- **Open Repository**
- Secret/token/password/cookie/credential field redaction
- Dedicated Cloudflare Worker + dedicated R2 bucket
- Per-IP upload rate limiting
- ZIP-only upload validation and bundle-size limit
- Admin-protected bundle listing/download endpoints
- Windows CI coverage for Python compilation and support bundle creation

## Privacy

Nothing is uploaded automatically. The tester must explicitly choose **Send Diagnostics to Developer** and confirm the prompt.

The support bundle intentionally excludes certification reports, raw controller samples, and raw HID captures. It contains the support event logs and a diagnostic manifest describing runtime/platform state.

## Cloudflare isolation

This project uses:

- Worker: `rcm-tool-support`
- R2 bucket: `rcm-tool-support-logs`

It does not share diagnostic storage with SubScript, Universal AI Studio, or SonicScout2.0.

The deployment workflow requires only the `CLOUDFLARE_API_TOKEN` GitHub Actions secret. The Cloudflare account ID is non-secret and is stored in the Wrangler/deployment configuration.
## Production Cloudflare services

Support diagnostics and tester file sharing are separate services and separate R2 buckets.

- Diagnostics Worker: `https://rcm-tool-support.sensoredrooster-com.workers.dev`
- Diagnostics R2: `rcm-tool-support-logs`
- Tester Share: `https://rcm-tool-share.sensoredrooster-com.workers.dev`
- Tester Share R2: `rcm-tool-share`

The app's built-in support endpoint is live by default; `RCM_SUPPORT_UPLOAD_URL` is only a development override. The Tester Share button opens the authenticated project portal.

