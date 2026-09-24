"""Background-thread wrapper for the Director (modeled on RunHandle).

Establishes the run + baseline with a zero-iteration start_run (so the champion
baseline is measured up front and every REAL slice gets meta-reviewed), then
runs the Director loop via run_slice until stopped.
"""

import threading
from pathlib import Path
from typing import Callable

from chi.config import (FleetConfig, ProblemConfig, load_problem, resolve_coders,
                        resolve_strategy)
from chi.director.loop import Director
from chi.director.research import Researcher
from chi.director.round import RoundRunner
from chi.director.strategy import Strategist
from chi.eval.holdout import build_holdout_gate
from chi.eval.noise import NoiseGuard
from chi.eval.popcorn import BenchResult, PopcornBackend
from chi.eval.sample import sample_score
from chi.orchestrator.loop import baseline_holdout, start_run
from chi.store import events
from chi.store.db import Store


def local_benchmark_fn(problem: ProblemConfig) -> Callable[[Path], BenchResult]:
    """One local benchmark sample for the NoiseGuard: run the problem's benchmark
    entrypoint once in the candidate's directory (the exported-champion workdir)
    and parse the trailing {"score": <float>} line, exactly like chi.eval.runner.
    """

    def bench(candidate: Path) -> BenchResult:
        candidate = Path(candidate)
        return sample_score(problem.entrypoints.benchmark, candidate.parent,
                            candidate.name, problem.timeout_seconds)

    return bench


def build_noise_guard(problem: ProblemConfig, direction: str) -> NoiseGuard | None:
    """The director's median-of-N guard for this problem, or None if unguardable.

    Leaderboard problems re-benchmark through popcorn (~8% B200 noise). Local
    problems get a guard over their own benchmark entrypoint: even with
    score.repeats medianing inside one eval, the stored score of a noisy bench
    can still be one lucky draw, so apparent wins are re-sampled before belief.
    """
    if problem.leaderboard:
        if not problem.benchmark_cmd:
            return None
        backend = PopcornBackend(problem.leaderboard, problem.benchmark_cmd or "",
                                 problem.submit_cmd or "")
        return NoiseGuard(backend.benchmark, n=3, direction=direction,
                          promote_margin_pct=problem.promote_margin_pct)
    if problem.score.repeats >= 1:
        return NoiseGuard(local_benchmark_fn(problem), n=3, direction=direction,
                          promote_margin_pct=problem.promote_margin_pct)
    return None


def build_holdout_check(store, run_id: str, run_dir: Path,
                        problem: ProblemConfig) -> Callable | None:
    """The director's generalization check, or None when the problem has no holdout.

    Closes over everything the verdict needs from the store — the run's benchmark
    baseline and the held-out baseline measured on the untouched candidate — so
    the Director itself stays a pure control loop.
    """
    gate = build_holdout_gate(problem, Path(run_dir) / "holdout")
    if gate is None:
        return None

    def check(candidate: Path, champion_score: float | None):
        rows = store.query(
            "SELECT score_value FROM experiments WHERE run_id=? AND author='baseline'"
            " ORDER BY ts LIMIT 1", (run_id,))
        baseline_score = rows[0]["score_value"] if rows else None
        verdict = gate.verify(candidate, baseline_holdout=baseline_holdout(store, run_id),
                              baseline_score=baseline_score,
                              champion_score=champion_score)
        events.append_event(store, run_id, events.HOLDOUT, agent_id="holdout",
                            payload={"phase": "director", **verdict.as_payload()})
        return verdict

    return check


class DirectorHandle:
    def __init__(self, fleet: FleetConfig, runs_root: Path,
                 brain_fn: Callable[[str], str] | None = None,
                 emit: Callable[[str], None] | None = None,
                 target_score: float | None = None,
                 cost_ceiling_usd: float | None = None) -> None:
        self._fleet = fleet
        self._runs_root = Path(runs_root)
        self._brain = brain_fn
        self._emit = emit or (lambda line: None)
        self._target_score = target_score
        self._cost_ceiling_usd = cost_ceiling_usd
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.run_id: str | None = None
        self.run_dir: Path | None = None
        self.error: str | None = None
        self._director: Director | None = None
        self._thread: threading.Thread | None = None

    def _target(self) -> None:
        try:
            # establish the run + baseline with zero coder iterations, so the
            # champion baseline is measured before the loop and later slices continue it
            seed_fleet = self._fleet.model_copy(update={
                "policies": self._fleet.policies.model_copy(update={"max_iterations": 0})})
            summary = start_run(seed_fleet, runs_root=self._runs_root,
                                stop_event=self.stop_event)
            self.run_dir = summary.run_dir
            self.run_id = summary.run_id
            store = Store.open(self.run_dir)
            problem = load_problem(self.run_dir / "workdir")
            direction = problem.score.direction
            runner = RoundRunner(self._fleet, self.run_dir, first_started=True,
                                 stop_event=self.stop_event)
            runner.run_id = self.run_id
            strategist = Strategist(store, self.run_id, self.run_dir, direction,
                                    brain_fn=self._brain, problem_name=problem.name,
                                    problem_description=problem.description,
                                    metric=problem.score.metric)
            researcher = Researcher(brain_fn=self._brain, problem_name=problem.name,
                                    problem_description=problem.description,
                                    metric=problem.score.metric, direction=direction)
            coders = resolve_coders(self._fleet)
            per_coder = {c.id: resolve_strategy(problem, c, i)
                         for i, c in enumerate(coders)}
            # median-of-N noise guard: popcorn-backed on a leaderboard, the
            # problem's own benchmark entrypoint locally — so apparent wins on
            # ANY noisy eval are verified before the director believes them
            noise_guard = build_noise_guard(problem, direction)
            self._director = Director(store, self.run_id, self.run_dir, runner, strategist,
                                      researcher, direction=direction, emit=self._emit,
                                      noise_guard=noise_guard,
                                      holdout_check=build_holdout_check(
                                          store, self.run_id, self.run_dir, problem),
                                      candidate_name=problem.candidate,
                                      per_coder_strategy=per_coder,
                                      target_score=self._target_score,
                                      cost_ceiling_usd=self._cost_ceiling_usd)
            self.ready.set()
            self._director.run(self.stop_event)
        except Exception as exc:  # surfaced to the transcript, never raised
            self.error = str(exc)
            self.ready.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._target, daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def cumulative_benchmarks(self) -> int:
        return self._director.cumulative_benchmarks if self._director else 0

    @property
    def cumulative_cost(self) -> float:
        return self._director.cumulative_cost if self._director else 0.0
