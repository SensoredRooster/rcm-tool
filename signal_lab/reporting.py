"""Self-contained HTML engineering report generation."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path


def _table(mapping: dict) -> str:
    rows = []
    for key, value in mapping.items():
        if isinstance(value, float):
            display = f"{value:.9g}"
        elif value is None:
            display = "Unavailable"
        else:
            display = str(value)
        rows.append(f"<tr><th>{escape(str(key))}</th><td>{escape(display)}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


def write_html_report(destination: str | Path, *, title: str, controller_metrics: dict, oscillator_metrics: dict, metadata: dict, limitations: list[str]) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    limitations_html = "".join(f"<li>{escape(item)}</li>" for item in limitations)
    raw_meta = escape(json.dumps(metadata, indent=2))
    html = f"""<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#080b12;color:#eef2ff;margin:0;padding:40px}}
main{{max-width:1050px;margin:auto}}h1{{font-size:34px;margin:0 0 6px}}h2{{margin-top:34px;color:#8bd8ff}}
.card{{background:#111827;border:1px solid #263247;border-radius:18px;padding:22px;margin:18px 0}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #243047;padding:10px}}th{{color:#9fb0c9;width:42%}}
code,pre{{font-family:Cascadia Mono,monospace}}pre{{white-space:pre-wrap;color:#b9c7db}}
.small{{color:#93a4bc;font-size:13px}}
</style></head><body><main><h1>{escape(title)}</h1><div class='small'>Generated {datetime.now(timezone.utc).isoformat()}</div>
<div class='card'><h2>Gamepad timing</h2>{_table(controller_metrics)}</div>
<div class='card'><h2>Oscillator / clock</h2>{_table(oscillator_metrics)}</div>
<div class='card'><h2>Measurement limitations</h2><ul>{limitations_html}</ul></div>
<div class='card'><h2>Metadata</h2><pre>{raw_meta}</pre></div>
</main></body></html>"""
    destination.write_text(html, encoding="utf-8")
    return destination
