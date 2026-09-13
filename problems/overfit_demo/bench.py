"""Benchmark: time one solve() call on a fixed 4000-element input; print JSON score."""

import importlib.util
import json
import random
import sys
import time


def _load(path: str):
    spec = importlib.util.spec_from_file_location("candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    candidate = _load(sys.argv[1])
    rng = random.Random(7)
    xs = [rng.uniform(-100.0, 100.0) for _ in range(4000)]
    candidate.solve(list(xs))  # warmup
    start = time.perf_counter()
    candidate.solve(list(xs))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    print(json.dumps({"score": elapsed_ms}))


if __name__ == "__main__":
    main()
