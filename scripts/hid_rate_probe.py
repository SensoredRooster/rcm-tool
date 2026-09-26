"""Measure a controller's Raw HID report rate with nothing else running.

Usage (from the repository folder):
    python scripts\\hid_rate_probe.py            20-second run
    python scripts\\hid_rate_probe.py 30         30-second run

Leave the controller untouched for the first half, then move both sticks.
Each second it prints how many reports arrived, how many were identical to the
previous report, and the longest gap between reports.

How to read it:
- The same rate here as in RcmTool means RcmTool is receiving everything the
  controller sends.
- A rate that rises when you move the sticks, with few or no repeated reports,
  means the controller only sends a report when its input changes. Its "8K"
  rating is then a maximum, not a constant stream.
- A steady ~8,000 here while RcmTool shows much less means RcmTool is still
  losing reports.
"""
from __future__ import annotations

import sys
import time

import hid


def pick_controller() -> dict:
    pads = [info for info in hid.enumerate() if info.get("usage_page") == 1 and info.get("usage") in (4, 5)]
    if not pads:
        sys.exit("No controller found. Plug it in by USB and run this again.")
    for index, info in enumerate(pads):
        print(f"[{index}] {info.get('product_string') or 'unnamed'}  "
              f"VID {int(info['vendor_id']):04X}  PID {int(info['product_id']):04X}")
    if len(pads) == 1:
        return pads[0]
    return pads[int(input("Type the controller number and press Enter: ") or 0)]


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    info = pick_controller()
    device = hid.device()
    device.open_path(info["path"])
    device.set_nonblocking(True)
    print(f"\nReading for {seconds:g} s. Leave the controller untouched until halfway, then move both sticks.\n")
    print(" time   reports/s   repeated   longest gap")
    start = time.perf_counter()
    second_end = start + 1.0
    count = repeats = 0
    longest_gap_ns = 0
    last_report = None
    last_arrival = None
    total = 0
    try:
        while True:
            report = device.read(256, 50)   # waits for the next report, up to 50 ms
            now = time.perf_counter()
            if report:
                arrival = time.perf_counter_ns()
                payload = bytes(report)
                count += 1
                repeats += payload == last_report
                last_report = payload
                if last_arrival is not None:
                    longest_gap_ns = max(longest_gap_ns, arrival - last_arrival)
                last_arrival = arrival
            if now >= second_end:
                elapsed = int(round(second_end - start))
                phase = "untouched" if elapsed <= seconds / 2 else "MOVE STICKS"
                print(f"{elapsed:4d}s   {count:9,d}   {repeats:8,d}   {longest_gap_ns / 1e6:8.2f} ms   {phase}")
                total += count
                count = repeats = 0
                longest_gap_ns = 0
                second_end += 1.0
                if now - start >= seconds:
                    break
    finally:
        device.close()
    print(f"\nAverage: {total / seconds:,.0f} reports per second")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
