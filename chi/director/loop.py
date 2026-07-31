"""The autonomous research director: a persistent above-the-fleet control loop.

One round: run a bounded fleet slice -> meta-review from the store -> classify
state -> research if stuck -> steer + mutate strategies -> repeat until stopped.
Runs until the user stops it (spec: no auto-termination); every round emits a
DIRECTOR_ROUND event carrying the running benchmark/$ spend — the visible
guardrail. NEVER fires a ranked leaderboard submit (spec J1: manual).
"""

import threading
import time
from pathlib import Path
from typing import Callable

from chi.director.review import build_digest, classify_state
from chi.director.types import DirectorState, RoundResult
from chi.store import events
from chi.store.db import Store


class Director:
    def __init__(self, store: Store, run_id: str, run_dir: Path,
                 round_runner: Callable[[int], RoundResult], strategist,
                 researcher=None, *, direction: str = "minimize",
                 emit: Callable[[str], None] | None = None, noise_guard=None,
                 stuck_k: int = 2, iterations_per_round: int = 2,
                 per_coder_strategy: dict | None = None,
                 idle_sleep_seconds: float = 5.0,
                 dead_eval_rounds: int = 3) -> None:
        self._store = store
        self._run_id = run_id
        self._run_dir = Path(run_dir)
        self._runner = round_runner
        self._strategist = strategist
        self._researcher = researcher
        self._direction = direction
        self._emit = emit or (lambda line: None)
        self._noise_guard = noise_guard
        self._stuck_k = stuck_k
        self._iters = iterations_per_round
        self._strategies = per_coder_strategy or {}
        # pause between rounds that produced no new information — prevents a
        # pathological fast-spin (and brain-call storm) when a slice returns
        # instantly, e.g. every coder erroring out immediately
        self._idle_sleep = idle_sleep_seconds
        # halt after this many consecutive rounds with NO scored champion: the
        # eval itself is broken (e.g. the leaderboard closed and every benchmark
        # errors) — run-until-stopped must not mean burn-forever-on-a-dead-eval
        self._dead_eval_rounds = dead_eval_rounds
        self.halted_reason: str | None = None
        self.cumulative_benchmarks = 0
        self.cumulative_cost = 0.0

    def run(self, stop_event: threading.Event) -> None:
        prev_best: float | None = None
        history: list = []
        round_index = 0
        last_key: tuple | None = None
        scoreless_rounds = 0
        while not stop_event.is_set():
            result = self._runner(self._iters)
            self.cumulative_benchmarks += result.benchmarks_run
            self.cumulative_cost += result.cost_usd
            digest = build_digest(self._store, self._run_id, round_index, prev_best,
                                  self._direction)
            state = classify_state(digest, history=history, stuck_k=self._stuck_k,
                                   direction=self._direction)
            # only spend the (expensive) research/strategy brain when the round
            # actually produced new information — new candidates were benchmarked,
            # the score moved, or dead classes grew. A round that evaluated nothing
            # AND left the digest unchanged just records + pauses (no brain-storm).
            key = (digest.best_score, tuple(digest.dead_classes))
            changed = result.benchmarks_run > 0 or key != last_key
            last_key = key
            findings = ""
            if changed:
                if state == DirectorState.STUCK and self._researcher is not None:
                    findings = self._researcher.research(digest.champion_score or 0.0,
                                                         digest.dead_classes)
                update = self._strategist.plan(digest, state, self._strategies, findings)
                self._strategist.apply(update)
                self._strategies = update.per_coder_strategy
            events.append_event(
                self._store, self._run_id, events.DIRECTOR_ROUND,
                payload={"round": round_index, "state": state.value,
                         "best": digest.best_score, "benchmarks_run": result.benchmarks_run,
                         "cost_usd": result.cost_usd,
                         "cum_benchmarks": self.cumulative_benchmarks,
                         "cum_cost": self.cumulative_cost, "researched": bool(findings)},
                cost_usd=result.cost_usd)
            self._emit(f"round {round_index}: {state.value} · best {digest.best_score}"
                       f" · Σ {self.cumulative_benchmarks} benches"
                       f" ${self.cumulative_cost:.2f}")
            history.append(digest)
            # advance the improvement baseline only on a confirmed improving round
            if state == DirectorState.IMPROVING and digest.best_score is not None:
                prev_best = digest.best_score
            elif prev_best is None:
                prev_best = digest.best_score
            round_index += 1
            # dead-eval detector: rounds keep completing but NOTHING ever scores.
            # That is an eval/infra failure (leaderboard closed, benchmark backend
            # down), not research — halt loudly instead of burning forever.
            scoreless_rounds = scoreless_rounds + 1 if digest.best_score is None else 0
            if scoreless_rounds >= self._dead_eval_rounds:
                self.halted_reason = (
                    f"eval appears broken: no scored candidate in {scoreless_rounds}"
                    " consecutive rounds — check the benchmark backend"
                    " (is the leaderboard still open?)")
                events.append_event(self._store, self._run_id, events.STOP,
                                    payload={"reason": self.halted_reason})
                self._emit(f"⚠ director halted: {self.halted_reason}")
                return
            # a round with no new benchmarks means the slice returned nothing to
            # chew on — back off so we don't busy-spin (interruptible via the event)
            if result.benchmarks_run == 0 and self._idle_sleep > 0:
                stop_event.wait(self._idle_sleep)
