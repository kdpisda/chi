"""The holdout gate: does the champion's win survive a workload it never saw?

chi's correctness gate is already held out — candidates never see reference
outputs. Its *score* was not. Every autoresearch loop optimises against one
frozen benchmark, so the loop's own selection pressure pushes candidates toward
that benchmark's particulars: its input size, its RNG seed, its shape. The
result is a number that moved and a program that didn't get faster.

This is the failure mode the ecosystem keeps rediscovering and no harness gates
on. The most-cited autoresearch result to date (a 53% parse/render win) shipped
with its author's own caveat that it was "probably somewhat overfit"; a widely
reported test-suite speedup measured 163s -> 100s locally and 14min -> 13min in
CI. Both are the same bug: the benchmark improved, the workload didn't.

So chi measures the gap instead of hoping it's zero. A problem may declare a
`holdout:` block — a second scoring command over a DIFFERENT workload, whose
files never enter an agent workdir. When a champion is crowned, chi re-scores it
on that held-out workload and compares the gain it *claimed* on the optimised
benchmark with the gain it actually *realised*:

    generalization = holdout gain % / benchmark gain %

A champion that claims 50% and realises 45% generalizes. One that claims 50% and
realises 3% is overfit to the benchmark, and chi says so — before you ship it.
"""

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from chi.config import ProblemConfig
from chi.eval.sample import sample_score

GENERALIZES = "generalizes"
OVERFIT = "overfit"
REGRESSED = "regressed"
UNAVAILABLE = "unavailable"


@dataclass
class HoldoutVerdict:
    verdict: str  # generalizes | overfit | regressed | unavailable
    baseline_holdout: float | None = None
    champion_holdout: float | None = None
    claimed_gain_pct: float | None = None
    holdout_gain_pct: float | None = None
    generalization: float | None = None
    samples: list[float] = field(default_factory=list)
    benchmarks_run: int = 0
    detail: str = ""

    @property
    def generalizes(self) -> bool:
        """True only on an affirmative pass — an unmeasurable holdout is not a pass."""
        return self.verdict == GENERALIZES

    @property
    def shippable(self) -> bool:
        """Whether chi will let this champion satisfy a goal or be exported clean.

        An `unavailable` holdout does NOT block: the gate reports what it can
        measure and never invents a failure out of its own breakage. Only a
        measured `overfit`/`regressed` verdict withholds the champion.
        """
        return self.verdict in (GENERALIZES, UNAVAILABLE)

    def as_payload(self) -> dict:
        return {
            "verdict": self.verdict,
            "baseline_holdout": self.baseline_holdout,
            "champion_holdout": self.champion_holdout,
            "claimed_gain_pct": self.claimed_gain_pct,
            "holdout_gain_pct": self.holdout_gain_pct,
            "generalization": self.generalization,
            "benchmarks_run": self.benchmarks_run,
            "detail": self.detail,
        }


def gain_pct(baseline: float, score: float, direction: str) -> float:
    """Percent improvement of score over baseline, signed by the score direction."""
    if baseline == 0:
        return 0.0
    if direction == "minimize":
        return (baseline - score) / abs(baseline) * 100.0
    return (score - baseline) / abs(baseline) * 100.0


