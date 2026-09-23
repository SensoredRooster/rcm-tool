"""Self-contained HTML engineering report generation."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
from typing import Sequence


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


def _svg_plot(title: str, values: Sequence[float], *, width: int = 920, height: int = 230) -> str:
    clean = [float(v) for v in values if math.isfinite(float(v))]
    if len(clean) < 2:
        return f"<div class='card'><h2>{escape(title)}</h2><p class='small'>Not enough samples for this plot.</p></div>"
    lo, hi = min(clean), max(clean)
    if math.isclose(lo, hi):
        pad = abs(lo) * 0.05 or 1.0
        lo -= pad
        hi += pad
    left, top, right, bottom = 58, 28, 18, 34
    plot_w, plot_h = width - left - right, height - top - bottom
    points = []
    for i, value in enumerate(clean):
        x = left + plot_w * i / max(1, len(clean) - 1)
        y = top + plot_h - (value - lo) / (hi - lo) * plot_h
        points.append(f"{x:.2f},{y:.2f}")
    grid = "".join(
        f"<line x1='{left}' y1='{top + plot_h*i/4:.2f}' x2='{left+plot_w}' y2='{top + plot_h*i/4:.2f}' stroke='#243047' stroke-width='1'/>"
        for i in range(5)
    )
    return (
        f"<div class='card'><h2>{escape(title)}</h2>"
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{escape(title)}'>"
        f"<rect width='{width}' height='{height}' rx='14' fill='#0d1420'/>"
        f"{grid}<polyline fill='none' stroke='#67d3ff' stroke-width='2' points='{' '.join(points)}'/>"
        f"<text x='8' y='{top+10}' fill='#91a4bf' font-size='12'>{hi:.6g}</text>"
        f"<text x='8' y='{top+plot_h}' fill='#91a4bf' font-size='12'>{lo:.6g}</text>"
        f"</svg></div>"
    )


def _sweep_html(points: Sequence[Sequence[float]]) -> str:
    if not points:
        return "<div class='card'><h2>Sweep response</h2><p class='small'>No sweep response data was captured.</p></div>"
    rows = []
    triples: list[tuple[float, float, float]] = []
    for item in points:
        if len(item) < 4:
            continue
        freq, amp, gamepad_rms, clock_ppm = map(float, item[:4])
        triples.append((freq, amp, gamepad_rms))
        rows.append(
            f"<tr><td>{freq:.9g}</td><td>{amp:.9g}</td><td>{gamepad_rms:.9g}</td><td>{clock_ppm:+.9g}</td></tr>"
        )
    if not triples:
        return "<div class='card'><h2>Sweep response</h2><p class='small'>No valid sweep points.</p></div>"
    values = [v for _, _, v in triples]
    vmin, vmax = min(values), max(values)
    if math.isclose(vmin, vmax):
        vmax = vmin + 1.0
    dots = []
    logs = [math.log10(max(f, 1e-12)) for f, _, _ in triples]
    amps = [a for _, a, _ in triples]
    xmin, xmax = min(logs), max(logs)
    ymin, ymax = min(amps), max(amps)
    if math.isclose(xmin, xmax): xmax = xmin + 1.0
    if math.isclose(ymin, ymax): ymax = ymin + 1.0
    for (freq, amp, value), lx in zip(triples, logs):
        x = 65 + 790 * (lx - xmin) / (xmax - xmin)
        y = 210 - 160 * (amp - ymin) / (ymax - ymin)
        ratio = max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
        hue = int(210 - 200 * ratio)
        dots.append(f"<circle cx='{x:.2f}' cy='{y:.2f}' r='7' fill='hsl({hue},75%,58%)'/>")
    return (
        "<div class='card'><h2>Sweep response</h2>"
        "<svg viewBox='0 0 920 250'><rect width='920' height='250' rx='14' fill='#0d1420'/>"
        "<text x='460' y='238' text-anchor='middle' fill='#91a4bf' font-size='12'>Stimulus frequency (log scale)</text>"
        "<text x='20' y='130' transform='rotate(-90 20 130)' text-anchor='middle' fill='#91a4bf' font-size='12'>Amplitude Vpp</text>"
        + "".join(dots) + "</svg>"
        "<table><thead><tr><th>Frequency Hz</th><th>Amplitude Vpp</th><th>Gamepad RMS ms</th><th>Clock ppm</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div>"
    )


def _timeline_html(events: Sequence[dict]) -> str:
    if not events:
        return "<div class='card'><h2>Experiment timeline</h2><p class='small'>No timeline events were recorded.</p></div>"
    rows = []
    for event in events[-250:]:
        rows.append(
            "<tr>"
            f"<td>{escape(str(event.get('timestamp_ns', '')))}</td>"
            f"<td>{escape(str(event.get('event_type', '')))}</td>"
            f"<td><code>{escape(json.dumps(event.get('payload', {}), separators=(',', ':')))}</code></td>"
            "</tr>"
        )
    return (
        "<div class='card'><h2>Experiment timeline</h2><table>"
        "<thead><tr><th>Timestamp ns</th><th>Event</th><th>Details</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div>"
    )


def write_html_report(
    destination: str | Path,
    *,
    title: str,
    controller_metrics: dict,
    oscillator_metrics: dict,
    metadata: dict,
    limitations: list[str],
    plots: dict[str, Sequence[float]] | None = None,
    sweep_points: Sequence[Sequence[float]] | None = None,
    timeline: Sequence[dict] | None = None,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    limitations_html = "".join(f"<li>{escape(item)}</li>" for item in limitations)
    raw_meta = escape(json.dumps(metadata, indent=2))
    plot_html = "".join(_svg_plot(name, values) for name, values in (plots or {}).items())
    html = f"""<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#080b12;color:#eef2ff;margin:0;padding:40px}}
main{{max-width:1050px;margin:auto}}h1{{font-size:34px;margin:0 0 6px}}h2{{margin:0 0 16px;color:#8bd8ff}}
.card{{background:#111827;border:1px solid #263247;border-radius:18px;padding:22px;margin:18px 0;overflow:auto}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #243047;padding:10px;vertical-align:top}}th{{color:#9fb0c9}}
code,pre{{font-family:Cascadia Mono,monospace}}pre{{white-space:pre-wrap;color:#b9c7db}}
.small{{color:#93a4bc;font-size:13px}}svg{{width:100%;height:auto;display:block}}
</style></head><body><main><h1>{escape(title)}</h1><div class='small'>Generated {datetime.now(timezone.utc).isoformat()}</div>
<div class='card'><h2>Gamepad timing</h2>{_table(controller_metrics)}</div>
<div class='card'><h2>Oscillator / clock</h2>{_table(oscillator_metrics)}</div>
{plot_html}
{_sweep_html(sweep_points or [])}
{_timeline_html(timeline or [])}
<div class='card'><h2>Measurement limitations</h2><ul>{limitations_html}</ul></div>
<div class='card'><h2>Metadata</h2><pre>{raw_meta}</pre></div>
</main></body></html>"""
    destination.write_text(html, encoding="utf-8")
    return destination
