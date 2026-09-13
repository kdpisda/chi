"""Deterministic v1 run loop: one coder agent, steering, watchdog, budgets."""

import json
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
    resolve_strategy,
)
from chi.eval.hashing import code_hash
from chi.eval.holdout import HoldoutVerdict, build_holdout_gate
from chi.eval.runner import evaluate
from chi.orchestrator.steering import Steering
from chi.orchestrator.watchdog import Watchdog
from chi.providers.budgets import BudgetExceededError, BudgetTracker
from chi.store import events, ledger, tasks
from chi.store.db import Store, utcnow


# Fed into the next iteration's seed after a zero-eval timeout: the fastest
# useful adaptation is a drastically smaller edit, not a retry of the same one.
TIMEOUT_MUTATION_NOTE = (
    "Your last attempt TIMED OUT with no measured result. Make the SMALLEST"
    " possible change this iteration — edit one focused region, not a rewrite —"
    " so it completes and benchmarks within the time limit."
)


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
    holdout: HoldoutVerdict | None = None


def holdout_ignore(problem: ProblemConfig) -> Callable | None:
    """copytree `ignore` that keeps the held-out workload out of an agent workdir.

    The whole point of a holdout is that the loop's selection pressure can't
    reach it. An agent with a shell and a copy of the holdout script would tune
    against it within a few iterations, so the files never leave chi's own
    directory — see chi/eval/holdout.py.
    """
    if problem.holdout is None or not problem.holdout.files:
        return None
    private = set(problem.holdout.files)
    return lambda directory, names: {n for n in names if n in private}


def prepare_holdout_dir(source_problem_dir: Path, run_dir: Path,
                        problem: ProblemConfig) -> Path | None:
    """Materialise chi's private holdout directory (the full, unredacted pack)."""
    if problem.holdout is None:
        return None
    holdout_dir = Path(run_dir) / "holdout"
    if not holdout_dir.exists():
        shutil.copytree(source_problem_dir, holdout_dir)
    return holdout_dir


def _record_holdout(store: Store, run_id: str, phase: str, payload: dict) -> None:
    events.append_event(store, run_id, events.HOLDOUT, agent_id="holdout",
                        payload={"phase": phase, **payload})


def baseline_holdout(store: Store, run_id: str) -> float | None:
    """The baseline's held-out score, recorded once when the run established it."""
    rows = store.query(
        "SELECT payload_json FROM events WHERE run_id=? AND type=?"
        " ORDER BY event_id", (run_id, events.HOLDOUT))
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload.get("phase") == "baseline" and payload.get("score") is not None:
            return float(payload["score"])
    return None


def _gate_champion(store: Store, run_id: str, run_dir: Path, problem: ProblemConfig,
                   base_workdir: Path, baseline_score: float | None,
                   champion_score: float | None) -> HoldoutVerdict | None:
    """Re-score the exported champion on the held-out workload and record the verdict."""
    gate = build_holdout_gate(problem, Path(run_dir) / "holdout")
    if gate is None:
        return None
    verdict = gate.verify(
        base_workdir / problem.candidate,
        baseline_holdout=baseline_holdout(store, run_id),
        baseline_score=baseline_score, champion_score=champion_score,
    )
    _record_holdout(store, run_id, "champion", verdict.as_payload())
    return verdict


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
    from chi.eval.registry import build_backend

    backend = build_backend(getattr(problem, "eval_backend", "popcorn"),
                            leaderboard=problem.leaderboard,
                            benchmark_cmd=problem.benchmark_cmd or "",
                            submit_cmd=problem.submit_cmd or "")

    def emit(line: str) -> None:
        events.append_event(store, run_id, events.STATUS, payload={"auto_submit": line})

    return AutoSubmitter(backend, direction=problem.score.direction,
                         margin_pct=problem.promote_margin_pct,
                         baseline=problem.current_best, emit=emit)


