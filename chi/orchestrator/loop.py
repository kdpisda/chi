"""Deterministic v1 run loop: one coder agent, steering, watchdog, budgets."""

import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chi.agents.cli_subprocess import CliSubprocessAdapter
from chi.agents.context import build_seed_context
from chi.agents.litellm_loop import LiteLLMLoopAdapter
from chi.agents.protocol import CoderAdapter
from chi.agents.scripted import ScriptedAdapter
from chi.config import (
    CoderCfg, FleetConfig, PoliciesCfg, ProblemConfig, load_problem, resolve_coders,
)
from chi.eval.hashing import code_hash
from chi.eval.runner import evaluate
from chi.orchestrator.steering import Steering
from chi.orchestrator.watchdog import Watchdog
from chi.providers.budgets import BudgetExceededError, BudgetTracker
from chi.store import events, ledger, tasks
from chi.store.db import Store, utcnow


@dataclass
class RunSummary:
    run_id: str
    run_dir: Path
    iterations: int
    baseline_score: float | None
    champion_score: float | None
    champion_hash: str | None
    total_cost_usd: float
    status: str


def _make_adapter(
    coder: CoderCfg,
    store: Store,
    run_id: str,
    workdir: Path,
    problem: ProblemConfig,
    budget: BudgetTracker,
    policies: PoliciesCfg,
    completion_fn: Callable | None,
) -> CoderAdapter:
    """Build the configured adapter for the coder entry (via the registry)."""
    from chi.agents.registry import build_adapter

    return build_adapter(coder, store=store, run_id=run_id, workdir=workdir,
                         problem=problem, budget=budget, policies=policies,
                         completion_fn=completion_fn)


def _build_auto_submitter(store, run_id, problem):
    """AutoSubmitter when the problem targets a leaderboard with auto_submit on."""
    if not (getattr(problem, "leaderboard", None) and getattr(problem, "auto_submit", False)):
        return None
    from chi.eval.autosubmit import AutoSubmitter
    from chi.eval.popcorn import PopcornBackend

    backend = PopcornBackend(problem.leaderboard, problem.benchmark_cmd or "",
                             problem.submit_cmd or "")

    def emit(line: str) -> None:
        events.append_event(store, run_id, events.STATUS, payload={"auto_submit": line})

    return AutoSubmitter(backend, direction=problem.score.direction,
                         margin_pct=problem.promote_margin_pct,
                         baseline=problem.current_best, emit=emit)


