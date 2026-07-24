"""Seed-context builder: any agent is reconstructable from the store alone."""

from pathlib import Path

from chi.agents.protocol import SeedContext
from chi.config import ProblemConfig
from chi.orchestrator.steering import SteeringState
from chi.store import ledger
from chi.store.db import Store


def build_seed_context(
    store: Store,
    run_id: str,
    problem: ProblemConfig,
    workdir: Path,
    steering_state: SteeringState,
    iteration: int,
    baseline_score: float | None,
    mutation_note: str = "",
) -> SeedContext:
    """Assemble everything an agent needs purely from the store + workdir."""
    champ = ledger.champion(store, run_id, problem.score.direction)
    dead_ends = [dict(r) for r in ledger.list_negatives(store, run_id)]
    candidate_code = (Path(workdir) / problem.candidate).read_text()
    return SeedContext(
        problem=problem,
        workdir=Path(workdir),
        candidate_code=candidate_code,
        champion_score=None if champ is None else champ["score_value"],
        baseline_score=baseline_score,
        dead_ends=dead_ends,
        steering_text=steering_state.text,
        steering_hash=steering_state.operator_hash,
        iteration=iteration,
        mutation_note=mutation_note,
    )
