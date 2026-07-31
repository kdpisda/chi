"""Fleet and problem configuration models."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class CoderCfg(BaseModel):
    id: str = "coder-1"
    model: str
    adapter: Literal["litellm_loop", "cli_subprocess", "json_stream", "scripted"] = "litellm_loop"
    command: str | None = None  # cli_subprocess: template with {prompt_file}
    script: str | None = None  # scripted: path to JSON list of candidate sources
    strategy: str | None = None  # the distinct approach this agent explores
    sandbox: Literal["none", "docker", "docker-cli"] = "none"  # where the agent command runs
    sandbox_image: str | None = None  # docker image when sandbox=docker/docker-cli
    sandbox_network: str = "none"  # docker --network policy (docker-cli forces bridge)


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
    coders: list[CoderCfg] = Field(default_factory=list)
    policies: PoliciesCfg = Field(default_factory=PoliciesCfg)


def resolve_coders(fleet: FleetConfig) -> list[CoderCfg]:
    """Fleet coders, else the user's global default_coders; error when neither."""
    if fleet.coders:
        return fleet.coders
    from chi.userconfig import load_user_config

    defaults = load_user_config().default_coders
    if defaults:
        return defaults
    raise ValueError(
        "no coders configured — run /models (or `chi models`) or add coders to fleet.yaml"
    )


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
    # authoritative tier: an external leaderboard reached via popcorn-cli.
    # benchmark = parallel proxy scoring; submit = serialized, rationed, approved.
    leaderboard: str | None = None
    eval_backend: str = "popcorn"  # named backend from chi.eval.registry
    benchmark_cmd: str | None = None  # popcorn-cli benchmark; template with {candidate}
    submit_cmd: str | None = None  # popcorn-cli leaderboard submit
    ration_per_window: int = 1
    ration_window_seconds: float = 1800.0
    auto_submit: bool = False  # submit real improvements to the leaderboard unattended
    promote_margin_pct: float = 0.5  # must beat the last submission by this to auto-submit
    current_best: float | None = None  # seed with your live leaderboard best
    # problem-scoped strategy priors (round-robin across coders). These beat the
    # coder-config `strategy` field, which is problem-agnostic: a global CUDA
    # prior leaking onto an unrelated problem produced hallucinated dead ends.
    strategies: list[str] = Field(default_factory=list)


def resolve_strategy(problem: "ProblemConfig", coder: "CoderCfg", index: int) -> str:
    """The strategy a coder runs under: problem prior > coder config > default."""
    if problem.strategies:
        return problem.strategies[index % len(problem.strategies)]
    return coder.strategy or f"strategy-{coder.id}"


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
