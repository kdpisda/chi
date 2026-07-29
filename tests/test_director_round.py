import json
from pathlib import Path

from chi.config import FleetConfig
from chi.director.round import RoundRunner
from chi.director.types import RoundResult

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
NAIVE = "def solve(xs):\n    return [sum(xs[: i + 1]) for i in range(len(xs))]\n"
GOOD = "import itertools\n\n\ndef solve(xs):\n    return list(itertools.accumulate(xs))\n"


def test_round_runner_runs_slices_and_reports_best(tmp_path):
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps([NAIVE, GOOD]))
    fleet = FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR), "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": 1, "eval_recency_iters": 100, "repeat_k": 3}})
    run_dir = tmp_path / "runs" / "t-fixed"
    runner = RoundRunner(fleet, run_dir)

    r0 = runner(1)
    assert isinstance(r0, RoundResult)
    assert r0.round_index == 0
    assert r0.best_score is not None

    run_id_after_r0 = runner.run_id
    r1 = runner(1)  # second slice continues the SAME run
    assert r1.round_index == 1
    assert runner.run_id == run_id_after_r0  # same run, not a new one
    assert runner.run_dir is not None
