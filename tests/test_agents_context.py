import json
from pathlib import Path

import yaml

from chi.agents.context import build_seed_context
from chi.agents.scripted import ScriptedAdapter
from chi.config import PoliciesCfg, load_problem
from chi.orchestrator.steering import Steering
from chi.providers.budgets import BudgetTracker
from chi.store.db import Store
from chi.store.events import list_events
from chi.store.ledger import add_negative, champion, record_experiment

MANIFEST = {
    "name": "stub", "candidate": "candidate.py",
    "entrypoints": {"correctness": "python check.py {candidate} --seed {seed}",
                     "benchmark": "python bench.py {candidate}"},
    "score": {"metric": "runtime_ms", "direction": "minimize", "repeats": 1},
    "correctness": {"seeds": [1], "tolerance": 1e-6},
}
CHECK = "import sys\nsys.exit(0)\n"
BENCH = ("import json, sys\ntext = open(sys.argv[1]).read()\n"
         "print(json.dumps({'score': float(text.split('=')[1])}))\n")


def _setup(tmp_path: Path):
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "problem.yaml").write_text(yaml.safe_dump(MANIFEST))
    (wd / "check.py").write_text(CHECK)
    (wd / "bench.py").write_text(BENCH)
    (wd / "candidate.py").write_text("score=10.0\n")
    store = Store.open(tmp_path / "run")
    return store, load_problem(wd), wd


def test_seed_context_pulls_store_state(tmp_path: Path) -> None:
    store, prob, wd = _setup(tmp_path)
    record_experiment(store, "r1", code_hash="sha256:c", correct=True, seeds_passed=[1],
                      score_value=8.0, noise_std=0.0, agent_id="a1")
    add_negative(store, "r1", approach_class="threads", summary="GIL",
                 evidence={}, authored_by="a1")
    state = Steering(store, "r1").refresh()
    seed = build_seed_context(store, "r1", prob, wd, state, iteration=3, baseline_score=10.0)
    assert seed.champion_score == 8.0 and seed.baseline_score == 10.0
    assert seed.dead_ends[0]["approach_class"] == "threads"
    assert seed.iteration == 3
    assert "score=10.0" in seed.candidate_code
    assert seed.steering_hash == state.operator_hash


def test_scripted_adapter_runs_script_and_records(tmp_path: Path) -> None:
    store, prob, wd = _setup(tmp_path)
    script = tmp_path / "script.json"
    script.write_text(json.dumps(["score=9.0\n", "score=4.0\n"]))
    adapter = ScriptedAdapter(
        store=store, run_id="r1", agent_id="a1", model="scripted", workdir=wd,
        problem=prob, budget=BudgetTracker(total_usd=1.0), policies=PoliciesCfg(),
        script=str(script),
    )
    state = Steering(store, "r1").refresh()
    for i in range(2):
        seed = build_seed_context(store, "r1", prob, wd, state, i, None)
        out = adapter.run_iteration(seed)
        assert out.evals_run == 1
    assert champion(store, "r1")["score_value"] == 4.0


def test_heartbeat_and_steer_ack(tmp_path: Path) -> None:
    store, prob, wd = _setup(tmp_path)
    store.execute(
        "INSERT INTO agents (agent_id, run_id, adapter, model, started_at)"
        " VALUES ('a1','r1','scripted','m','2026-01-01T00:00:00Z')")
    adapter = ScriptedAdapter(
        store=store, run_id="r1", agent_id="a1", model="m", workdir=wd, problem=prob,
        budget=BudgetTracker(total_usd=1.0), policies=PoliciesCfg(), script=None,
    )
    adapter.heartbeat()
    row = store.query("SELECT last_heartbeat_at FROM agents WHERE agent_id='a1'")[0]
    assert row["last_heartbeat_at"] is not None
    adapter.ack_steering("sha256:x")
    adapter.ack_steering("sha256:x")  # duplicate suppressed
    assert len(list_events(store, "r1", "STEER_ACK")) == 1
