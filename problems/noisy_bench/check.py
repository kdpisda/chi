"""Structural correctness gate: solve(xs) must return prefix sums of xs.

Kept simple on purpose — the interesting part of this pack is the noisy
benchmark, not the correctness check.
"""

import argparse
import importlib.util
import itertools
import json
import random
import sys


def _load(path: str):
    spec = importlib.util.spec_from_file_location("candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate")
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    xs = [rng.uniform(-100.0, 100.0) for _ in range(200)]
    got = _load(args.candidate).solve(list(xs))
    want = list(itertools.accumulate(xs))
    if not isinstance(got, list) or len(got) != len(want):
        print(json.dumps({"error": "expected a list of prefix sums"}))
        return 1
    max_abs_error = max(abs(g - w) for g, w in zip(got, want))
    print(json.dumps({"max_abs_error": max_abs_error}))
    return 0 if max_abs_error <= 1e-6 else 1


if __name__ == "__main__":
    sys.exit(main())
