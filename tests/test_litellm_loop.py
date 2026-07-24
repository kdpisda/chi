import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from chi.agents.context import build_seed_context
from chi.agents.litellm_loop import LiteLLMLoopAdapter
from chi.config import PoliciesCfg, load_problem
from chi.orchestrator.steering import Steering
from chi.providers.budgets import BudgetTracker
from chi.store.db import Store
from chi.store.ledger import champion

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


def _tc(name: str, args: dict, call_id: str):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _response(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content,
                                                          tool_calls=tool_calls))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        _hidden_params={"response_cost": 0.001},
    )


def _scripted_completion(turns: list):
    """Return a completion_fn that plays back canned responses in order."""
    state = {"i": 0}

    def fn(model: str, messages: list, **kwargs):
        resp = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        return resp

    return fn


def _setup(tmp_path: Path):
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "problem.yaml").write_text(yaml.safe_dump(MANIFEST))
    (wd / "check.py").write_text(CHECK)
    (wd / "bench.py").write_text(BENCH)
    (wd / "candidate.py").write_text("score=10.0\n")
    store = Store.open(tmp_path / "run")
    return store, load_problem(wd), wd


def _adapter(store, prob, wd, turns):
    return LiteLLMLoopAdapter(
        store=store, run_id="r1", agent_id="a1", model="test/m", workdir=wd,
        problem=prob, budget=BudgetTracker(total_usd=1.0), policies=PoliciesCfg(),
        completion_fn=_scripted_completion(turns),
    )


def test_model_writes_and_evals_candidate(tmp_path: Path) -> None:
    store, prob, wd = _setup(tmp_path)
    turns = [
        _response(tool_calls=[_tc("write_file",
                                  {"path": "candidate.py", "content": "score=3.0\n"}, "c1")]),
        _response(tool_calls=[_tc("run_eval", {}, "c2")]),
        _response(content="done"),
    ]
    adapter = _adapter(store, prob, wd, turns)
    state = Steering(store, "r1").refresh()
    seed = build_seed_context(store, "r1", prob, wd, state, 0, None)
    out = adapter.run_iteration(seed)
    assert out.evals_run == 1
    assert champion(store, "r1")["score_value"] == 3.0


def test_path_escape_is_refused_not_raised(tmp_path: Path) -> None:
    store, prob, wd = _setup(tmp_path)
    turns = [
        _response(tool_calls=[_tc("read_file", {"path": "../../etc/passwd"}, "c1")]),
        _response(content="ok"),
    ]
    adapter = _adapter(store, prob, wd, turns)
    state = Steering(store, "r1").refresh()
    out = adapter.run_iteration(build_seed_context(store, "r1", prob, wd, state, 0, None))
    assert out.evals_run == 0  # loop completed without crashing


def test_loop_stops_at_max_tool_calls(tmp_path: Path) -> None:
    store, prob, wd = _setup(tmp_path)
    forever = _response(tool_calls=[_tc("read_file", {"path": "candidate.py"}, "cX")])
    adapter = _adapter(store, prob, wd, [forever])
    adapter.max_tool_calls = 4
    state = Steering(store, "r1").refresh()
    out = adapter.run_iteration(build_seed_context(store, "r1", prob, wd, state, 0, None))
    assert out.evals_run == 0  # terminated by cap, not hang
