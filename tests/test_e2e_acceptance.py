"""Phase 1 acceptance: design doc §12 items provable in CI (no live providers)."""

import json
from pathlib import Path

from chi.config import FleetConfig
from chi.orchestrator.loop import start_run
from chi.store.db import Store
from chi.store.events import list_events

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

NAIVE = ("def solve(xs: list[float]) -> list[float]:\n"
         "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")
BETTER = ("def solve(xs: list[float]) -> list[float]:\n"
          "    out = []\n    total = 0.0\n"
          "    for x in xs:\n        total += x\n        out.append(total)\n"
          "    return out\n")
WRONG = "def solve(xs):\n    return xs\n"
BEST = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")


def test_full_acceptance_run(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    # wrong candidate is gated; identical resubmission is deduped; best wins
    script.write_text(json.dumps([WRONG, BETTER, BETTER, BEST]))
    fleet = FleetConfig.model_validate({
        "run_name": "accept",
        "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script)}],
        "policies": {"max_iterations": 4, "eval_recency_iters": 10, "repeat_k": 5},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(summary.run_dir)

    # every attempt recorded with a code hash; identical candidate deduped
    hashes = [r["code_hash"] for r in store.query("SELECT code_hash FROM experiments")]
    assert len(hashes) == len(set(hashes)) == 4  # baseline, wrong, better, best

    # incorrect candidates are gated (recorded but can't be champion)
    assert summary.champion_score is not None and summary.champion_score > 0

    # champion improved on the baseline
    assert summary.champion_score < summary.baseline_score

    # the run trace is reconstructable: iteration events + steering history exist
    assert len(list_events(store, summary.run_id, "ITERATION_COMPLETE")) == 4
    assert len(list_events(store, summary.run_id, "STEER_UPDATE")) >= 1
    assert (summary.run_dir / "mirror" / "events.jsonl").exists()
