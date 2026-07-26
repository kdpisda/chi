"""Tier-1 local evaluation: correctness hard gate, then median-of-k benchmark."""

import json
import shlex
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from chi.config import ProblemConfig
from chi.eval.hashing import code_hash
from chi.store import ledger
from chi.store.db import Store


@dataclass
class EvalResult:
    code_hash: str
    correct: bool
    seeds_passed: list[int] = field(default_factory=list)
    score_value: float | None = None
    noise_std: float | None = None
    detail: str = ""
    cached: bool = False


def _run(cmd: str, workdir: Path, timeout: int) -> subprocess.CompletedProcess:
    """Run one entrypoint command in the workdir, capturing output."""
    return subprocess.run(
        shlex.split(cmd), cwd=workdir, capture_output=True, text=True, timeout=timeout
    )


def evaluate(
    problem: ProblemConfig,
    workdir: Path,
    *,
    store: Store | None = None,
    run_id: str | None = None,
    agent_id: str = "local",
    task_id: str | None = None,
    parent_code_hash: str | None = None,
    strategy: str | None = None,
) -> EvalResult:
    """Evaluate the candidate in workdir; records to the store when attached."""
    workdir = Path(workdir)
    candidate = workdir / problem.candidate
    source = candidate.read_text()
    chash = code_hash(source)

    if store is not None:
        prior = ledger.get_experiment(store, chash)
        if prior is not None:
            return EvalResult(
                code_hash=chash, correct=bool(prior["correct"]),
                seeds_passed=json.loads(prior["seeds_passed_json"]),
                score_value=prior["score_value"], noise_std=prior["noise_std"],
                detail="cached", cached=True,
            )

    seeds_passed: list[int] = []
    detail = ""
    correct = True
    for seed in problem.correctness.seeds:
        # {python} resolves to the running interpreter so problems don't depend
        # on a bare `python` being on PATH (it isn't in uv tool environments).
        cmd = problem.entrypoints.correctness.format(
            candidate=problem.candidate, seed=seed, python=sys.executable
        )
        try:
            proc = _run(cmd, workdir, problem.timeout_seconds)
        except subprocess.TimeoutExpired:
            correct = False
            detail = f"correctness timeout on seed {seed}"
            break
        if proc.returncode != 0:
            correct = False
            detail = (
                f"correctness failed on seed {seed}:"
                f" {proc.stdout.strip()} {proc.stderr.strip()}"
            )[:500]
            break
        seeds_passed.append(seed)

    score_value: float | None = None
    noise_std: float | None = None
    if correct:
        samples: list[float] = []
        cmd = problem.entrypoints.benchmark.format(
            candidate=problem.candidate, python=sys.executable
        )
        for _ in range(problem.score.repeats):
            try:
                proc = _run(cmd, workdir, problem.timeout_seconds)
            except subprocess.TimeoutExpired:
                correct = False
                detail = "benchmark timeout"
                break
            if proc.returncode != 0:
                correct = False
                detail = f"benchmark failed: {proc.stderr.strip()}"[:500]
                break
            samples.append(float(json.loads(proc.stdout.strip().splitlines()[-1])["score"]))
        if samples and correct:
            score_value = statistics.median(samples)
            noise_std = statistics.pstdev(samples) if len(samples) > 1 else 0.0

    result = EvalResult(
        code_hash=chash, correct=correct, seeds_passed=seeds_passed,
        score_value=score_value, noise_std=noise_std, detail=detail,
    )
    if store is not None and run_id is not None:
        ledger.record_experiment(
            store, run_id, code_hash=chash, correct=result.correct,
            seeds_passed=result.seeds_passed, score_value=result.score_value,
            noise_std=result.noise_std, agent_id=agent_id, task_id=task_id,
            score_metric=problem.score.metric, parent_code_hash=parent_code_hash,
            strategy=strategy,
        )
    return result
