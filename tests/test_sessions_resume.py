import json
import time
from pathlib import Path

from chi.config import FleetConfig
from chi.orchestrator.loop import start_run
from chi.session.engine import SessionEngine
from chi.userconfig import (
    append_history, default_runs_root, list_sessions, load_history, record_session,
)

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")


def _run_once(tmp_path: Path):
    script = tmp_path / "script.json"
    script.write_text(json.dumps([GOOD]))
    fleet = FleetConfig.model_validate({
        "run_name": "hist", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script)}],
        "policies": {"max_iterations": 1},
    })
    return start_run(fleet, runs_root=tmp_path / "runs")


def test_registry_dedupes_and_orders_newest_first() -> None:
    record_session({"run_id": "a", "status": "running", "started_at": "2026-01-01T00:00:00Z"})
    record_session({"run_id": "b", "status": "running", "started_at": "2026-02-01T00:00:00Z"})
    record_session({"run_id": "a", "status": "done", "champion_score": 1.5})
    sessions = list_sessions()
    assert [s["run_id"] for s in sessions] == ["b", "a"]
    merged = sessions[1]
    assert merged["status"] == "done" and merged["champion_score"] == 1.5
    assert merged["started_at"] == "2026-01-01T00:00:00Z"  # merge keeps creation fields


def test_start_run_registers_globally(tmp_path: Path) -> None:
    summary = _run_once(tmp_path)
    sessions = list_sessions()
    assert sessions[0]["run_id"] == summary.run_id
    assert sessions[0]["status"] == "done"
    assert sessions[0]["champion_score"] == summary.champion_score
    assert sessions[0]["run_dir"] == str(summary.run_dir.resolve())


def test_resume_attaches_from_anywhere(tmp_path: Path) -> None:
    summary = _run_once(tmp_path)
    # a fresh engine with a DIFFERENT runs_root — as if launched in another dir
    engine = SessionEngine(runs_root=tmp_path / "elsewhere",
                           picker_fn=lambda m, c, multi: [summary.run_id])
    lines = engine.submit("/resume")
    assert any(line.startswith(f"resumed {summary.run_id}") for line in lines)
    assert any(line.startswith("champion:") for line in lines)
    # inspection commands now target the resumed run
    assert engine.submit("/champion")[0].startswith("champion:")
    # steering reaches the resumed run's steering.md
    engine.submit("/steer try numpy next time")
    steering = (summary.run_dir / "steering.md").read_text()
    assert "try numpy next time" in steering


def test_resume_by_id_and_unknown(tmp_path: Path) -> None:
    summary = _run_once(tmp_path)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    assert engine.submit(f"/resume {summary.run_id}")[0].startswith("resumed")
    assert engine.submit("/resume nope-123")[0].startswith("error: unknown session")


def test_engine_defaults_to_global_runs_root() -> None:
    engine = SessionEngine()
    assert engine.runs_root == default_runs_root()


def test_history_round_trip() -> None:
    append_history("/run fleet.yaml")
    append_history("steer this")
    append_history("")  # ignored
    assert load_history()[-2:] == ["/run fleet.yaml", "steer this"]
