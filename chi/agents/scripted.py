"""Deterministic test adapter: plays back a scripted sequence of candidates."""

import json
from pathlib import Path

from chi.agents.protocol import CoderAdapter, IterationOutcome, SeedContext
from chi.eval.runner import evaluate


class ScriptedAdapter(CoderAdapter):
    """Writes the i-th scripted candidate source, then evaluates it."""

    def run_iteration(self, seed: SeedContext) -> IterationOutcome:
        """Apply the scripted candidate for this iteration and evaluate."""
        self.ack_steering(seed.steering_hash)
        self.heartbeat()
        if self.script is not None:
            sources: list[str] = json.loads(Path(self.script).read_text())
            source = sources[min(seed.iteration, len(sources) - 1)]
            (self.workdir / self.problem.candidate).write_text(source)
        evaluate(
            self.problem, self.workdir, store=self.store, run_id=self.run_id,
            agent_id=self.agent_id, strategy=seed.strategy,
        )
        return IterationOutcome(evals_run=1)
