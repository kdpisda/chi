"""Background-thread wrapper for the Director (modeled on RunHandle).

Establishes the run + baseline with a zero-iteration start_run (so the champion
baseline is measured up front and every REAL slice gets meta-reviewed), then
runs the Director loop via run_slice until stopped.
"""

import json
import math
import shlex
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from chi.config import (FleetConfig, ProblemConfig, load_problem, resolve_coders,
                        resolve_strategy)
from chi.director.loop import Director
from chi.director.research import Researcher
from chi.director.round import RoundRunner
from chi.director.strategy import Strategist
from chi.eval.noise import NoiseGuard
from chi.eval.popcorn import BenchResult, PopcornBackend
from chi.orchestrator.loop import start_run
from chi.store.db import Store


def local_benchmark_fn(problem: ProblemConfig) -> Callable[[Path], BenchResult]:
    """One local benchmark sample for the NoiseGuard: run the problem's benchmark
    entrypoint once in the candidate's directory (the exported-champion workdir)
    and parse the trailing {"score": <float>} line, exactly like chi.eval.runner.
    """

    def bench(candidate: Path) -> BenchResult:
        candidate = Path(candidate)
        cmd = problem.entrypoints.benchmark.format(
            candidate=candidate.name, python=sys.executable)
        try:
            proc = subprocess.run(shlex.split(cmd), cwd=candidate.parent,
                                  capture_output=True, text=True,
                                  timeout=problem.timeout_seconds)
        except subprocess.TimeoutExpired:
            return BenchResult(False, None, "benchmark timed out")
        if proc.returncode != 0:
            out = ((proc.stdout or "") + " " + (proc.stderr or "")).strip()
            return BenchResult(False, None, f"benchmark failed: {out[:300]}")
        try:
            score = float(json.loads(proc.stdout.strip().splitlines()[-1])["score"])
        except (IndexError, KeyError, TypeError, ValueError):
            return BenchResult(False, None,
                               f"no score parsed from: {proc.stdout.strip()[:300]}")
        # same rule as the eval runner: a frozen/negative/inf "runtime" is gaming
        # or breakage, not speed — reject the measurement
        if not math.isfinite(score) or score <= 0:
            return BenchResult(False, None,
                               f"invalid score {score!r}: must be finite and > 0")
        return BenchResult(True, score, "ok")

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


class DirectorHandle:
    def __init__(self, fleet: FleetConfig, runs_root: Path,
                 brain_fn: Callable[[str], str] | None = None,
                 emit: Callable[[str], None] | None = None) -> None:
        self._fleet = fleet
        self._runs_root = Path(runs_root)
        self._brain = brain_fn
        self._emit = emit or (lambda line: None)
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
                                      candidate_name=problem.candidate,
                                      per_coder_strategy=per_coder)
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
