"""Correctness gate: compare candidate solve() to the reference on a seeded input."""

import argparse
import importlib.util
import json
import random
import sys
from pathlib import Path


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate")
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    xs = [rng.uniform(-100.0, 100.0) for _ in range(500)]
    got = _load(args.candidate, "candidate").solve(list(xs))
    want = _load(str(Path(__file__).with_name("reference.py")), "reference").reference_solve(xs)
    if len(got) != len(want):
        print(json.dumps({"error": "length mismatch"}))
        return 1
    max_abs_error = max(abs(g - w) for g, w in zip(got, want))
    print(json.dumps({"max_abs_error": max_abs_error}))
    return 0 if max_abs_error <= 1e-6 else 1


if __name__ == "__main__":
    sys.exit(main())
