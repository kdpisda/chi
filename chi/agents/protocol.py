"""Adapter contract: the harness does the bookkeeping, not the model."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from chi.config import PoliciesCfg, ProblemConfig
from chi.providers.budgets import BudgetTracker
from chi.store import events
from chi.store.db import Store, utcnow


@dataclass
class SeedContext:
    problem: ProblemConfig
    workdir: Path
    candidate_code: str
    champion_score: float | None
    baseline_score: float | None
    dead_ends: list[dict]
    steering_text: str
    steering_hash: str
    iteration: int
    mutation_note: str = ""
    strategy: str | None = None


@dataclass
class IterationOutcome:
    evals_run: int
    note: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    context_pct: float | None = None  # last prompt size vs model context limit


class CoderAdapter(ABC):
    """One coder agent backed by some substrate (CLI, API loop, script)."""

    def __init__(
        self,
        *,
        store: Store,
        run_id: str,
        agent_id: str,
        model: str,
        workdir: Path,
        problem: ProblemConfig,
        budget: BudgetTracker,
        policies: PoliciesCfg,
        command: str | None = None,
        script: str | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.agent_id = agent_id
        self.model = model
        self.workdir = Path(workdir)
        self.problem = problem
        self.budget = budget
        self.policies = policies
        self.command = command
        self.script = script
        self._acked: set[str] = set()

    @abstractmethod
    def run_iteration(self, seed: SeedContext) -> IterationOutcome:
        """Run one improvement iteration (blocking)."""

    def heartbeat(self) -> None:
        """Record liveness in the agents table and the event log."""
        self.store.execute(
            "UPDATE agents SET last_heartbeat_at=? WHERE agent_id=? AND run_id=?",
            (utcnow(), self.agent_id, self.run_id),
        )
        events.append_event(self.store, self.run_id, events.HEARTBEAT, agent_id=self.agent_id)

    def ack_steering(self, steering_hash: str) -> None:
        """Emit STEER_ACK the first time this agent runs under a steering version."""
        if steering_hash in self._acked:
            return
        self._acked.add(steering_hash)
        events.append_event(
            self.store, self.run_id, events.STEER_ACK, agent_id=self.agent_id,
            payload={"steering_hash": steering_hash},
        )
