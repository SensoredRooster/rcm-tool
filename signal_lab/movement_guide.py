"""The guided movement step: what each stick should do, second by second.

Both sticks are tested in turn: the left stick for the first 10 seconds, then
the right. Each stick runs the same pattern: hold center, push to full travel
and hold, let go so it springs back and settles, then quick reversals left to
right and up to down. The on-screen ring follows this pattern so the tester
can match it, and the recorded samples are scored against it afterwards.
"""
from __future__ import annotations

import math
from typing import Sequence

STICK_SECONDS = 10.0
MOVEMENT_SECONDS = 2 * STICK_SECONDS
# A person needs time to react to the ring; a position counts as on target if
# it matches where the ring was at any point in the last REACTION_S seconds.
REACTION_S = 0.4
ON_TARGET_DISTANCE = 0.3
OTHER_STICK_STILL_DISTANCE = 0.15
FULL_TRAVEL = 0.9

# (start s, end s, target at start, target at end, instruction), per stick.
PATTERN: tuple[tuple[float, float, tuple[float, float], tuple[float, float], str], ...] = (
    (0.00, 1.00, (0, 0), (0, 0), "Hold the stick at center"),
    (1.00, 1.60, (0, 0), (1, 0), "Push fully RIGHT"),
    (1.60, 2.60, (1, 0), (1, 0), "Hold full RIGHT"),
    (2.60, 2.80, (1, 0), (0, 0), "Let go: it springs back to center"),
    (2.80, 3.80, (0, 0), (0, 0), "Hands off: let it settle"),
    (3.80, 4.40, (0, 0), (-1, 0), "Push fully LEFT"),
    (4.40, 5.00, (-1, 0), (-1, 0), "Hold full LEFT"),
    (5.00, 5.25, (-1, 0), (1, 0), "Flick quickly to full RIGHT"),
    (5.25, 5.75, (1, 0), (1, 0), "Hold full RIGHT"),
    (5.75, 5.95, (1, 0), (0, 0), "Let go"),
    (5.95, 6.60, (0, 0), (0, 0), "Hands off: let it settle"),
    (6.60, 7.10, (0, 0), (0, 1), "Push fully UP"),
    (7.10, 7.50, (0, 1), (0, 1), "Hold full UP"),
    (7.50, 7.75, (0, 1), (0, -1), "Flick quickly to full DOWN"),
    (7.75, 8.25, (0, -1), (0, -1), "Hold full DOWN"),
    (8.25, 8.45, (0, -1), (0, 0), "Let go"),
    (8.45, 10.00, (0, 0), (0, 0), "Hands off: let it settle"),
)


def guide_at(elapsed_s: float) -> dict:
    """Which stick is active, where its ring is, and what to do right now."""
    t = min(max(0.0, float(elapsed_s)), MOVEMENT_SECONDS - 1e-9)
    stick = "left" if t < STICK_SECONDS else "right"
    local = t - (0.0 if stick == "left" else STICK_SECONDS)
    index = next(i for i, phase in enumerate(PATTERN) if local < phase[1] or i == len(PATTERN) - 1)
    start, end, (x0, y0), (x1, y1), text = PATTERN[index]
    fraction = 0.0 if end <= start else min(1.0, (local - start) / (end - start))
    if index + 1 < len(PATTERN):
        upcoming = PATTERN[index + 1][4]
    elif stick == "left":
        upcoming = "Switch to the RIGHT stick"
    else:
        upcoming = "Done"
    return {
        "stick": stick,
        "target": (x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction),
        "instruction": text,
        "next": upcoming,
        "phase_seconds_left": max(0.0, end - local),
        "seconds_left": MOVEMENT_SECONDS - t,
    }


def on_target(elapsed_s: float, x: float, y: float) -> bool:
    """True if (x, y) matches where the ring was within the reaction window."""
    for step in range(5):
        lag = REACTION_S * step / 4
        tx, ty = guide_at(max(0.0, elapsed_s - lag))["target"]
        if math.hypot(x - tx, y - ty) <= ON_TARGET_DISTANCE:
            return True
    return False


def score_movement(timestamps_ns: Sequence[int], samples: Sequence[dict], start_ns: int) -> dict:
    """How well a recorded movement step followed the guide.

    followed_percent: share of samples where the active stick was on target
    (within 0.3 of the ring's position during the last 0.4 s).
    other_stick_still_percent: share where the resting stick stayed within
    0.15 of center. reach: the largest travel seen in each direction.
    """
    count = min(len(timestamps_ns), len(samples))
    stride = max(1, count // 4000)  # at most ~4,000 scored points, even at 8 kHz
    followed = still = scored = 0
    reach = {stick: {"right": 0.0, "left": 0.0, "up": 0.0, "down": 0.0} for stick in ("left", "right")}
    for index in range(count):
        sample = samples[index]
        for stick, (xk, yk) in (("left", ("lx", "ly")), ("right", ("rx", "ry"))):
            x, y = float(sample.get(xk, 0.0)), float(sample.get(yk, 0.0))
            travel = reach[stick]
            travel["right"] = max(travel["right"], x)
            travel["left"] = max(travel["left"], -x)
            travel["up"] = max(travel["up"], y)
            travel["down"] = max(travel["down"], -y)
        if index % stride:
            continue
        elapsed = (int(timestamps_ns[index]) - int(start_ns)) / 1e9
        if not 0.0 <= elapsed < MOVEMENT_SECONDS:
            continue
        active = guide_at(elapsed)["stick"]
        ax, ay, ox, oy = ("lx", "ly", "rx", "ry") if active == "left" else ("rx", "ry", "lx", "ly")
        scored += 1
        followed += on_target(elapsed, float(sample.get(ax, 0.0)), float(sample.get(ay, 0.0)))
        still += math.hypot(float(sample.get(ox, 0.0)), float(sample.get(oy, 0.0))) <= OTHER_STICK_STILL_DISTANCE
    full = {
        stick: all(value >= FULL_TRAVEL for value in directions.values())
        for stick, directions in reach.items()
    }
    return {
        "pattern": "left stick 0-10 s, right stick 10-20 s: center, full right, spring back, full left, "
                   "flick right, spring back, full up, flick down, spring back",
        "scored_points": scored,
        "followed_percent": round(100.0 * followed / scored, 1) if scored else None,
        "other_stick_still_percent": round(100.0 * still / scored, 1) if scored else None,
        "reach": {stick: {k: round(v, 3) for k, v in directions.items()} for stick, directions in reach.items()},
        "full_travel_reached": full,
    }
