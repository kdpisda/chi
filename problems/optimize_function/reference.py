"""Reference implementation: prefix sums (held-out ground truth)."""

import itertools


def reference_solve(xs: list[float]) -> list[float]:
    """Prefix sums of xs."""
    return list(itertools.accumulate(xs))
