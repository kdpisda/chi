"""Autonomous leaderboard submission with safety rails.

A complete autoresearch harness closes the loop itself: when a fleet finds a
real improvement it should submit — no human in the path for each one. But
"real" is load-bearing. Three rails, or auto-submit actively hurts the user
(their own campaign saw benchmark-overfit "wins" regress on the frozen harness
three times):

1. Correctness: never submit a candidate that fails the held-out check.
2. Margin: only submit when the benchmark beats the last submitted score by
   more than `margin_pct` — noise must not trigger a submission.
3. Serialize + ration: the actual submit goes through the backend's
   SubmissionGate (mutex + rate limit), so one at a time, within budget.

The improvement check and the submit happen under one lock, so two agents that
improve at once can't both fire on near-identical scores.
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class SubmitDecision:
    submitted: bool
    reason: str
    score: float | None = None


class AutoSubmitter:
    """Thread-safe gatekeeper: decide + submit a benchmarked candidate."""

    def __init__(
        self,
        backend,
        direction: str = "minimize",
        margin_pct: float = 0.5,
        baseline: float | None = None,
        emit: Callable[[str], None] | None = None,
        enabled: bool = True,
    ) -> None:
        self.backend = backend
        self.direction = direction
        # clamp the margin to a sane floor: a 0/negative margin would let noise or
        # regressions submit and drop the user's real rank (rank-protection rail)
        self.margin_pct = max(float(margin_pct), 0.1)
        self.enabled = enabled
        self._last = baseline  # last submitted score (seed with current LB best)
        self._lock = threading.Lock()
        self._emit = emit or (lambda line: None)

    @property
    def last_submitted(self) -> float | None:
        return self._last

    def _improves(self, score: float) -> bool:
        if self._last is None:
            return True
        if self.direction == "minimize":
            return score < self._last * (1.0 - self.margin_pct / 100.0)
        return score > self._last * (1.0 + self.margin_pct / 100.0)

    def consider(self, candidate: Path, score: float | None, correct: bool) -> SubmitDecision:
        """Submit iff enabled, correct, and a real improvement — else say why."""
        if not self.enabled:
            return SubmitDecision(False, "auto-submit disabled", score)
        if not correct:
            return SubmitDecision(False, "candidate not correct — never submitted", score)
        if score is None:
            return SubmitDecision(False, "no benchmark score", None)
        with self._lock:
            if not self._improves(score):
                return SubmitDecision(
                    False,
                    f"{score} is not a >{self.margin_pct}% improvement over"
                    f" {self._last} — held (noise/regression guard)",
                    score,
                )
            result = self.backend.submit(Path(candidate))
            if getattr(result, "rationed", False):
                return SubmitDecision(False, f"rationed — {result.detail}", score)
            if not result.ok:
                return SubmitDecision(False, f"submit failed — {result.detail}", score)
            self._last = score
            self._emit(f"✓ auto-submitted {score} to the leaderboard ({result.detail})")
            return SubmitDecision(True, result.detail, score)
