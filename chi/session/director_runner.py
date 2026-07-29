"""Background-thread wrapper for the Director (modeled on RunHandle).

Establishes the run + baseline with a zero-iteration start_run (so the champion
baseline is measured up front and every REAL slice gets meta-reviewed), then
runs the Director loop via run_slice until stopped.
"""

import threading
from pathlib import Path
from typing import Callable

from chi.config import FleetConfig, load_problem, resolve_coders
from chi.director.loop import Director
from chi.director.research import Researcher
from chi.director.round import RoundRunner
from chi.director.strategy import Strategist
from chi.orchestrator.loop import start_run
from chi.store.db import Store


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
            direction = load_problem(self.run_dir / "workdir").score.direction
            runner = RoundRunner(self._fleet, self.run_dir, first_started=True,
                                 stop_event=self.stop_event)
            runner.run_id = self.run_id
            strategist = Strategist(store, self.run_id, self.run_dir, direction,
                                    brain_fn=self._brain)
            researcher = Researcher(brain_fn=self._brain)
            coders = resolve_coders(self._fleet)
            per_coder = {c.id: (c.strategy or f"strategy-{c.id}") for c in coders}
            self._director = Director(store, self.run_id, self.run_dir, runner, strategist,
                                      researcher, direction=direction, emit=self._emit,
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
