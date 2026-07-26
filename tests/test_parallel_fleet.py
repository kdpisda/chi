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
          "    for x in xs:\n        total += x\n        out.append(total)\n    return out\n")
BEST = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")


def _coder(tmp_path: Path, cid: str, sources: list[str], strategy: str) -> dict:
    script = tmp_path / f"{cid}.json"
    script.write_text(json.dumps(sources))
    return {"id": cid, "model": "scripted", "adapter": "scripted",
            "script": str(script), "strategy": strategy}


def test_three_agents_run_in_parallel_and_best_wins(tmp_path: Path) -> None:
    fleet = FleetConfig.model_validate({
        "run_name": "fleet", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [
            _coder(tmp_path, "claude-A", [NAIVE], "blocked"),
            _coder(tmp_path, "codex-B", [BETTER], "left-looking"),
            _coder(tmp_path, "grok-C", [BEST], "fused-tensorcore"),
        ],
        "policies": {"max_iterations": 1, "eval_recency_iters": 50, "repeat_k": 50},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    assert summary.status == "done"
    store = Store.open(summary.run_dir)

    # every agent got its own worktree and ran
    agents = {r["agent_id"] for r in store.query("SELECT agent_id FROM agents")}
    assert {"claude-A", "codex-B", "grok-C"} <= agents

    # champion is the best across all three, and its strategy is recorded
    champ = store.query("SELECT * FROM experiments WHERE correct=1 AND score_value IS NOT NULL"
                        " ORDER BY score_value ASC LIMIT 1")[0]
    assert champ["author"] == "grok-C" and champ["strategy"] == "fused-tensorcore"
    assert summary.champion_score == champ["score_value"]

    # each agent explored its own strategy — recorded on its experiments
    strategies = {r["strategy"] for r in store.query(
        "SELECT strategy FROM experiments WHERE author LIKE '%-%'")}
    assert {"blocked", "left-looking", "fused-tensorcore"} <= strategies

    # the winning candidate is exported into the shared workdir for /champion --export
    exported = (summary.run_dir / "workdir" / "candidate.py").read_text()
    assert "itertools.accumulate" in exported


def test_dedup_shared_across_agents(tmp_path: Path) -> None:
    # two agents propose the identical candidate; the registry dedups across them
    fleet = FleetConfig.model_validate({
        "run_name": "dedup", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [
            _coder(tmp_path, "a1", [BEST], "s1"),
            _coder(tmp_path, "a2", [BEST], "s2"),
        ],
        "policies": {"max_iterations": 1, "eval_recency_iters": 50, "repeat_k": 50},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(summary.run_dir)
    # baseline + one shared BEST hash = 2 distinct experiments, not 3
    hashes = [r["code_hash"] for r in store.query("SELECT code_hash FROM experiments")]
    assert len(hashes) == len(set(hashes)) == 2


def test_single_coder_still_uses_shared_workdir(tmp_path: Path) -> None:
    fleet = FleetConfig.model_validate({
        "run_name": "solo", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [_coder(tmp_path, "solo", [BEST], "only")],
        "policies": {"max_iterations": 1},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    assert summary.status == "done" and summary.champion_score is not None
    assert (summary.run_dir / "workdir").exists()
    assert not (summary.run_dir / "workdir-solo").exists()  # single coder = no per-agent dir
