"""Store-backed replay of a director run's rounds via /resume and /director."""

from pathlib import Path

from chi.session.engine import SessionEngine
from chi.store import events
from chi.store.db import Store, utcnow
from chi.userconfig import record_session

ROUNDS = [
    {"round": 1, "state": "exploring", "best": None, "benchmarks_run": 2,
     "cost_usd": 0.10, "cum_benchmarks": 2, "cum_cost": 0.10, "researched": False},
    {"round": 2, "state": "improving", "best": 0.42, "benchmarks_run": 3,
     "cost_usd": 0.15, "cum_benchmarks": 5, "cum_cost": 0.25, "researched": False},
    {"round": 3, "state": "plateaued", "best": 0.42, "benchmarks_run": 1,
     "cost_usd": 0.05, "cum_benchmarks": 6, "cum_cost": 0.30, "researched": True},
]


def _seed_run(tmp_path: Path, run_id: str, rounds: list[dict]) -> Path:
    """Seed a run store (runs row + DIRECTOR_ROUND events) and register the session."""
    run_dir = tmp_path / "runs" / run_id
    store = Store.open(run_dir)
    store.execute(
        "INSERT INTO runs (run_id, problem, fleet_config_json, started_at, status)"
        " VALUES (?,?,?,?,?)",
        (run_id, "prob", "{}", utcnow(), "done"),
    )
    for payload in rounds:
        events.append_event(store, run_id, events.DIRECTOR_ROUND,
                            payload=payload, cost_usd=payload["cost_usd"])
    store.close()
    record_session({"run_id": run_id, "run_dir": str(run_dir), "status": "done",
                    "started_at": utcnow(), "problem": "prob"})
    return run_dir


class _LiveDir:
    """Minimal stand-in for DirectorHandle (alive, with run_id/run_dir)."""

    alive = True

    def __init__(self, run_id: str, run_dir: Path) -> None:
        self.run_id = run_id
        self.run_dir = run_dir


def test_resume_replays_director_rounds(tmp_path: Path) -> None:
    _seed_run(tmp_path, "dir-1", ROUNDS)
    engine = SessionEngine(runs_root=tmp_path / "elsewhere")
    lines = engine.submit("/resume dir-1")
    assert any(line.startswith("resumed dir-1") for line in lines)
    replay = [line for line in lines if line.startswith("round ")]
    assert len(replay) == 3
    assert replay[0] == "round 1: exploring · best None · Σ 2 benches $0.10"
    assert replay[1] == "round 2: improving · best 0.42 · Σ 5 benches $0.25"
    assert replay[2] == "round 3: plateaued · best 0.42 · Σ 6 benches $0.30"
    # no director in this process → the run is marked historical
    assert any("historical" in line for line in lines)
    assert not any("LIVE" in line for line in lines)


def test_director_command_replays_after_resume(tmp_path: Path) -> None:
    _seed_run(tmp_path, "dir-2", ROUNDS)
    engine = SessionEngine(runs_root=tmp_path / "elsewhere")
    engine.submit("/resume dir-2")
    lines = engine.submit("/director")
    assert len([line for line in lines if line.startswith("round ")]) == 3
    assert "historical" in lines[-1]


def test_director_command_marks_live_director(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path, "dir-3", ROUNDS)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine._director = _LiveDir("dir-3", run_dir)
    lines = engine.submit("/director")
    assert len([line for line in lines if line.startswith("round ")]) == 3
    assert "LIVE" in lines[-1] and "dir-3" in lines[-1]


def test_resumed_run_stays_historical_while_other_director_lives(tmp_path: Path) -> None:
    _seed_run(tmp_path, "dir-a", ROUNDS)
    other_dir = _seed_run(tmp_path, "dir-b", ROUNDS[:1])
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine._director = _LiveDir("dir-b", other_dir)
    lines = engine.submit("/resume dir-a")
    # the resumed run is not the live director's run → historical marker
    assert any("historical" in line for line in lines)
    assert not any("LIVE" in line for line in lines)


def test_resume_without_rounds_degrades_gracefully(tmp_path: Path) -> None:
    _seed_run(tmp_path, "plain-1", rounds=[])
    engine = SessionEngine(runs_root=tmp_path / "elsewhere")
    lines = engine.submit("/resume plain-1")
    assert any(line.startswith("resumed plain-1") for line in lines)
    # a non-director run replays nothing and adds no director noise
    assert not any(line.startswith("round ") for line in lines)
    assert not any("director" in line.lower() for line in lines)
    # asking explicitly gets a clear answer, not a crash
    replay = engine.submit("/director")
    assert any("no director rounds recorded" in line for line in replay)


def test_director_command_without_any_run(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    lines = engine.submit("/director")
    assert lines and lines[0].startswith("no run")
