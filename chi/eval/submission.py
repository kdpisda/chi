"""Submission gate: leaderboard submissions go one at a time; benchmarks don't.

Benchmarking a candidate (getting a B200 time) is cheap and parallel — every
fleet agent can do it concurrently. An actual ranked leaderboard submission is
different: rank = your best submission, the endpoint is rate-limited, and racing
submissions wastes budget. So submissions pass through a process-global mutex
plus a token-bucket rate limit; benchmarks never touch the gate.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class RationDenied(Exception):
    """Raised when a submission is refused because the rate window is full."""

    retry_after_seconds: float

    def __str__(self) -> str:
        return f"submission rationed — retry in {self.retry_after_seconds:.0f}s"


class SubmissionGate:
    """Serializes leaderboard submissions (mutex) and rations them (token bucket).

    One instance guards one leaderboard. `submit` blocks on the mutex so only one
    submission is ever in flight; within the mutex it enforces max_per_window
    submissions per window_seconds. `benchmark` is intentionally NOT gated.
    """

    def __init__(
        self,
        max_per_window: int = 1,
        window_seconds: float = 1800.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self._clock = clock
        self._mutex = threading.Lock()
        self._recent: list[float] = []  # submission timestamps within the window

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._recent = [t for t in self._recent if t > cutoff]

    def tokens_available(self) -> int:
        """How many submissions could go right now without being rationed."""
        with self._mutex:
            self._prune(self._clock())
            return max(0, self.max_per_window - len(self._recent))

    def retry_after(self) -> float:
        """Seconds until a token frees up (0 if one is available now)."""
        with self._mutex:
            now = self._clock()
            self._prune(now)
            if len(self._recent) < self.max_per_window:
                return 0.0
            return self.window_seconds - (now - min(self._recent))

    def submit(self, action: Callable[[], object], *, wait: bool = False) -> object:
        """Run `action` as a serialized, rationed leaderboard submission.

        Only one submission runs at a time (mutex). If the window is full: block
        until a token frees when wait=True, else raise RationDenied immediately.
        """
        with self._mutex:
            now = self._clock()
            self._prune(now)
            if len(self._recent) >= self.max_per_window:
                wait_s = self.window_seconds - (now - min(self._recent))
                if not wait:
                    raise RationDenied(retry_after_seconds=max(0.0, wait_s))
                time.sleep(max(0.0, wait_s))
                now = self._clock()
                self._prune(now)
            self._recent.append(now)
            return action()


# one gate per leaderboard, keyed by name, shared across every run/agent in-process
_GATES: dict[str, SubmissionGate] = {}
_GATES_LOCK = threading.Lock()


def gate_for(
    leaderboard: str, max_per_window: int = 1, window_seconds: float = 1800.0
) -> SubmissionGate:
    """Return the process-wide gate for a leaderboard, creating it once."""
    with _GATES_LOCK:
        gate = _GATES.get(leaderboard)
        if gate is None:
            gate = SubmissionGate(max_per_window, window_seconds)
            _GATES[leaderboard] = gate
        return gate
