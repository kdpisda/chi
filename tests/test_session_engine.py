import json
import time
from pathlib import Path

import pytest
import yaml

from chi.session.engine import SessionEngine

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")
NAIVE = ("def solve(xs: list[float]) -> list[float]:\n"
         "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CHI_CONFIG_DIR", str(tmp_path / "chi-cfg"))


def _write_fleet(tmp_path: Path, script: list[str], max_iterations: int = 2,
                 slow: bool = False) -> Path:
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script))
    fleet_path = tmp_path / "fleet.yaml"
    fleet_path.write_text(yaml.safe_dump({
        "run_name": "sess",
        "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script_path)}],
        "policies": {"max_iterations": max_iterations, "eval_recency_iters": 50,
                      "repeat_k": 50},
    }))
    return fleet_path


def _drain(engine: SessionEngine, timeout_s: float = 60.0) -> list[str]:
    """Poll until the run finishes; return every transcript line."""
    lines: list[str] = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        lines.extend(engine.poll_events())
        if any(line.startswith("run finished") for line in lines):
            return lines
        time.sleep(0.05)
    raise AssertionError(f"run did not finish; got: {lines}")


def test_help_and_unknown_command(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    assert any("/models" in line for line in engine.submit("/help"))
    assert engine.submit("/nope")[0].startswith("error: unknown command")


def test_run_streams_events_and_finishes(tmp_path: Path) -> None:
    fleet = _write_fleet(tmp_path, [NAIVE, GOOD])
    engine = SessionEngine(runs_root=tmp_path / "runs")
    out = engine.submit(f"/run {fleet}")
    assert out[0].startswith("starting run")
    lines = _drain(engine)
    assert any("iteration 0 complete" in line for line in lines)
    assert any("★ new best" in line for line in lines)
    assert any(line.startswith("run finished [done]") for line in lines)
    assert not engine.has_active_run()


def test_run_refused_while_active_and_free_text_steers(tmp_path: Path) -> None:
    fleet = _write_fleet(tmp_path, [NAIVE, NAIVE, GOOD], max_iterations=3)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.submit(f"/run {fleet}")
    # wait until the run dir exists
    deadline = time.time() + 30
    while engine._handle is not None and not engine._handle.ready.is_set():
        assert time.time() < deadline
        time.sleep(0.02)
    assert engine.submit(f"/run {fleet}")[0].startswith("error: a run is already active")
    if engine.has_active_run():
        out = engine.submit("focus on itertools, skip micro-tuning")
        run_dir = engine._handle.run_dir
        if out and "queued" in out[0]:
            steering = (run_dir / "steering.md").read_text()
            assert "focus on itertools" in steering
    _drain(engine)


def test_stop_ends_run_early(tmp_path: Path) -> None:
    fleet = _write_fleet(tmp_path, [NAIVE], max_iterations=50)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.submit(f"/run {fleet}")
    deadline = time.time() + 30
    while engine._handle is not None and not engine._handle.ready.is_set():
        assert time.time() < deadline
        time.sleep(0.02)
    engine.submit("/stop")
    lines = _drain(engine)
    assert any("run finished [stopped]" in line or "run stop: operator" in line
               for line in lines)


def test_quit_guard_and_idle_free_text(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    out = engine.submit("hello chi")
    # unconfigured session: conversation is gated on model setup
    assert any("chi needs models" in line for line in out)
    assert engine.submit("/quit") == ["bye"]
    assert engine.quit_requested is True


def test_bare_exit_and_quit_words_quit(tmp_path: Path) -> None:
    for word in ("exit", "quit", "EXIT", " Quit "):
        engine = SessionEngine(runs_root=tmp_path / "runs")
        assert engine.submit(word) == ["bye"]
        assert engine.quit_requested is True
    engine = SessionEngine(runs_root=tmp_path / "runs")
    assert engine.submit("/exit") == ["bye"]


def test_status_and_champion_after_run(tmp_path: Path) -> None:
    fleet = _write_fleet(tmp_path, [GOOD], max_iterations=1)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.submit(f"/run {fleet}")
    _drain(engine)
    assert engine.submit("/status")[0].startswith("idle — last run:")
    assert engine.submit("/champion")[0].startswith("champion:")
    ledger_lines = engine.submit("/ledger")
    assert len(ledger_lines) >= 2  # baseline + scripted candidate


def test_run_missing_fleet_file(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    out = engine.submit(f"/run {tmp_path}/nope.yaml")
    assert out[0].startswith("error:") and "not found" in out[0]


def test_progress_stream_drains_without_active_run(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.emit_progress("→ query_ledger(precision)")
    engine.emit_progress("  found 2 entries")
    assert engine.poll_events() == ["→ query_ledger(precision)", "  found 2 entries"]
    assert engine.poll_events() == []
