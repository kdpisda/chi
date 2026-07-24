import json
from pathlib import Path

from chi.config import FleetConfig
from chi.orchestrator.loop import start_run
from chi.store.db import Store
from chi.store.events import list_events

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")
NAIVE = ("def solve(xs: list[float]) -> list[float]:\n"
         "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


def _fleet(tmp_path: Path, script: list[str], max_iterations: int = 3) -> FleetConfig:
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script))
    return FleetConfig.model_validate({
        "run_name": "t",
        "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script_path)}],
        "policies": {"max_iterations": max_iterations, "eval_recency_iters": 6,
                      "repeat_k": 3},
    })


def test_scripted_run_improves_champion(tmp_path: Path) -> None:
    summary = start_run(_fleet(tmp_path, [NAIVE, GOOD]), runs_root=tmp_path / "runs")
    assert summary.status == "done"
    assert summary.baseline_score is not None
    assert summary.champion_score < summary.baseline_score
    store = Store.open(summary.run_dir)
    assert len(list_events(store, summary.run_id, "ITERATION_START")) == 3
    assert len(list_events(store, summary.run_id, "STEER_UPDATE")) >= 1


def test_watchdog_kills_unchanging_agent(tmp_path: Path) -> None:
    # Script always replays the same (baseline) candidate: eval dedup means
    # 0 new evals per iteration -> eval-recency kill fires.
    fleet = _fleet(tmp_path, [NAIVE], max_iterations=20)
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    assert summary.status == "stalled"
    store = Store.open(summary.run_dir)
    kills = list_events(store, summary.run_id, "WATCHDOG_KILL")
    assert len(kills) == 1
    # the task went back to pending on kill
    row = store.query("SELECT status FROM tasks")[0]
    assert row["status"] == "pending"


def test_agent_acked_steering(tmp_path: Path) -> None:
    summary = start_run(_fleet(tmp_path, [NAIVE, GOOD]), runs_root=tmp_path / "runs")
    steering_path = summary.run_dir / "steering.md"
    assert steering_path.exists()
    store = Store.open(summary.run_dir)
    acks = list_events(store, summary.run_id, "STEER_ACK")
    assert len(acks) >= 1  # agent acknowledged the steering version it ran under


def test_steer_command_appends(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from chi.cli import app

    summary = start_run(_fleet(tmp_path, [NAIVE, GOOD]), runs_root=tmp_path / "runs")
    r = CliRunner().invoke(app, ["steer", str(summary.run_dir), "try numpy"])
    assert r.exit_code == 0
    assert "try numpy" in (summary.run_dir / "steering.md").read_text()
