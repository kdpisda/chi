"""Adapt the deterministic orchestrator into the Director's round callable.

Round 0 creates the run + baseline via start_run; later rounds continue it via
run_slice. So a sustained Director run is ONE logical run with a growing store.
"""

from pathlib import Path
from typing import Callable

from chi.config import FleetConfig, load_problem
from chi.director.types import RoundResult
from chi.orchestrator.loop import run_slice, start_run
from chi.store import ledger
from chi.store.db import Store


class RoundRunner:
    def __init__(self, fleet: FleetConfig, run_dir: Path, *, first_started: bool = False,
                 completion_fn: Callable | None = None, stop_event=None) -> None:
        self._fleet = fleet
        self._run_dir = Path(run_dir)
        self._started = first_started
        self._completion_fn = completion_fn
        self._stop_event = stop_event
        self._round = 0
        self.run_id: str | None = None

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    def _direction(self) -> str:
        try:
            return load_problem(self._run_dir / "workdir").score.direction
        except (FileNotFoundError, ValueError):
            return "minimize"

    def __call__(self, iterations: int) -> RoundResult:
        before = 0
        if not self._started:
            sliced = self._fleet.model_copy(update={
                "policies": self._fleet.policies.model_copy(
                    update={"max_iterations": iterations})})
            summary = start_run(sliced, runs_root=self._run_dir.parent,
                                completion_fn=self._completion_fn,
                                stop_event=self._stop_event)
            self._run_dir = summary.run_dir
            self.run_id = summary.run_id
            self._started = True
        else:
            store = Store.open(self._run_dir)
            before = store.query(
                "SELECT COUNT(*) n FROM experiments WHERE run_id=?", (self.run_id,))[0]["n"]
            summary = run_slice(self._fleet, self._run_dir, iterations=iterations,
                                completion_fn=self._completion_fn,
                                stop_event=self._stop_event)
            self.run_id = summary.run_id
        store = Store.open(self._run_dir)
        after = store.query(
            "SELECT COUNT(*) n FROM experiments WHERE run_id=?", (self.run_id,))[0]["n"]
        champ = ledger.champion(store, self.run_id, self._direction())
        cost = store.query(
            "SELECT COALESCE(SUM(cost_usd),0) c FROM events WHERE run_id=?",
            (self.run_id,))[0]["c"]
        result = RoundResult(
            round_index=self._round, new_experiments=[],
            best_score=None if champ is None else champ["score_value"],
            benchmarks_run=max(0, after - before), cost_usd=float(cost))
        self._round += 1
        return result
