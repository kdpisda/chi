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
