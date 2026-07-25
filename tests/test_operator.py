import json
import time
from pathlib import Path
from types import SimpleNamespace

from chi.config import CoderCfg
from chi.session.engine import SessionEngine
from chi.session.operator import MAX_MESSAGES, OperatorChat
from chi.userconfig import UserConfig, save_user_config

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")


def _tc(name: str, args: dict, call_id: str):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _response(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content,
                                                          tool_calls=tool_calls))],
        usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10),
        _hidden_params={"response_cost": 0.002},
    )


def _scripted(turns: list):
    state = {"i": 0}

    def fn(model, messages, **kwargs):
        resp = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        return resp

    return fn


def _configure_defaults(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([GOOD]))
    save_user_config(UserConfig(
        default_coders=[CoderCfg(id="c1", model="scripted", adapter="scripted",
                                 script=str(script))],
        role_models={"orchestrator": "test/op-model"},
    ))


def _drain(engine: SessionEngine, timeout_s: float = 60.0) -> list[str]:
    lines: list[str] = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        lines.extend(engine.poll_events())
        if any(line.startswith("run finished") for line in lines):
            return lines
        time.sleep(0.05)
    raise AssertionError(f"run never finished: {lines}")


def test_free_text_starts_run_via_operator(tmp_path: Path) -> None:
    _configure_defaults(tmp_path)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.completion_fn = _scripted([
        _response(tool_calls=[_tc("start_run", {"problem_dir": str(PROBLEM_DIR),
                                                 "max_iterations": 1}, "c1")]),
        _response(content="Run started — watching it."),
    ])
    lines = engine.submit(f"optimize the prefix sums in {PROBLEM_DIR}")
    assert any(line.startswith("starting run from") for line in lines)
    assert any("Run started" in line for line in lines)
    finished = _drain(engine)
    assert any("run finished [done]" in line for line in finished)
    # operator usage reached the session telemetry
    assert engine.snapshot()["cost_usd"] > 0


def test_operator_answers_from_tools(tmp_path: Path) -> None:
    _configure_defaults(tmp_path)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.completion_fn = _scripted([
        _response(tool_calls=[_tc("run_status", {}, "c1")]),
        _response(content="No run is active right now."),
    ])
    lines = engine.submit("what's happening?")
    assert lines == ["No run is active right now."]


def test_free_text_without_config_demands_setup(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    lines = engine.submit("optimize something")
    assert any("chi needs models" in line for line in lines)


def test_free_text_with_only_cli_coder_needs_api_model(tmp_path: Path) -> None:
    save_user_config(UserConfig(default_coders=[
        CoderCfg(id="c1", model="claude", adapter="cli_subprocess", command="claude x")
    ]))
    engine = SessionEngine(runs_root=tmp_path / "runs")
    lines = engine.submit("hello")
    assert any("needs an API model" in line for line in lines)


def test_launch_problem_rejects_non_problem_dir(tmp_path: Path) -> None:
    _configure_defaults(tmp_path)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    lines = engine.launch_problem(str(tmp_path / "nowhere"))
    assert lines[0].startswith("error:") and "problem.yaml" in lines[0]


def test_busy_note_set_during_operator_turn(tmp_path: Path) -> None:
    _configure_defaults(tmp_path)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    seen: dict = {}

    def completion(model, messages, **kwargs):
        seen["note"] = engine.busy_note
        return _response(content="hi")

    engine.completion_fn = completion
    assert engine.submit("hello") == ["hi"]
    assert seen["note"].startswith("thinking via")
    assert engine.busy_note is None  # cleared after the turn


def test_operator_history_trim_keeps_system(tmp_path: Path) -> None:
    _configure_defaults(tmp_path)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    from chi.providers.budgets import BudgetTracker

    op = OperatorChat(engine, "test/m", BudgetTracker(1.0),
                      completion_fn=_scripted([_response(content="ok")]))
    for i in range(MAX_MESSAGES + 20):
        op.messages.append({"role": "user", "content": f"m{i}"})
    op._trim()
    assert len(op.messages) == MAX_MESSAGES
    assert op.messages[0]["role"] == "system"
    assert op.messages[-1]["content"] == f"m{MAX_MESSAGES + 19}"