def _seed_watchdog(store, run_id: str, agent_id: str, policies: PoliciesCfg) -> Watchdog:
    """Watchdog with counters restored from the agent's iteration history.

    Under the director the fleet runs in short slices; a watchdog built fresh per
    slice can never reach its kill thresholds. Trailing ITERATION_COMPLETE events
    carry evals_run and the evaluated candidate_hash, which is all the rules need.
    """
    watchdog = Watchdog(policies)
    window = max(2 * policies.repeat_k, policies.eval_recency_iters) + 1
    rows = store.query(
        "SELECT payload_json FROM events WHERE run_id=? AND agent_id=? AND type=?"
        " ORDER BY event_id DESC LIMIT ?",
        (run_id, agent_id, events.ITERATION_COMPLETE, window),
    )
    payloads = [json.loads(r["payload_json"]) for r in rows]  # newest first
    iters_without_eval = 0
    for payload in payloads:
        if payload.get("evals_run", 0) == 0:
            iters_without_eval += 1
        else:
            break
    last_hash: str | None = None
    hash_streak = 0
    for payload in payloads:
        h = payload.get("candidate_hash")
        if h is None:
            break
        if last_hash is None:
            last_hash, hash_streak = h, 1
        elif h == last_hash:
            hash_streak += 1
        else:
            break
    watchdog.seed(iters_without_eval=iters_without_eval, last_hash=last_hash,
                  hash_streak=hash_streak)
    return watchdog


def _run_coder(coder, workdir, task_id, strategy, store, run_id, problem, budget,
               policies, steering, baseline_score, stop_event, completion_fn,
               coder_status, auto_submitter=None) -> None:
    """One coder agent's iteration loop, in its own worktree (runs in a thread)."""
    adapter = _make_adapter(coder, store, run_id, workdir, problem, budget, policies,
                            completion_fn)
    watchdog = _seed_watchdog(store, run_id, coder.id, policies)
    # a coder with a dead-eval history (CLI erroring instantly, slice after slice)
    # is reaped up front instead of burning another iteration every round
    verdict = watchdog.preflight()
    if verdict.action == "kill":
        events.append_event(store, run_id, events.WATCHDOG_KILL, agent_id=coder.id,
                            task_id=task_id, payload={"reason": verdict.reason})
        tasks.release_task(store, run_id, task_id)
        coder_status[coder.id] = ("stalled", 0)
        return
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
        candidate_hash = code_hash((workdir / problem.candidate).read_text())
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
        # candidate_hash in the payload lets a later slice re-seed the watchdog
        events.append_event(store, run_id, events.ITERATION_COMPLETE, agent_id=coder.id,
                            task_id=task_id,
                            payload={"iteration": iteration, "evals_run": outcome.evals_run,
                                     "note": outcome.note, "strategy": strategy,
                                     "context_pct": outcome.context_pct,
                                     "steering_hash": state.operator_hash,
                                     "candidate_hash": watchdog_hash},
                            cost_usd=outcome.cost_usd, tokens_in=outcome.tokens_in,
                            tokens_out=outcome.tokens_out)
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
        if outcome.evals_run == 0 and outcome.note == "timeout":
            # fast-adapt: the iteration timed out before measuring anything, so
            # repeating the same large edit is pure waste — steer the coder to a
            # much smaller change NOW, ahead of the watchdog's slower thresholds
            mutation_note = TIMEOUT_MUTATION_NOTE
        verdict = watchdog.observe_iteration(new_evals=outcome.evals_run,
                                             candidate_hash=watchdog_hash,
                                             note=outcome.note)
        if verdict.action == "mutate" and not mutation_note:
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
    """Copy the champion into the shared workdir from its eval-time archive.

    Source the champion from run_dir/champions/<hash>.py (written by evaluate), NOT the
    coder's live workdir. On a single-coder run src==dst made this a no-op, and coders
    revert candidate.py to a losing attempt after a benchmark — so the live file is not
    the champion. The archive holds the champion's exact verified bytes.
    """
    archive = run_dir / "champions" / f"{champ['code_hash']}.py"
    dst = base_workdir / problem.candidate
    if archive.exists():
        shutil.copy(archive, dst)


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
    # the holdout pack is copied WHOLE into chi's private directory first, then
    # the agent workdir is copied without the held-out files
    source_problem = load_problem(fleet.problem)
    prepare_holdout_dir(fleet.problem, run_dir, source_problem)
    base_workdir = run_dir / "workdir"
    shutil.copytree(fleet.problem, base_workdir, ignore=holdout_ignore(source_problem))
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

    # the held-out workload's baseline, measured once on the untouched candidate.
    # Every later generalization verdict is relative to this number, so it has to
    # be taken before any agent has edited anything.
    gate = build_holdout_gate(problem, run_dir / "holdout")
    if gate is not None:
        median, samples, detail = gate.measure(base_workdir / problem.candidate)
        _record_holdout(store, run_id, "baseline",
                        {"score": median, "samples": samples, "detail": detail})

    return _launch_fleet(
        store, run_id, run_dir, fleet, problem, policies, coders, steering,
        auto_submitter, budget, baseline_score, completion_fn, stop_event,
        sessions_path,
    )


