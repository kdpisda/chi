"""Fleet and problem configuration models."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class CoderCfg(BaseModel):
    id: str = "coder-1"
    model: str
    adapter: Literal["litellm_loop", "cli_subprocess", "scripted"] = "litellm_loop"
    command: str | None = None  # cli_subprocess: template with {prompt_file}
    script: str | None = None  # scripted: path to JSON list of candidate sources


class BudgetsCfg(BaseModel):
    total_usd: float = 5.0
    per_role_usd: dict[str, float] = Field(default_factory=dict)


class PoliciesCfg(BaseModel):
    heartbeat_seconds: int = 30
    lease_seconds: int = 900
    repeat_k: int = 3
    eval_recency_iters: int = 10
    max_iterations: int = 20
    iteration_timeout_seconds: int = 600


class FleetConfig(BaseModel):
    run_name: str
    problem: Path
    budgets: BudgetsCfg = Field(default_factory=BudgetsCfg)
    coders: list[CoderCfg]
    policies: PoliciesCfg = Field(default_factory=PoliciesCfg)


class EntrypointsCfg(BaseModel):
    correctness: str
    benchmark: str
    build: str | None = None


class ScoreCfg(BaseModel):
    metric: str = "runtime_ms"
    direction: Literal["minimize", "maximize"] = "minimize"
    repeats: int = 5


class CorrectnessCfg(BaseModel):
    seeds: list[int]
    tolerance: float = 1e-6


class ProblemConfig(BaseModel):
    name: str
    description: str = ""
    candidate: str = "candidate.py"
    entrypoints: EntrypointsCfg
    score: ScoreCfg = Field(default_factory=ScoreCfg)
    correctness: CorrectnessCfg
    timeout_seconds: int = 60
    dir: Path | None = None


def load_fleet(path: Path) -> FleetConfig:
    """Load and validate a fleet.yaml."""
    data = yaml.safe_load(Path(path).read_text())
    return FleetConfig.model_validate(data)


def load_problem(problem_dir: Path) -> ProblemConfig:
    """Load and validate <problem_dir>/problem.yaml; records the directory."""
    manifest = Path(problem_dir) / "problem.yaml"
    if not manifest.exists():
        raise FileNotFoundError(f"no problem.yaml in {problem_dir}")
    data = yaml.safe_load(manifest.read_text())
    prob = ProblemConfig.model_validate(data)
    prob.dir = Path(problem_dir)
    return prob
