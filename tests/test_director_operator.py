import json
from pathlib import Path

from chi.config import FleetConfig
from chi.session.director_runner import DirectorHandle

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
NAIVE = "def solve(xs):\n    return [sum(xs[: i + 1]) for i in range(len(xs))]\n"


def _fleet(tmp_path):
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps([NAIVE]))
    return FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR), "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": 1, "eval_recency_iters": 100, "repeat_k": 3}})


def test_director_handle_starts_and_stops(tmp_path):
    h = DirectorHandle(_fleet(tmp_path), runs_root=tmp_path / "runs", brain_fn=None)
    h.start()
    assert h.ready.wait(timeout=60)
    h.request_stop()
    h.join(timeout=60)
    assert not h.alive
    assert h.run_id is not None
    assert h.error is None


def test_operator_dispatch_has_director_tools():
    from chi.session.operator import TOOLS

    names = {t["function"]["name"] for t in TOOLS}
    assert {"start_director", "stop_director", "director_status"} <= names