def _launch_fleet(
    store: Store,
    run_id: str,
    run_dir: Path,
    fleet: FleetConfig,
    problem: ProblemConfig,
    policies: PoliciesCfg,
    coders: list,
    steering: Steering,
    auto_submitter,
    budget: BudgetTracker,
    baseline_score: float | None,
    completion_fn: Callable | None,
    stop_event: threading.Event | None,
    sessions_path: Path | None,
    gate_holdout: bool = True,
) -> RunSummary:
    """Run all coders for policies.max_iterations, aggregate, export champion.

    Shared by start_run (round 1, after it creates the run + baseline) and
    run_slice (rounds 2..N, continuing the same run). Deterministic; no LLM.

    `gate_holdout` is off for a director slice: the director runs the holdout
    itself, and only on a win that already survived the NoiseGuard. Paying for it
    at every slice boundary too would double the cost and record a second,
    possibly disagreeing verdict for the same champion.
    """
    from chi.userconfig import record_session

    base_workdir = run_dir / "workdir"
    # one worktree + task + watchdog per coder; all share the blackboard store so
    # dedup, the negative ledger, and champion selection work across the fleet
    coder_status: dict[str, tuple[str, int]] = {}
    threads: list[threading.Thread] = []
    for index, coder in enumerate(coders):
        coder_workdir = run_dir / f"workdir-{coder.id}" if len(coders) > 1 else base_workdir
        if coder_workdir != base_workdir and not coder_workdir.exists():
            shutil.copytree(fleet.problem, coder_workdir,
                            ignore=holdout_ignore(problem))
        # INSERT OR IGNORE: a later slice re-uses the agent row from the first slice
        store.execute(
            "INSERT OR IGNORE INTO agents (agent_id, run_id, adapter, model, workdir,"
            " started_at) VALUES (?,?,?,?,?,?)",
            (coder.id, run_id, coder.adapter, coder.model, str(coder_workdir), utcnow()),
        )
        strategy = resolve_strategy(problem, coder, index)
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
    holdout_verdict: HoldoutVerdict | None = None
    if champ is not None and champ["author"] not in (None, "baseline"):
        _export_champion(store, run_id, champ, coders, run_dir, base_workdir, problem)
        # the win is only a win if it survives a workload the fleet never saw
        if gate_holdout:
            holdout_verdict = _gate_champion(
                store, run_id, run_dir, problem, base_workdir, baseline_score,
                champ["score_value"])
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
        total_cost_usd=budget.spent, status=status, holdout=holdout_verdict,
    )


def run_slice(
    fleet: FleetConfig,
    run_dir: Path,
    *,
    iterations: int,
    completion_fn: Callable | None = None,
    stop_event: threading.Event | None = None,
    sessions_path: Path | None = None,
) -> RunSummary:
    """Run `iterations` more iterations on an already-created run in run_dir.

    Unlike start_run this does NOT create the run dir, copy the pack, or
    re-establish baseline — the Director uses it for rounds 2..N so a sustained
    run is one logical run with a growing store, not a chain of fresh runs.
    """
    store = Store.open(run_dir)
    run_id = store.query("SELECT run_id FROM runs ORDER BY started_at LIMIT 1")[0]["run_id"]
    problem = load_problem(run_dir / "workdir")
    baseline_row = store.query(
        "SELECT score_value FROM experiments WHERE run_id=? AND author='baseline'"
        " ORDER BY ts LIMIT 1", (run_id,))
    baseline_score = baseline_row[0]["score_value"] if baseline_row else None
    sliced = fleet.model_copy(update={
        "policies": fleet.policies.model_copy(update={"max_iterations": iterations})})
    policies = sliced.policies
    budget = BudgetTracker(sliced.budgets.total_usd, sliced.budgets.per_role_usd,
                           store=store, run_id=run_id)
    coders = resolve_coders(sliced)
    steering = Steering(store, run_id, problem.score.direction)
    auto_submitter = _build_auto_submitter(store, run_id, problem)
    return _launch_fleet(
        store, run_id, run_dir, sliced, problem, policies, coders, steering,
        auto_submitter, budget, baseline_score, completion_fn, stop_event,
        sessions_path, gate_holdout=False,
    )
