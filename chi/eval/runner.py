"""Tier-1 local evaluation: correctness hard gate, then median-of-k benchmark."""

import json
import math
import os
import shlex
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from chi.agents.sandbox import Sandbox, make_sandbox
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


def _dispatch(cmd: str, workdir: Path, timeout: int,
              sandbox: Sandbox | None) -> subprocess.CompletedProcess:
    """Route one entrypoint run: direct host subprocess, or through the sandbox."""
    if sandbox is None:
        return _run(cmd, workdir, timeout)
    return sandbox.run(shlex.split(cmd), workdir, dict(os.environ), timeout)


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
    sandbox: Sandbox | None = None,
) -> EvalResult:
    """Evaluate the candidate in workdir; records to the store when attached.

    An untrusted candidate can attack the eval itself (a dogfood candidate froze
    the benchmark's perf_counter and hijacked `list` in the caller frame). When
    `problem.eval_sandbox` opts in, correctness AND benchmark subprocesses run
    inside a sandbox; `sandbox` is the injection seam for tests.
    """
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

    if sandbox is None and problem.eval_sandbox != "none":
        sandbox = make_sandbox(problem.eval_sandbox, problem.eval_sandbox_image)
    # {python} resolves to the running interpreter so problems don't depend
    # on a bare `python` being on PATH (it isn't in uv tool environments).
    # Inside a sandbox, sys.executable is a host path that doesn't exist in
    # the container — resolve to python3 on the container's PATH instead.
    python = "python3" if sandbox is not None else sys.executable

    seeds_passed: list[int] = []
    detail = ""
    correct = True
    for seed in problem.correctness.seeds:
        cmd = problem.entrypoints.correctness.format(
            candidate=problem.candidate, seed=seed, python=python
        )
        try:
            proc = _dispatch(cmd, workdir, problem.timeout_seconds, sandbox)
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
        # a benchmark failure is an EVAL failure, not candidate incorrectness —
        # the candidate already passed the correctness gate. Marking it incorrect
        # poisoned the ledger live (the champion kernel recorded correct=0 when
        # the leaderboard closed and every popcorn benchmark started erroring).
        bench_failed = False
        cmd = problem.entrypoints.benchmark.format(
            candidate=problem.candidate, python=python
        )
        for _ in range(problem.score.repeats):
            try:
                proc = _dispatch(cmd, workdir, problem.timeout_seconds, sandbox)
            except subprocess.TimeoutExpired:
                bench_failed = True
                detail = "benchmark timeout"
                break
            if proc.returncode != 0:
                bench_failed = True
                detail = f"benchmark failed: {proc.stderr.strip()}"[:500]
                break
            sample = float(json.loads(proc.stdout.strip().splitlines()[-1])["score"])
            # a runtime must be finite and strictly positive: a candidate that
            # freezes the benchmark's clock (0.0) or reports a negative/inf time is
            # gaming or broken, not fast. Reject the measurement — don't crown it.
            if not math.isfinite(sample) or sample <= 0:
                bench_failed = True
                detail = f"invalid score {sample!r}: runtime must be finite and > 0"
                break
            samples.append(sample)
        if samples and not bench_failed:
            score_value = statistics.median(samples)
            noise_std = statistics.pstdev(samples) if len(samples) > 1 else 0.0

    result = EvalResult(
        code_hash=chash, correct=correct, seeds_passed=seeds_passed,
        score_value=score_value, noise_std=noise_std, detail=detail,
    )
    if store is not None and run_id is not None:
        # Archive every scored+correct candidate's EXACT bytes, keyed by code hash,
        # so the champion (now or after a later regression) is always recoverable —
        # the on-disk workdir file gets overwritten/reverted by coders and is not a
        # reliable record of what actually won.
        artifacts_path: str | None = None
        if result.correct and result.score_value is not None:
            champions_dir = store.run_dir / "champions"
            champions_dir.mkdir(parents=True, exist_ok=True)
            archive = champions_dir / f"{chash}.py"
            if not archive.exists():  # dedup by content hash; tiny files
                archive.write_bytes(candidate.read_bytes())
            artifacts_path = str(archive)
        ledger.record_experiment(
            store, run_id, code_hash=chash, correct=result.correct,
            seeds_passed=result.seeds_passed, score_value=result.score_value,
            noise_std=result.noise_std, agent_id=agent_id, task_id=task_id,
            score_metric=problem.score.metric, parent_code_hash=parent_code_hash,
            strategy=strategy, artifacts_path=artifacts_path,
        )
    return result