class HoldoutGate:
    """Re-score a champion on a held-out workload and rate its generalization."""

    def __init__(self, benchmark_fn: Callable, *, repeats: int = 3,
                 direction: str = "minimize", min_generalization: float = 0.5,
                 max_regression_pct: float = 1.0) -> None:
        self._benchmark = benchmark_fn
        self._repeats = max(1, repeats)
        self._direction = direction
        self._min_generalization = min_generalization
        self._max_regression = max_regression_pct

    def measure(self, candidate: Path) -> tuple[float | None, list[float], str]:
        """Median holdout score over `repeats` samples; None if too few landed."""
        samples: list[float] = []
        detail = ""
        for _ in range(self._repeats):
            result = self._benchmark(candidate)
            if result.ok and result.score_us is not None:
                samples.append(float(result.score_us))
            else:
                detail = result.detail
        if not samples:
            return None, samples, detail or "no holdout samples"
        return statistics.median(samples), samples, detail

    def verify(self, candidate: Path, *, baseline_holdout: float | None,
               baseline_score: float | None, champion_score: float | None
               ) -> HoldoutVerdict:
        """Compare the champion's claimed gain with the gain it realises held out."""
        if baseline_holdout is None:
            return HoldoutVerdict(UNAVAILABLE,
                                  detail="no holdout baseline was measured")
        if baseline_score is None or champion_score is None:
            return HoldoutVerdict(UNAVAILABLE, baseline_holdout=baseline_holdout,
                                  detail="no benchmark baseline/champion score to compare")

        median, samples, detail = self.measure(candidate)
        runs = len(samples)
        if median is None:
            return HoldoutVerdict(UNAVAILABLE, baseline_holdout=baseline_holdout,
                                  samples=samples, benchmarks_run=runs,
                                  detail=f"holdout unmeasurable: {detail}")

        claimed = gain_pct(baseline_score, champion_score, self._direction)
        realised = gain_pct(baseline_holdout, median, self._direction)
        generalization = realised / claimed if claimed > 0 else None

        common = {
            "baseline_holdout": baseline_holdout, "champion_holdout": median,
            "claimed_gain_pct": claimed, "holdout_gain_pct": realised,
            "generalization": generalization, "samples": samples,
            "benchmarks_run": runs,
        }
        if realised < -self._max_regression:
            return HoldoutVerdict(
                REGRESSED, **common,
                detail=(f"claims {claimed:+.1f}% on the benchmark but is"
                        f" {realised:+.1f}% on the held-out workload"))
        if claimed <= 0:
            return HoldoutVerdict(
                GENERALIZES, **common,
                detail=(f"no benchmark gain claimed ({claimed:+.1f}%); held-out"
                        f" workload {realised:+.1f}% (no regression)"))
        if generalization is not None and generalization < self._min_generalization:
            return HoldoutVerdict(
                OVERFIT, **common,
                detail=(f"claims {claimed:+.1f}% on the benchmark but realises only"
                        f" {realised:+.1f}% held out"
                        f" ({generalization:.0%} of the claim, floor"
                        f" {self._min_generalization:.0%})"))
        return HoldoutVerdict(
            GENERALIZES, **common,
            detail=(f"claims {claimed:+.1f}%, realises {realised:+.1f}% held out"
                    + (f" ({generalization:.0%} of the claim)"
                       if generalization is not None else "")))


def holdout_benchmark_fn(problem: ProblemConfig, holdout_dir: Path) -> Callable:
    """One holdout sample: copy the candidate into chi's private holdout dir and score it.

    The candidate travels to the evaluator, never the other way round — the
    holdout workload's files stay in a directory no agent ever gets a copy of.
    """
    holdout = problem.holdout
    assert holdout is not None, "holdout_benchmark_fn requires problem.holdout"
    holdout_dir = Path(holdout_dir)

    def bench(candidate: Path):
        candidate = Path(candidate)
        target = holdout_dir / problem.candidate
        if candidate.resolve() != target.resolve():
            target.write_bytes(candidate.read_bytes())
        return sample_score(holdout.benchmark, holdout_dir, problem.candidate,
                            holdout.timeout_seconds or problem.timeout_seconds)

    return bench


def build_holdout_gate(problem: ProblemConfig, holdout_dir: Path) -> HoldoutGate | None:
    """The gate for this problem, or None when it declares no holdout."""
    if problem.holdout is None:
        return None
    return HoldoutGate(
        holdout_benchmark_fn(problem, holdout_dir),
        repeats=problem.holdout.repeats,
        direction=problem.score.direction,
        min_generalization=problem.holdout.min_generalization,
        max_regression_pct=problem.holdout.max_regression_pct,
    )
