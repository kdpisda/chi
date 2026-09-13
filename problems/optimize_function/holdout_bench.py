"""Held-out benchmark: the SAME solve(), a workload the fleet never optimises against.

bench.py always times one 4000-element input built from `random.Random(7)`. That
is the number agents hill-climb, so it is the number they can overfit: a
candidate that special-cases length 4000, memoises that exact input, or tunes a
threshold to it wins the benchmark without getting faster.

This file scores a deliberately different workload — several input sizes, a
different seed, and a different call shape — and chi never copies it into an
agent workdir (`holdout.files` in problem.yaml). A real algorithmic improvement
shows up here too; a benchmark-shaped one does not.
"""

import importlib.util
import json
import random
import sys
import time

SIZES = (900, 3300, 9000)
SEED = 4242


def _load(path: str):
    spec = importlib.util.spec_from_file_location("candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    candidate = _load(sys.argv[1])
    rng = random.Random(SEED)
    workloads = [[rng.uniform(-100.0, 100.0) for _ in range(n)] for n in SIZES]
    for xs in workloads:  # warmup, same shape as the timed section
        candidate.solve(list(xs))
    start = time.perf_counter()
    for xs in workloads:
        candidate.solve(list(xs))
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    print(json.dumps({"score": elapsed_ms}))


if __name__ == "__main__":
    main()
