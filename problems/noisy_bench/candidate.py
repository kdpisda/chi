"""Baseline candidate: intentionally naive O(n^2) prefix sums. Improve me —
but expect the benchmark to lie by ~10% on any single run."""


def solve(xs: list[float]) -> list[float]:
    """Prefix sums of xs."""
    return [sum(xs[: i + 1]) for i in range(len(xs))]
