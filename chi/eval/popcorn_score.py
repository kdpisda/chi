"""Parse the real B200 geomean from popcorn-cli benchmark stdout.

The blocker that made chi fabricate scores: popcorn's `--mode benchmark` leaves
the JSON `score` field null — the real per-shape timings are printed as text:

    n: 32; cond: 2; seed: 41032; batch: 4096
     ⏱ 26.3 ± 0.70 µs
    ...
    n: 32768; cond: 2; seed: 48368; batch: 1
     ⏱ 35.2 ± 0.01 ms

The leaderboard score is the geometric mean of the per-shape mean runtimes.
Verified: parsing codex_v652's 15-shape benchmark output and taking the geomean
reproduces its ranked leaderboard score (0.000637 s = 637 µs) exactly.

Never fabricate: if no timing lines are present, return None so the eval fails
honestly rather than inventing a number.
"""

import math
import re

# " ⏱ 26.3 ± 0.70 µs"  /  " ⏱ 120 ± 0.1 µs"  /  " ⏱ 4.98 ± 0.005 ms"
_TIMING = re.compile(r"⏱\s*([\d.]+)\s*±\s*[\d.]+\s*(µs|us|ms|ns|s)\b")

_TO_US = {"ns": 1e-3, "µs": 1.0, "us": 1.0, "ms": 1e3, "s": 1e6}


def parse_shape_times_us(text: str) -> list[float]:
    """Every per-shape mean runtime from popcorn benchmark stdout, in microseconds."""
    out: list[float] = []
    for value, unit in _TIMING.findall(text):
        try:
            out.append(float(value) * _TO_US[unit])
        except (ValueError, KeyError):
            continue
    return out


def parse_geomean_us(text: str) -> float | None:
    """Geometric mean of per-shape runtimes (µs) = the leaderboard score.

    None when no timing lines are present (fail honestly, never fabricate).
    """
    times = parse_shape_times_us(text)
    if not times:
        return None
    log_sum = sum(math.log(t) for t in times if t > 0)
    n = sum(1 for t in times if t > 0)
    if n == 0:
        return None
    return math.exp(log_sum / n)
