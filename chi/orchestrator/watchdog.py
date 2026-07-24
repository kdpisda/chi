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

    def observe_iteration(self, *, new_evals: int, candidate_hash: str) -> WatchdogVerdict:
        """Feed one finished iteration; get the verdict for what to do next."""
        if new_evals > 0:
            self._iters_without_eval = 0
        else:
            self._iters_without_eval += 1
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
