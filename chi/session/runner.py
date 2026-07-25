"""Background run execution for interactive sessions."""

import threading
from pathlib import Path
from typing import Callable

from chi.config import FleetConfig
from chi.orchestrator.loop import RunSummary, start_run


class RunHandle:
    """One run executing in a daemon thread, stoppable and observable."""

    def __init__(
        self,
        fleet: FleetConfig,
        runs_root: Path,
        completion_fn: Callable | None = None,
    ) -> None:
        self._fleet = fleet
        self._runs_root = runs_root
        self._completion_fn = completion_fn
        self.ready = threading.Event()
        self.run_id: str | None = None
        self.run_dir: Path | None = None
        self.summary: RunSummary | None = None
        self.error: str | None = None
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _on_created(self, run_id: str, run_dir: Path) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.ready.set()

    def _target(self) -> None:
        try:
            self.summary = start_run(
                self._fleet,
                runs_root=self._runs_root,
                completion_fn=self._completion_fn,
                on_run_created=self._on_created,
                stop_event=self.stop_event,
            )
        except Exception as exc:  # surfaced to the session transcript, never raised
            self.error = str(exc)
            self.ready.set()

    def start(self) -> None:
        """Launch the run thread."""
        self._thread = threading.Thread(target=self._target, daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        """True while the run thread is still executing."""
        return self._thread is not None and self._thread.is_alive()

    def request_stop(self) -> None:
        """Ask the orchestrator to stop at the next iteration boundary."""
        self.stop_event.set()
