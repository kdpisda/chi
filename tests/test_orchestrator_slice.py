import json
from pathlib import Path

from chi.config import FleetConfig
from chi.orchestrator.loop import run_slice, start_run
from chi.store.db import Store
from chi.store.events import list_events

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")
NAIVE = ("def solve(xs: list[float]) -> list[float]:\n"
         "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


def _fleet(tmp_path: Path, script: list[str], iters: int) -> FleetConfig:
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(script))
    return FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": iters, "eval_recency_iters": 100, "repeat_k": 3},
    })


def test_problem_scoped_strategies_beat_global_coder_priors(tmp_path: Path) -> None:
    # Observed live: cholesky-era strategies from the global coder config (CUDA
    # vocabulary) leaked onto a Python prefix-sums problem and the coder recorded
    # a hallucinated CUDA dead end. A problem pack that declares `strategies:`
    # must override the problem-agnostic config priors (round-robin by coder).
    import shutil

    import yaml

    pack = tmp_path / "pack"
    shutil.copytree(PROBLEM_DIR, pack)
    manifest = yaml.safe_load((pack / "problem.yaml").read_text())
    manifest["strategies"] = ["vectorize-with-stdlib", "algorithmic-rewrite"]
    (pack / "problem.yaml").write_text(yaml.safe_dump(manifest))

    sp = tmp_path / "s.json"
    sp.write_text(json.dumps([GOOD]))
    fleet = FleetConfig.model_validate({
        "run_name": "t", "problem": str(pack),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp), "strategy": "tune-champion-fused-kernel"}],
        "policies": {"max_iterations": 1, "eval_recency_iters": 100, "repeat_k": 3},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(summary.run_dir)
    strategies = {r["strategy"] for r in store.query(
        "SELECT strategy FROM experiments WHERE author='c1'")}
    assert strategies == {"vectorize-with-stdlib"}  # problem prior, not the CUDA one


def test_watchdog_state_persists_across_slices(tmp_path: Path) -> None:
    # Under the director, the fleet runs in short slices (2 iterations). The
    # watchdog was constructed fresh per slice, so its kill thresholds could
    # NEVER accumulate — a coder replaying the same candidate forever was never
    # reaped. Seeding from the store's iteration history restores the rule.
    fleet = _fleet(tmp_path, [NAIVE], iters=2)  # same candidate every iteration
    fleet = fleet.model_copy(update={"policies": fleet.policies.model_copy(
        update={"repeat_k": 3, "eval_recency_iters": 100})})  # kill at streak 6
    first = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(first.run_dir)
    assert list_events(store, first.run_id, "WATCHDOG_KILL") == []  # 2 < 6

    run_slice(fleet, first.run_dir, iterations=2)  # cumulative 4
    assert list_events(store, first.run_id, "WATCHDOG_KILL") == []

    run_slice(fleet, first.run_dir, iterations=2)  # cumulative 6 -> kill
    kills = list_events(store, first.run_id, "WATCHDOG_KILL")
    assert len(kills) == 1


def test_preflight_skips_coder_with_dead_eval_history(tmp_path: Path, monkeypatch) -> None:
    # A coder that produces NO evals (e.g. a CLI erroring instantly) must not
    # burn an iteration every slice forever: once its trailing zero-eval history
    # reaches the recency cap, the next slice kills it up front without running.
    from chi.agents.protocol import IterationOutcome
    from chi.agents.scripted import ScriptedAdapter

    def dead_run_iteration(self, seed) -> IterationOutcome:
        self.ack_steering(seed.steering_hash)
        self.heartbeat()
        return IterationOutcome(evals_run=0, note="exit 1")

    monkeypatch.setattr(ScriptedAdapter, "run_iteration", dead_run_iteration)
    fleet = _fleet(tmp_path, [NAIVE], iters=2)
    fleet = fleet.model_copy(update={"policies": fleet.policies.model_copy(
        update={"repeat_k": 50, "eval_recency_iters": 4})})
    first = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(first.run_dir)

    run_slice(fleet, first.run_dir, iterations=2)  # cumulative 4 zero-eval iters
    kills = list_events(store, first.run_id, "WATCHDOG_KILL")
    assert len(kills) >= 1  # recency rule fired across slices

    starts_before = len(list_events(store, first.run_id, "ITERATION_START"))
    run_slice(fleet, first.run_dir, iterations=2)  # dead history -> preflight skip
    starts_after = len(list_events(store, first.run_id, "ITERATION_START"))
    assert starts_after == starts_before  # coder never ran again
    assert len(list_events(store, first.run_id, "WATCHDOG_KILL")) >= 2


def test_run_slice_continues_existing_run_without_new_baseline(tmp_path: Path) -> None:
    fleet = _fleet(tmp_path, [NAIVE, GOOD], iters=1)
    first = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(first.run_dir)
    baselines_before = store.query(
        "SELECT COUNT(*) n FROM experiments WHERE author='baseline'")[0]["n"]

    second = run_slice(fleet, first.run_dir, iterations=1)

    assert second.run_id == first.run_id  # same run, not a new one
    baselines_after = store.query(
        "SELECT COUNT(*) n FROM experiments WHERE author='baseline'")[0]["n"]
    assert baselines_after == baselines_before  # no second baseline eval
    # the slice ran another iteration: total ITERATION_START >= 2
    assert len(list_events(store, first.run_id, "ITERATION_START")) >= 2
