"""Median-of-N re-benchmark: tell a real improvement from ~8% B200 noise.

The same cholesky kernel measured 636/652/686µs across runs. A single benchmark
that beats the champion may be luck. Re-benchmark N times and believe the win
only if the MEDIAN clears the champion by the promote margin. Fires only on
apparent winners, so the extra B200 cost is bounded.
"""

import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class VerifyResult:
    is_real_improvement: bool
    median_score: float | None
    samples: list = field(default_factory=list)
    benchmarks_run: int = 0
    detail: str = ""


class NoiseGuard:
    """Re-benchmark an apparent winner N times; median must beat the champion."""

    def __init__(self, benchmark_fn: Callable, n: int = 3,
                 direction: str = "minimize", promote_margin_pct: float = 0.5) -> None:
        self._benchmark = benchmark_fn
        self._n = n
        self._direction = direction
        self._margin = promote_margin_pct

    def verify(self, candidate: Path, champion_score: float) -> VerifyResult:
        samples: list[float] = []
        for _ in range(self._n):
            result = self._benchmark(candidate)
            if result.ok and result.score_us is not None:
                samples.append(float(result.score_us))
        if len(samples) < math.ceil(self._n / 2):
            return VerifyResult(False, None, samples, self._n,
                                f"insufficient samples ({len(samples)}/{self._n})")
        median = statistics.median(samples)
        if self._direction == "minimize":
            real = median < champion_score * (1 - self._margin / 100)
        else:
            real = median > champion_score * (1 + self._margin / 100)
        return VerifyResult(real, median, samples, self._n,
                            f"median {median:.1f} vs champion {champion_score:.1f}")
