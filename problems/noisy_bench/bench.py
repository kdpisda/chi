"""Deliberately NOISY benchmark: deterministic op-count base × ~±10% per-run noise.

Base cost = the number of Python line events executed inside solve() on a fixed
input, so an O(n) rewrite really scores lower than the O(n^2) baseline. Each run
then applies a noise factor in [0.9, 1.1] hashed from the candidate source plus
a per-run counter (persisted next to the candidate): repeated benchmarks of the
SAME candidate spread like real hardware noise, yet the whole sequence replays
identically from a fresh workdir. A single sample can lie — that is the point;
the Director's median-of-N NoiseGuard has to arbitrate the wins.
"""

import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path

INPUT_SIZE = 300
COUNTER_FILE = ".noisy_bench_runs"


def _load(path: str):
    spec = importlib.util.spec_from_file_location("candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _count_line_events(fn, xs: list[float]) -> int:
    """Run fn(xs) under a tracer, counting executed Python lines — a
    deterministic proxy for algorithmic cost (no wall-clock jitter)."""
    count = 0

    def tracer(frame, event, arg):
        nonlocal count
        if event == "line":
            count += 1
        return tracer

    sys.settrace(tracer)
    try:
        fn(xs)
    finally:
        sys.settrace(None)
    return count


def _next_run_index(workdir: Path) -> int:
    """Bump the per-workdir run counter so successive runs draw fresh noise."""
    counter_file = workdir / COUNTER_FILE
    run_index = int(counter_file.read_text()) if counter_file.exists() else 0
    counter_file.write_text(str(run_index + 1))
    return run_index


def _noise_factor(source: str, run_index: int) -> float:
    """Deterministic pseudo-noise in [0.9, 1.1] from (candidate, run) identity."""
    digest = hashlib.sha256(f"{source}\n#run={run_index}".encode()).hexdigest()
    return 0.9 + 0.2 * (int(digest[:8], 16) / 0xFFFFFFFF)


def main() -> None:
    candidate_path = Path(sys.argv[1])
    candidate = _load(str(candidate_path))
    rng = random.Random(5)
    xs = [rng.uniform(-100.0, 100.0) for _ in range(INPUT_SIZE)]
    base_ops = _count_line_events(candidate.solve, xs)
    factor = _noise_factor(candidate_path.read_text(),
                           _next_run_index(candidate_path.parent))
    print(json.dumps({"score": base_ops * factor}))


if __name__ == "__main__":
    main()
