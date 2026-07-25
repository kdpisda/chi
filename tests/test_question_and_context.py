import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from chi.config import FleetConfig, PoliciesCfg, load_problem
from chi.session.engine import SessionEngine
from chi.tui.app import ChiApp, QuestionScreen

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

NAIVE = ("def solve(xs: list[float]) -> list[float]:\n"
         "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


def _fleet_path(tmp_path: Path, max_iterations: int = 50) -> Path:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([NAIVE]))
    fleet = tmp_path / "fleet.yaml"
    fleet.write_text(yaml.safe_dump({
        "run_name": "q", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script)}],
        "policies": {"max_iterations": max_iterations, "eval_recency_iters": 100,
                      "repeat_k": 100},
    }))
    return fleet


def _wait_ready(engine: SessionEngine) -> None:
    deadline = time.time() + 30
    while engine._handle is not None and not engine._handle.ready.is_set():
        assert time.time() < deadline
        time.sleep(0.02)


def _drain(engine: SessionEngine, timeout_s: float = 60.0) -> list[str]:
    lines: list[str] = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        lines.extend(engine.poll_events())
        if engine.quit_requested or any(l.startswith("run finished") for l in lines):
            return lines
        time.sleep(0.05)
    raise AssertionError(f"never finished: {lines}")


def test_quit_question_stop_choice_quits_after_run(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.ask_fn = lambda question, options: "stop"
    engine.submit(f"/run {_fleet_path(tmp_path)}")
    _wait_ready(engine)
    out = engine.submit("/quit")
    assert any("stopping the run" in line for line in out)
    lines = _drain(engine)
    assert engine.quit_requested is True
    assert any("bye" in line for line in lines)


def test_quit_question_stay_choice_keeps_running(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.ask_fn = lambda question, options: "stay"
    engine.submit(f"/run {_fleet_path(tmp_path, max_iterations=3)}")
    _wait_ready(engine)
    out = engine.submit("/quit")
    assert engine.quit_requested is False
    assert any("run is active" in line for line in out)
    engine.submit("/stop")
    _drain(engine)


def test_quit_without_ask_fn_keeps_old_behavior(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.submit(f"/run {_fleet_path(tmp_path, max_iterations=3)}")
    _wait_ready(engine)
    out = engine.submit("/quit")
    assert engine.quit_requested is False and any("run is active" in l for l in out)
    engine.submit("/stop")
    _drain(engine)


# --- context telemetry -------------------------------------------------------


def _response(content=None, tool_calls=None, prompt_tokens=10):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content,
                                                          tool_calls=tool_calls))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=5),
        _hidden_params={"response_cost": 0.001},
    )


def _tc(name: str, args: dict, call_id: str):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _stub_problem(tmp_path: Path):
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "problem.yaml").write_text(yaml.safe_dump({
        "name": "stub", "candidate": "candidate.py",
        "entrypoints": {"correctness": "{python} check.py {candidate} --seed {seed}",
                         "benchmark": "{python} bench.py {candidate}"},
        "score": {"metric": "runtime_ms", "direction": "minimize", "repeats": 1},
        "correctness": {"seeds": [1], "tolerance": 1e-6},
    }))
    (wd / "check.py").write_text("import sys\nsys.exit(0)\n")
    (wd / "bench.py").write_text(
        "import json, sys\nprint(json.dumps({'score': 1.0}))\n")
    (wd / "candidate.py").write_text("x = 1\n")
    return wd


def test_iteration_outcome_carries_tokens_and_context(tmp_path: Path) -> None:
    from chi.agents.context import build_seed_context
    from chi.agents.litellm_loop import LiteLLMLoopAdapter
    from chi.orchestrator.steering import Steering
    from chi.providers.budgets import BudgetTracker
    from chi.store.db import Store

    wd = _stub_problem(tmp_path)
    store = Store.open(tmp_path / "run")
    turns = [
        _response(tool_calls=[_tc("read_file", {"path": "candidate.py"}, "c1")],
                  prompt_tokens=10),
        _response(content="done", prompt_tokens=20),
    ]
    state = {"i": 0}

    def completion(model, messages, **kwargs):
        resp = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        return resp

    adapter = LiteLLMLoopAdapter(
        store=store, run_id="r1", agent_id="a1", model="test/m", workdir=wd,
        problem=load_problem(wd), budget=BudgetTracker(total_usd=1.0),
        policies=PoliciesCfg(), completion_fn=completion, context_limit=100,
    )
    seed = build_seed_context(store, "r1", load_problem(wd), wd,
                              Steering(store, "r1").refresh(), 0, None)
    out = adapter.run_iteration(seed)
    assert out.tokens_in == 30 and out.tokens_out == 10
    assert out.cost_usd == pytest.approx(0.002)
    assert out.context_pct == pytest.approx(20.0)  # last call: 20 of 100


def test_context_guard_stops_iteration(tmp_path: Path) -> None:
    from chi.agents.context import build_seed_context
    from chi.agents.litellm_loop import LiteLLMLoopAdapter
    from chi.orchestrator.steering import Steering
    from chi.providers.budgets import BudgetTracker
    from chi.store.db import Store

    wd = _stub_problem(tmp_path)
    store = Store.open(tmp_path / "run")
    looping = _response(tool_calls=[_tc("read_file", {"path": "candidate.py"}, "cX")],
                        prompt_tokens=90)
    adapter = LiteLLMLoopAdapter(
        store=store, run_id="r1", agent_id="a1", model="test/m", workdir=wd,
        problem=load_problem(wd), budget=BudgetTracker(total_usd=1.0),
        policies=PoliciesCfg(), completion_fn=lambda model, messages, **kw: looping,
        context_limit=100,
    )
    seed = build_seed_context(store, "r1", load_problem(wd), wd,
                              Steering(store, "r1").refresh(), 0, None)
    out = adapter.run_iteration(seed)
    assert "context guard" in out.note
    assert out.context_pct == pytest.approx(90.0)


# --- question screen ---------------------------------------------------------


async def test_question_screen_toggler_and_digits(tmp_path: Path) -> None:
    app = ChiApp(SessionEngine(runs_root=tmp_path / "runs"), offer_setup=False)
    answers: list = []
    async with app.run_test() as pilot:
        app.push_screen(
            QuestionScreen("Quit anyway?", [("stop", "Stop it"), ("stay", "Stay")]),
            answers.append,
        )
        await pilot.pause()
        await pilot.press("down", "enter")   # toggle to option 2, select
        await pilot.pause()
        app.push_screen(
            QuestionScreen("Again?", [("a", "A"), ("b", "B")]),
            answers.append,
        )
        await pilot.pause()
        await pilot.press("1")               # digit quick-pick
        await pilot.pause()
        app.push_screen(
            QuestionScreen("Cancel me", [("a", "A")]),
            answers.append,
        )
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert answers == ["stay", "a", None]
