"""Deterministic, zero-LLM watchdog. The nemotron rule lives here."""

from dataclasses import dataclass

from chi.config import PoliciesCfg


@dataclass
class WatchdogVerdict:
    action: str  # "ok" | "mutate" | "kill"
    reason: str = ""


class Watchdog:
    """Detects looping and eval-starved agents from cheap per-iteration signals."""

    def __init__(self, policies: PoliciesCfg) -> None:
        self._policies = policies
        self._iters_without_eval = 0
        self._last_hash: str | None = None
        self._hash_streak = 0
        self._timeout_streak = 0

    def seed(self, *, iters_without_eval: int = 0, last_hash: str | None = None,
             hash_streak: int = 0) -> None:
        """Restore counters from a prior slice's history (the store).

        Under the director the fleet runs in short slices; a per-slice watchdog
        could never accumulate to its kill thresholds, so a dead or looping coder
        was never reaped. Seeding makes the rules hold across slices.
        """
        self._iters_without_eval = iters_without_eval
        self._last_hash = last_hash
        self._hash_streak = hash_streak

    def preflight(self) -> WatchdogVerdict:
        """Verdict from seeded history alone, before running any iteration.

        Only the eval-recency rule fires here: a coder with a long trail of
        zero-eval iterations (a CLI erroring instantly) must not burn another
        iteration every slice. The repeat-hash rule stays iteration-driven — a
        plateaued-but-alive coder should still get to try under new steering.
        """
        if self._iters_without_eval >= self._policies.eval_recency_iters:
            return WatchdogVerdict(
                "kill", f"preflight: no eval datapoints in {self._iters_without_eval}"
                        " iterations across slices")
        return WatchdogVerdict("ok")

    def observe_iteration(self, *, new_evals: int, candidate_hash: str,
                          note: str = "") -> WatchdogVerdict:
        """Feed one finished iteration; get the verdict for what to do next."""
        if new_evals > 0:
            self._iters_without_eval = 0
        else:
            self._iters_without_eval += 1
        if new_evals == 0 and note == "timeout":
            self._timeout_streak += 1
        else:
            self._timeout_streak = 0
        if candidate_hash == self._last_hash:
            self._hash_streak += 1
        else:
            self._hash_streak = 1
            self._last_hash = candidate_hash

        recency_cap = self._policies.eval_recency_iters
        if self._iters_without_eval >= recency_cap:
            return WatchdogVerdict(
                "kill", f"no new eval datapoints in {self._iters_without_eval} iterations"
            )
        # a zero-eval TIMEOUT is a doomed-edit signal, not mere idleness: mutate
        # on the very first one so the next seed steers toward a smaller edit,
        # instead of waiting for the recency-half mutate or the recency kill
        if self._timeout_streak >= 1:
            return WatchdogVerdict(
                "mutate",
                f"iteration timed out with no measured result ({self._timeout_streak}"
                " in a row) — make a much smaller, faster edit",
            )
        if self._iters_without_eval == max(1, recency_cap // 2):
            return WatchdogVerdict(
                "mutate", "no eval datapoints recently — produce measured results now"
            )
        if self._hash_streak >= 2 * self._policies.repeat_k:
            return WatchdogVerdict(
                "kill", f"candidate unchanged {self._hash_streak} consecutive iterations"
            )
        if self._hash_streak == self._policies.repeat_k:
            return WatchdogVerdict(
                "mutate",
                f"candidate unchanged {self._hash_streak} times — try a different approach",
            )
        return WatchdogVerdict("ok")
