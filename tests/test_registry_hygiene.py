import json
import time
from pathlib import Path

from chi.config import CoderCfg, FleetConfig
from chi.providers.catalog import ModelInfo, ProviderInfo, list_models
from chi.providers.recommend import recommend_setup
from chi.session.engine import SessionEngine
from chi.session.runner import RunHandle
from chi.userconfig import list_sessions, prune_sessions, record_session

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")


def test_prune_drops_missing_run_dirs(tmp_path: Path) -> None:
    real = tmp_path / "real-run"
    real.mkdir()
    record_session({"run_id": "gone", "run_dir": str(tmp_path / "deleted"),
                    "status": "running", "started_at": "2026-01-01T00:00:00Z"})
    record_session({"run_id": "kept", "run_dir": str(real),
                    "status": "done", "started_at": "2026-02-01T00:00:00Z"})
    assert prune_sessions() == 1
    remaining = list_sessions()
    assert [s["run_id"] for s in remaining] == ["kept"]
    assert prune_sessions() == 0  # idempotent


def test_resume_prunes_and_reports(tmp_path: Path) -> None:
    record_session({"run_id": "phantom", "run_dir": str(tmp_path / "nope"),
                    "status": "running", "started_at": "2026-01-01T00:00:00Z"})
    engine = SessionEngine(runs_root=tmp_path / "runs")
    lines = engine.submit("/resume")
    assert any("pruned 1 stale session" in line for line in lines)
    assert any("no sessions recorded yet" in line for line in lines)


def test_run_handle_pins_registry_at_creation(tmp_path: Path, monkeypatch) -> None:
    # the registry target must be resolved when the handle is created, not when
    # the run thread happens to write (this race polluted the real registry)
    dir_a = tmp_path / "data-a"
    dir_b = tmp_path / "data-b"
    monkeypatch.setenv("CHI_DATA_DIR", str(dir_a))
    script = tmp_path / "script.json"
    script.write_text(json.dumps([GOOD]))
    fleet = FleetConfig(
        run_name="pin", problem=PROBLEM_DIR,
        coders=[CoderCfg(id="c1", model="scripted", adapter="scripted",
                         script=str(script))],
    )
    fleet.policies.max_iterations = 1
    handle = RunHandle(fleet, tmp_path / "runs")
    monkeypatch.setenv("CHI_DATA_DIR", str(dir_b))  # env changes after creation
    handle.start()
    deadline = time.time() + 60
    while handle.alive and time.time() < deadline:
        time.sleep(0.05)
    assert (dir_a / "sessions.jsonl").exists()
    assert not (dir_b / "sessions.jsonl").exists()


def test_recommend_skips_deprecated_stale_and_keyless() -> None:
    providers = [
        ProviderInfo(key="anthropic", kind="api", ready=True, detail="key found"),
        ProviderInfo(key="openai", kind="api", ready=False, detail="missing key"),
        ProviderInfo(key="claude", kind="cli", ready=False, detail="not found"),
        ProviderInfo(key="codex", kind="cli", ready=False, detail="not found"),
    ]
    models = [
        # retired 2024 snapshot with juicy cost: must NOT be recommended
        ModelInfo(id="claude-3-opus-20240229", provider="anthropic", kind="api",
                  input_cost_per_m=15.0, output_cost_per_m=75.0, deprecated=True),
        # stale snapshot, not marked deprecated: still excluded by snapshot age
        ModelInfo(id="claude-old-20240607", provider="anthropic", kind="api",
                  input_cost_per_m=20.0, output_cost_per_m=90.0),
        ModelInfo(id="claude-opus-5", provider="anthropic", kind="api",
                  input_cost_per_m=10.0, output_cost_per_m=50.0),
        # keyless provider: excluded no matter the cost
        ModelInfo(id="gpt-5.6", provider="openai", kind="api",
                  input_cost_per_m=30.0, output_cost_per_m=120.0),
    ]
    coders, roles, summary = recommend_setup(providers, models)
    picked = [c.model for c in coders]
    assert picked == ["claude-opus-5"]
    assert roles.get("orchestrator") == "claude-opus-5"
    assert all("20240229" not in line and "gpt-5.6" not in line for line in summary)


def test_list_models_filters_non_chat_modes() -> None:
    registry = {"anthropic": ["claude-opus-5", "voyage-embedding-3"]}
    costs = {
        "claude-opus-5": {"mode": "chat", "input_cost_per_token": 1e-05,
                          "output_cost_per_token": 5e-05},
        "voyage-embedding-3": {"mode": "embedding", "input_cost_per_token": 1e-07},
    }
    models = list_models(["anthropic"], registry=registry, cost_map=costs,
                         which_fn=lambda n: None)
    assert [m.id for m in models] == ["claude-opus-5"]
