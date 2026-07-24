"""Deterministic v1 run loop: one coder agent, steering, watchdog, budgets."""

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chi.agents.cli_subprocess import CliSubprocessAdapter
from chi.agents.context import build_seed_context
from chi.agents.litellm_loop import LiteLLMLoopAdapter
from chi.agents.protocol import CoderAdapter
from chi.agents.scripted import ScriptedAdapter
from chi.config import CoderCfg, FleetConfig, PoliciesCfg, ProblemConfig, load_problem
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
    """Build the configured adapter for the coder entry."""
    kwargs = dict(store=store, run_id=run_id, agent_id=coder.id, model=coder.model,
                  workdir=workdir, problem=problem, budget=budget, policies=policies,
                  command=coder.command, script=coder.script)
    if coder.adapter == "scripted":
        return ScriptedAdapter(**kwargs)
    if coder.adapter == "litellm_loop":
        return LiteLLMLoopAdapter(**kwargs, completion_fn=completion_fn)
    if coder.adapter == "cli_subprocess":
        if not coder.command:
            raise ValueError("cli_subprocess adapter requires 'command' in the coder config")
        return CliSubprocessAdapter(**kwargs)
    raise ValueError(f"unknown adapter {coder.adapter}")


def start_run(
    fleet: FleetConfig,
    runs_root: Path = Path("runs"),
    completion_fn: Callable | None = None,
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
    workdir = run_dir / "workdir"
    shutil.copytree(fleet.problem, workdir)
    problem = load_problem(workdir)
    policies = fleet.policies
    budget = BudgetTracker(fleet.budgets.total_usd, fleet.budgets.per_role_usd,
                           store=store, run_id=run_id)
    coder = fleet.coders[0]
    store.execute(
        "INSERT INTO agents (agent_id, run_id, adapter, model, workdir, started_at)"
        " VALUES (?,?,?,?,?,?)",
        (coder.id, run_id, coder.adapter, coder.model, str(workdir), utcnow()),
    )
    adapter = _make_adapter(coder, store, run_id, workdir, problem, budget, policies,
                            completion_fn)
    steering = Steering(store, run_id, problem.score.direction)
    watchdog = Watchdog(policies)

    baseline = evaluate(problem, workdir, store=store, run_id=run_id, agent_id="baseline")
    baseline_score = baseline.score_value

    task_id = tasks.create_task(store, run_id, spec={"goal": "improve score"})
    tasks.claim_task(store, run_id, coder.id, policies.lease_seconds)

    status = "done"
    mutation_note = ""
    iterations_done = 0
    for iteration in range(policies.max_iterations):
        state = steering.refresh()
        tasks.expire_stale_leases(store, run_id)
        tasks.renew_lease(store, task_id, policies.lease_seconds)
        seed = build_seed_context(store, run_id, problem, workdir, state, iteration,
                                  baseline_score, mutation_note)
        mutation_note = ""
        events.append_event(store, run_id, events.ITERATION_START, agent_id=coder.id,
                            task_id=task_id,
                            payload={"iteration": iteration,
                                     "steering_hash": state.operator_hash})
        try:
            outcome = adapter.run_iteration(seed)
        except BudgetExceededError as exc:
            events.append_event(store, run_id, events.STOP, agent_id=coder.id,
                                payload={"reason": str(exc)})
            status = "budget_exhausted"
            iterations_done = iteration + 1
            break
        iterations_done = iteration + 1
        events.append_event(store, run_id, events.ITERATION_COMPLETE, agent_id=coder.id,
                            task_id=task_id,
                            payload={"iteration": iteration, "evals_run": outcome.evals_run,
                                     "note": outcome.note,
                                     "steering_hash": state.operator_hash})
        candidate_hash = code_hash((workdir / problem.candidate).read_text())
        verdict = watchdog.observe_iteration(new_evals=outcome.evals_run,
                                             candidate_hash=candidate_hash)
        if verdict.action == "mutate":
            mutation_note = f"WATCHDOG: {verdict.reason}"
        elif verdict.action == "kill":
            events.append_event(store, run_id, events.WATCHDOG_KILL, agent_id=coder.id,
                                task_id=task_id, payload={"reason": verdict.reason})
            tasks.release_task(store, run_id, task_id)
            status = "stalled"
            break

    champ = ledger.champion(store, run_id, problem.score.direction)
    if status == "done":
        tasks.set_status(store, task_id, "verified")
    events.append_event(store, run_id, events.STOP, payload={"status": status})
    store.execute("UPDATE runs SET ended_at=?, status=? WHERE run_id=?",
                  (utcnow(), status, run_id))
    return RunSummary(
        run_id=run_id, run_dir=run_dir, iterations=iterations_done,
        baseline_score=baseline_score,
        champion_score=None if champ is None else champ["score_value"],
        champion_hash=None if champ is None else champ["code_hash"],
        total_cost_usd=budget.spent, status=status,
    )
