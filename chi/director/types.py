"""Shared vocabulary for the Director loop and its collaborators."""

from dataclasses import dataclass, field
from enum import Enum


class DirectorState(str, Enum):
    IMPROVING = "improving"
    PLATEAUED = "plateaued"
    STUCK = "stuck"


@dataclass
class RoundResult:
    round_index: int
    new_experiments: list[dict]
    best_score: float | None
    benchmarks_run: int
    cost_usd: float


@dataclass
class RoundDigest:
    round_index: int
    best_score: float | None
    champion_score: float | None
    prev_best: float | None
    dead_classes: list[str] = field(default_factory=list)
    repeated_dead_classes: list[str] = field(default_factory=list)
    near_misses: list[dict] = field(default_factory=list)
    distinct_new_classes: int = 0


@dataclass
class StrategyUpdate:
    steering_text: str
    per_coder_strategy: dict[str, str] = field(default_factory=dict)
    new_dead_classes: list[str] = field(default_factory=list)
    promoted_near_misses: list[str] = field(default_factory=list)
    researched: bool = False
