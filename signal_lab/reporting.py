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
        try:
            freq = float(item[0])
            amp = float(item[1])
        except (TypeError, ValueError):
            continue
        gamepad_rms = None if item[2] is None else float(item[2])
        clock_ppm = None if item[3] is None else float(item[3])
        if gamepad_rms is not None and math.isfinite(gamepad_rms):
            triples.append((freq, amp, gamepad_rms))
        gamepad_text = "Unavailable" if gamepad_rms is None or not math.isfinite(gamepad_rms) else f"{gamepad_rms:.9g}"
        clock_text = "Unavailable" if clock_ppm is None or not math.isfinite(clock_ppm) else f"{clock_ppm:+.9g}"
        rows.append(
            f"<tr><td>{freq:.9g}</td><td>{amp:.9g}</td><td>{gamepad_text}</td><td>{clock_text}</td></tr>"
        )
    if not rows:
        return "<div class='card'><h2>Sweep response</h2><p class='small'>No valid sweep points.</p></div>"
    if not triples:
        return (
            "<div class='card'><h2>Sweep response</h2>"
            "<p class='small'>Sweep steps were recorded, but no gamepad timing metric was available for the response map.</p>"
            "<table><thead><tr><th>Frequency Hz</th><th>Amplitude Vpp</th><th>Gamepad RMS ms</th><th>Clock ppm</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>"
        )
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
    oscillator_metrics: dict | None,
    metadata: dict,
    limitations: list[str],
    plots: dict[str, Sequence[float]] | None = None,
    sweep_points: Sequence[Sequence[float]] | None = None,
    timeline: Sequence[dict] | None = None,
    interpretation: str | None = None,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    limitations_html = "".join(f"<li>{escape(item)}</li>" for item in limitations)
    raw_meta = escape(json.dumps(metadata, indent=2))
    plot_html = "".join(_svg_plot(name, values) for name, values in (plots or {}).items())
    oscillator_html = (
        f"<div class='card'><h2>Oscillator / clock</h2>{_table(oscillator_metrics)}</div>"
        if oscillator_metrics is not None else ""
    )
    sweep_html = _sweep_html(sweep_points) if sweep_points else ""
    interpretation_html = (
        f"<div class='card'><h2>How to interpret these results</h2><p>{escape(interpretation)}</p></div>"
        if interpretation else ""
    )
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
{interpretation_html}
{oscillator_html}
{plot_html}
{sweep_html}
{_timeline_html(timeline or [])}
<div class='card'><h2>Measurement limitations</h2><ul>{limitations_html}</ul></div>
<div class='card'><h2>Metadata</h2><pre>{raw_meta}</pre></div>
</main></body></html>"""
    destination.write_text(html, encoding="utf-8")
    return destination


def _report_shell(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#080b12;color:#eef2ff;margin:0;padding:36px}}
main{{max-width:1100px;margin:auto}}h1{{font-size:32px;margin:0 0 6px}}h2{{color:#8bd8ff;margin:0 0 14px}}
.card{{background:#111827;border:1px solid #263247;border-radius:16px;padding:20px;margin:16px 0;overflow:auto}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.metric{{background:#0d1420;border:1px solid #243047;border-radius:12px;padding:14px}}
.metric b{{display:block;color:#91a4bf;font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #243047;padding:9px;vertical-align:top}}th{{color:#9fb0c9}}
.small{{color:#93a4bc;font-size:13px}}.ok{{color:#6de0b1}}.warn{{color:#f0b862}}code,pre{{font-family:Cascadia Mono,monospace}}pre{{white-space:pre-wrap;color:#b9c7db}}
</style></head><body><main><h1>{escape(title)}</h1>{body}</main></body></html>"""


def _metric_grid(metrics: dict[str, object]) -> str:
    return "<div class='grid'>" + "".join(
        f"<div class='metric'><b>{escape(str(key))}</b><span>{escape(str(value))}</span></div>"
        for key, value in metrics.items()
    ) + "</div>"