def _run_coder(coder, workdir, task_id, strategy, store, run_id, problem, budget,
               policies, steering, baseline_score, stop_event, completion_fn,
               coder_status, auto_submitter=None) -> None:
    """One coder agent's iteration loop, in its own worktree (runs in a thread)."""
    adapter = _make_adapter(coder, store, run_id, workdir, problem, budget, policies,
                            completion_fn)
    watchdog = Watchdog(policies)
    mutation_note = ""
    status = "done"
    completed = 0
    for iteration in range(policies.max_iterations):
        if stop_event is not None and stop_event.is_set():
            events.append_event(store, run_id, events.STOP, agent_id=coder.id,
                                task_id=task_id, payload={"reason": "operator"})
            tasks.release_task(store, run_id, task_id)
            status = "stopped"
            break
        state = steering.refresh()
        tasks.renew_lease(store, task_id, policies.lease_seconds)
        seed = build_seed_context(store, run_id, problem, workdir, state, iteration,
                                  baseline_score, mutation_note)
        seed.strategy = strategy
        mutation_note = ""
        events.append_event(store, run_id, events.ITERATION_START, agent_id=coder.id,
                            task_id=task_id,
                            payload={"iteration": iteration, "strategy": strategy,
                                     "steering_hash": state.operator_hash})
        try:
            outcome = adapter.run_iteration(seed)
        except BudgetExceededError as exc:
            events.append_event(store, run_id, events.STOP, agent_id=coder.id,
                                task_id=task_id, payload={"reason": str(exc)})
            status = "budget_exhausted"
            break
        except Exception as exc:  # one agent's crash must not sink the fleet
            events.append_event(store, run_id, events.STATUS, agent_id=coder.id,
                                task_id=task_id, payload={"error": str(exc)[:200]})
            status = "failed"
            break
        events.append_event(store, run_id, events.ITERATION_COMPLETE, agent_id=coder.id,
                            task_id=task_id,
                            payload={"iteration": iteration, "evals_run": outcome.evals_run,
                                     "note": outcome.note, "strategy": strategy,
                                     "context_pct": outcome.context_pct,
                                     "steering_hash": state.operator_hash},
                            cost_usd=outcome.cost_usd, tokens_in=outcome.tokens_in,
                            tokens_out=outcome.tokens_out)
        candidate_hash = code_hash((workdir / problem.candidate).read_text())
        # auto-submit the candidate just benchmarked, if it clears the rails.
        # (On a WINNING iteration the coder keeps the winning candidate.py, so
        # this file hash correctly resolves to the improving experiment.)
        if auto_submitter is not None and outcome.evals_run > 0:
            exp = ledger.get_experiment(store, candidate_hash)
            if exp is not None:
                decision = auto_submitter.consider(
                    workdir / problem.candidate, exp["score_value"], bool(exp["correct"]))
                events.append_event(
                    store, run_id, events.STATUS, agent_id=coder.id, task_id=task_id,
                    payload={"auto_submit": decision.reason, "submitted": decision.submitted})
        # The watchdog's loop-detection must track the candidate the coder
        # actually EVALUATED this iteration, not the on-disk file: coders revert
        # candidate.py to the champion after a losing benchmark, so the file hash
        # looks unchanged every iteration and would falsely reap an agent that is
        # exploring a new distinct candidate each round. The store is the truth.
        watchdog_hash = candidate_hash
        if outcome.evals_run > 0:
            latest = ledger.latest_experiment(store, run_id, coder.id)
            if latest is not None:
                watchdog_hash = latest["code_hash"]
        verdict = watchdog.observe_iteration(new_evals=outcome.evals_run,
                                             candidate_hash=watchdog_hash)
        if verdict.action == "mutate":
            mutation_note = f"WATCHDOG: {verdict.reason}"
        elif verdict.action == "kill":
            events.append_event(store, run_id, events.WATCHDOG_KILL, agent_id=coder.id,
                                task_id=task_id, payload={"reason": verdict.reason})
            tasks.release_task(store, run_id, task_id)
            status = "stalled"
            completed = iteration + 1
            break
        completed = iteration + 1
    if status == "done":
        tasks.set_status(store, task_id, "verified")
    coder_status[coder.id] = (status, completed)


def _export_champion(store, run_id, champ, coders, run_dir, base_workdir, problem) -> None:
    """Copy the winning candidate into the shared workdir so /champion --export works."""
    author = champ["author"]
    src_workdir = base_workdir
    if len(coders) > 1 and any(c.id == author for c in coders):
        src_workdir = run_dir / f"workdir-{author}"
    src = src_workdir / problem.candidate
    dst = base_workdir / problem.candidate
    if src.exists() and src.resolve() != dst.resolve():
        shutil.copy(src, dst)


def start_run(
    fleet: FleetConfig,
    runs_root: Path = Path("runs"),
    completion_fn: Callable | None = None,
    on_run_created: Callable[[str, Path], None] | None = None,
    stop_event: threading.Event | None = None,
    sessions_path: Path | None = None,
) -> RunSummary:
    """Execute one full v1 run; returns the summary."""
    run_id = f"{fleet.run_name}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(runs_root) / run_id
    store = Store.open(run_dir)
    store.execute(
        "INSERT INTO runs (run_id, problem, fleet_config_json, started_at)"
        " VALUES (?,?,?,?)",
        (run_id, str(fleet.problem), fleet.model_dump_json(), utcnow()),
    )
    if on_run_created is not None:
        on_run_created(run_id, run_dir)
    from chi.userconfig import record_session

    record_session({
        "run_id": run_id, "run_dir": str(run_dir.resolve()), "status": "running",
        "run_name": fleet.run_name, "problem": str(fleet.problem),
        "cwd": str(Path.cwd()), "started_at": utcnow(),
    }, sessions_path=sessions_path)
    base_workdir = run_dir / "workdir"
    shutil.copytree(fleet.problem, base_workdir)
    problem = load_problem(base_workdir)
    policies = fleet.policies
    budget = BudgetTracker(fleet.budgets.total_usd, fleet.budgets.per_role_usd,
                           store=store, run_id=run_id)
    coders = resolve_coders(fleet)
    steering = Steering(store, run_id, problem.score.direction)

    # auto-submit: when the problem targets a leaderboard and it's enabled, the
    # fleet submits real improvements itself (correctness + margin + gate rails)
    auto_submitter = _build_auto_submitter(store, run_id, problem)

    # baseline established once, in the shared workdir the champion is exported from
    baseline = evaluate(problem, base_workdir, store=store, run_id=run_id,
                        agent_id="baseline")
    baseline_score = baseline.score_value

    # one worktree + task + watchdog per coder; all share the blackboard store so
    # dedup, the negative ledger, and champion selection work across the fleet
    coder_status: dict[str, str] = {}
    threads: list[threading.Thread] = []
    for coder in coders:
        coder_workdir = run_dir / f"workdir-{coder.id}" if len(coders) > 1 else base_workdir
        if coder_workdir != base_workdir:
            shutil.copytree(fleet.problem, coder_workdir)
        store.execute(
            "INSERT INTO agents (agent_id, run_id, adapter, model, workdir, started_at)"
            " VALUES (?,?,?,?,?,?)",
            (coder.id, run_id, coder.adapter, coder.model, str(coder_workdir), utcnow()),
        )
        strategy = coder.strategy or f"strategy-{coder.id}"
        task_id = tasks.create_task(store, run_id, spec={"goal": "improve score",
                                                         "strategy": strategy})
        tasks.claim_task(store, run_id, coder.id, policies.lease_seconds)
        args = (coder, coder_workdir, task_id, strategy, store, run_id, problem,
                budget, policies, steering, baseline_score, stop_event, completion_fn,
                coder_status, auto_submitter)
        threads.append(threading.Thread(target=_run_coder, args=args, daemon=True))

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    iterations_done = max((c[1] for c in coder_status.values()), default=0)
    statuses = {c[0] for c in coder_status.values()}
    if "budget_exhausted" in statuses:
        status = "budget_exhausted"
    elif stop_event is not None and stop_event.is_set():
        status = "stopped"
    elif statuses <= {"stalled", "failed"} and statuses:
        status = "stalled" if "stalled" in statuses else "failed"
    else:
        status = "done"

    champ = ledger.champion(store, run_id, problem.score.direction)
    if champ is not None and champ["author"] not in (None, "baseline"):
        _export_champion(store, run_id, champ, coders, run_dir, base_workdir, problem)
    events.append_event(store, run_id, events.STOP, payload={"status": status})
    store.execute("UPDATE runs SET ended_at=?, status=? WHERE run_id=?",
                  (utcnow(), status, run_id))
    record_session({
        "run_id": run_id, "run_dir": str(run_dir.resolve()), "status": status,
        "ended_at": utcnow(), "baseline_score": baseline_score,
        "champion_score": None if champ is None else champ["score_value"],
    }, sessions_path=sessions_path)
    return RunSummary(
        run_id=run_id, run_dir=run_dir, iterations=iterations_done,
        baseline_score=baseline_score,
        champion_score=None if champ is None else champ["score_value"],
        champion_hash=None if champ is None else champ["code_hash"],
        total_cost_usd=budget.spent, status=status,
    )