def write_noise_evidence_report(
    destination: str | Path,
    result: dict,
    *,
    captures: dict[str, dict] | None = None,
    title: str = "RcmTool Raw HID Smoothing Evidence",
) -> Path:
    """Write a plain-language report for a real Raw HID noise capture."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    quality = result.get("capture_quality") or {}
    stationary = result.get("stationary_check") or {}
    rate_reference = result.get("rate_vs_reference_percent")
    rate_reference_text = (
        f"{float(rate_reference):.1f}%"
        if isinstance(rate_reference, (int, float)) else "Not configured"
    )
    sample_count = int(result.get("sample_count", 0))
    sample_rate = result.get("sample_rate_hz")
    sample_rate_text = (
        f"{float(sample_rate):.2f} reports/s"
        if sample_count >= 2 and isinstance(sample_rate, (int, float)) else "Unavailable"
    )
    duplicate_percent = result.get("duplicate_report_percent")
    summary = _metric_grid({
        "Evidence class": result.get("evidence_class", "Unavailable"),
        "Capture": result.get("capture_kind", "Unavailable"),
        "Observed reports": result.get("raw_hid_report_count", 0),
        "Observed rate": sample_rate_text,
        "Rate vs configured reference": rate_reference_text,
        "Duration": f"{float(result.get('duration_s', 0.0)):.3f} s",
        "Raw report coverage": f"{float(result.get('raw_report_coverage_percent', 0.0)):.1f}%",
        "Duplicate payloads": f"{float(duplicate_percent):.2f}%" if isinstance(duplicate_percent, (int, float)) else "Unavailable (<2 raw reports)",
        "Capture checks": quality.get("label", "unavailable"),
    })
    quality_rows = []
    for check in quality.get("checks", []):
        if check.get("observed_percent") is not None:
            observed = f"{float(check['observed_percent']):.1f}%"
        elif "observed_s" in check:
            observed = f"{float(check['observed_s']):.3f} s"
        else:
            observed = str(check.get("observed", "Unavailable"))
        if check.get("required_percent") is not None:
            required = f"{float(check['required_percent']):g}%"
        elif "required_s" in check:
            required = f"{float(check['required_s']):.1f} s minimum"
        else:
            required = str(check.get("required", "—"))
        quality_rows.append(
            "<tr>"
            f"<td>{escape(str(check.get('name', 'Check')))}</td>"
            f"<td>{escape(observed)}</td>"
            f"<td>{escape(required)}</td>"
            f"<td>{escape(str(check.get('status', 'unavailable')).upper())}</td>"
            "</tr>"
        )
    quality_table = (
        "<div class='card'><h2>Capture integrity checks</h2>"
        "<p>These independent checks describe completeness, not measurement accuracy or firmware confidence. "
        "The 100-sample floor is a screening rule, not a statistical guarantee. Review each result; no weighted quality percentage is calculated.</p>"
        "<table><thead><tr><th>Check</th><th>Observed</th><th>Screening requirement</th><th>Result</th></tr></thead>"
        f"<tbody>{''.join(quality_rows) or '<tr><td colspan=4>Unavailable</td></tr>'}</tbody></table></div>"
    )
    def axis_number(metrics: dict, key: str, decimals: int) -> str:
        value = metrics.get(key)
        return f"{float(value):.{decimals}f}" if isinstance(value, (int, float)) else "Unavailable"

    axis_rows = []
    for axis, metrics in (result.get("axes") or {}).items():
        axis_rows.append(
            "<tr>"
            f"<td>{escape(str(axis).upper())}</td>"
            f"<td>{axis_number(metrics, 'noise_rms', 8)}</td>"
            f"<td>{axis_number(metrics, 'peak_to_peak', 8)}</td>"
            f"<td>{axis_number(metrics, 'slow_trend_residual_rms', 8)}</td>"
            f"<td>{axis_number(metrics, 'high_frequency_energy_percent', 2)}"
            f"{'%' if metrics.get('high_frequency_energy_percent') is not None else ''}</td>"
            f"<td>{int(metrics.get('unique_levels', 0))}</td>"
            "</tr>"
        )
    axis_table = (
        "<table><thead><tr><th>Axis</th><th>Total RMS</th><th>Peak-to-peak</th>"
        "<th>50 ms residual RMS</th><th>High-frequency energy</th><th>Unique levels</th></tr></thead>"
        f"<tbody>{''.join(axis_rows) or '<tr><td colspan=6>Unavailable</td></tr>'}</tbody></table>"
    )
    capture_rows = []
    for name, capture in (captures or {}).items():
        capture_sample_count = int(capture.get("sample_count", 0))
        capture_rate = capture.get("sample_rate_hz")
        capture_rate_text = (
            f"{float(capture_rate):.2f}"
            if capture_sample_count >= 2 and isinstance(capture_rate, (int, float)) else "Unavailable"
        )
        capture_stationary = (capture.get("stationary_check") or {}).get("is_stationary")
        capture_rows.append(
            "<tr>"
            f"<td>{escape(str(name))}</td>"
            f"<td>{capture_sample_count}</td>"
            f"<td>{capture_rate_text}</td>"
            f"<td>{float(capture.get('duration_s', 0.0)):.3f}</td>"
            f"<td>{escape(str((capture.get('capture_quality') or {}).get('label', 'unavailable')))}</td>"
            f"<td>{escape(str(capture_stationary if capture_stationary is not None else 'Unavailable'))}</td>"
            "</tr>"
        )
    capture_table = ""
    if capture_rows:
        capture_table = (
            "<div class='card'><h2>All guided captures</h2>"
            "<table><thead><tr><th>Capture</th><th>Samples</th><th>Rate</th><th>Duration</th><th>Capture checks</th><th>Stationary check</th></tr></thead>"
            f"<tbody>{''.join(capture_rows)}</tbody></table></div>"
        )
    interpretation = escape(str(result.get("interpretation", "No interpretation available.")))
    if stationary.get("is_stationary") is True:
        stationary_text = "The capture stayed within the stationary threshold."
    elif stationary.get("is_stationary") is False:
        stationary_text = "The capture exceeded the stationary threshold; do not call this a stationary noise floor."
    else:
        stationary_text = "Stationarity is unavailable because the capture did not meet the sample-count screening minimum."
    body = (
        "<div class='card'><h2>Bottom line</h2>"
        f"<p>{interpretation}</p><p class='small'>{escape(stationary_text)} "
        "These percentages describe the captured signal; they are not probabilities that firmware is cheating or filtering.</p></div>"
        f"<div class='card'><h2>Capture summary</h2>{summary}</div>"
        f"{quality_table}"
        f"{capture_table}"
        "<div class='card'><h2>How to read the numbers</h2>"
        "<ul><li><b>Total RMS</b> is the axis variation around its mean during this capture.</li>"
        "<li><b>Rate vs configured reference</b> compares the observed Raw HID arrival rate with the reference you entered. It is a warning signal, not proof that the controller or firmware dropped reports.</li>"
        "<li><b>50 ms residual RMS</b> subtracts a documented slow trend. It is a repeatable comparison metric, not a direct firmware measurement.</li>"
        "<li><b>High-frequency energy</b> is the squared residual RMS as a percentage of total AC energy. Higher means more captured variation remains above the slow trend.</li>"
        "<li><b>Duplicate payloads</b> is the percentage of adjacent Raw HID reports with identical bytes. It is not automatically bad: a centered stick can legitimately repeat.</li>"
        "<li><b>Capture checks</b> show duration, sample count, timestamp order, and Raw HID report-byte coverage separately. Their screening requirements are visible above; they are not a combined score or confidence percentage.</li></ul></div>"
        f"<div class='card'><h2>Axis results</h2>{axis_table}</div>"
        "<div class='card'><h2>What this test can prove</h2>"
        "<p>This is a host-observed Raw HID result downstream of the controller firmware and USB transport. It can document report cadence, repeated bytes, output noise, and the amount of variation left after the slow-trend comparison.</p>"
        "<p>It cannot identify whether smoothing was introduced by the sensor, analog circuit, firmware, USB transport, or the host. Use the Electrical Trace test with a real upstream probe and a hardware trigger to support that attribution.</p></div>"
        f"<div class='card'><h2>Source metadata</h2><pre>{escape(json.dumps(result.get('device_metadata') or {}, indent=2, default=str))}</pre></div>"
    )
    destination.write_text(_report_shell(title, body), encoding="utf-8")
    return destination


def write_trace_evidence_report(
    destination: str | Path,
    result: dict,
    *,
    title: str = "RcmTool Electrical Trace Evidence",
) -> Path:
    """Write a plain-language report for a sigrok instrument capture."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    synchronization = result.get("synchronization") or {}
    method = synchronization.get("method", "not recorded")
    synchronized = method == "hardware-trigger-assisted"
    channel_rows = []
    for name, metrics in (result.get("channels") or {}).items():
        channel_rows.append(
            "<tr>"
            f"<td>{escape(str(name))}</td>"
            f"<td>{int(metrics.get('samples', 0))}</td>"
            f"<td>{float(metrics.get('sample_rate_hz', 0.0)):.3f}</td>"
            f"<td>{float(metrics.get('mean', 0.0)):.8g}</td>"
            f"<td>{float(metrics.get('noise_rms', 0.0)):.8g}</td>"
            f"<td>{float(metrics.get('peak_to_peak', 0.0)):.8g}</td>"
            f"<td>{escape(str(metrics.get('edge_rate_hz', 'n/a')))}</td>"
            "</tr>"
        )
    channel_table = (
        "<table><thead><tr><th>Channel</th><th>Samples</th><th>Sample rate</th><th>Mean</th>"
        "<th>Noise RMS</th><th>Peak-to-peak</th><th>Edge rate</th></tr></thead>"
        f"<tbody>{''.join(channel_rows) or '<tr><td colspan=7>Unavailable</td></tr>'}</tbody></table>"
    )
    if synchronized:
        bottom_line = "A hardware-trigger-assisted capture was requested. Verify the physical trigger wiring and pretrigger configuration before making a causal claim."
    else:
        bottom_line = "This capture is not hardware-synchronized with the Raw HID stream. It can describe the electrical trace, but it cannot establish electrical-to-USB timing or firmware causation."
    body = (
        f"<div class='card'><h2>Bottom line</h2><p>{escape(bottom_line)}</p></div>"
        f"<div class='card'><h2>Capture summary</h2>{_metric_grid({
            'Evidence class': result.get('evidence_class', 'Unavailable'),
            'Source': result.get('source', 'Unavailable'),
            'Instrument samples': result.get('sample_count', 0),
            'Duration': f"{float(result.get('duration_s', 0.0)):.6f} s",
            'Synchronization': method,
            'Raw capture': result.get('raw_capture_path', 'Unavailable'),
        })}</div>"
        "<div class='card'><h2>How to read the numbers</h2>"
        "<ul><li><b>Sample rate</b> is the rate represented by the timestamps in the exported sigrok CSV, not merely the requested device setting.</li>"
        "<li><b>Noise RMS</b> is the channel variation around its mean. For a digital channel, it is usually less useful than edge timing and duty cycle.</li>"
        "<li><b>Peak-to-peak</b> is the observed minimum-to-maximum span in the captured window.</li>"
        "<li><b>Edge rate</b> is reported only for channels that look digital-like. Analog sensor channels require probe-specific step and spectrum analysis.</li></ul></div>"
        f"<div class='card'><h2>Channel results</h2>{channel_table}</div>"
        f"<div class='card'><h2>Synchronization record</h2><pre>{escape(json.dumps(synchronization, indent=2, default=str))}</pre></div>"
        "<div class='card'><h2>Required attribution boundary</h2><p>A trace can support a firmware-filtering claim only when the upstream electrical signal, the Raw HID output, and their timing relationship are captured with a known physical connection and a valid shared trigger. Software host-start alignment alone is not enough.</p></div>"
    )
    destination.write_text(_report_shell(title, body), encoding="utf-8")
    return destination
