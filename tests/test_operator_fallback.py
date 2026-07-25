import json
import time
from pathlib import Path

from chi.config import CoderCfg
from chi.session.engine import SessionEngine, _short_error
from chi.session.operator import CliOperatorChat, _extract_json
from chi.userconfig import UserConfig, load_user_config, save_user_config

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")


def _configure_scripted_coder(tmp_path: Path, **extra) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([GOOD]))
    save_user_config(UserConfig(
        default_coders=[CoderCfg(id="c1", model="scripted", adapter="scripted",
                                 script=str(script))],
        **extra,
    ))


def test_short_error_strips_provider_json() -> None:
    exc = RuntimeError('AuthenticationError - {"error": {"message": "bad key", '
                       '"type": "authentication_error"}}')
    short = _short_error(exc)
    assert "{" not in short and "bad key" not in short
    assert short.startswith("RuntimeError")


def test_extract_json_finds_object_in_noise() -> None:
    raw = 'Sure thing!\n```json\n{"action":"reply","text":"hi"}\n```'
    assert _extract_json(raw) == {"action": "reply", "text": "hi"}
    assert _extract_json("no json here") is None


def test_cli_operator_reply_action(tmp_path: Path) -> None:
    _configure_scripted_coder(tmp_path)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    chat = CliOperatorChat(engine, "claude",
                           runner=lambda prompt: '{"action":"reply","text":"hello!"}')
    assert chat.turn("hi") == ["hello!"]


def test_cli_operator_start_run_action(tmp_path: Path) -> None:
    _configure_scripted_coder(tmp_path)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    calls = {"n": 0}

    def runner(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"action": "start_run", "problem_dir": str(PROBLEM_DIR),
                               "max_iterations": 1})
        return json.dumps({"action": "reply", "text": "Run is going."})

    chat = CliOperatorChat(engine, "claude", runner=runner)
    lines = chat.turn(f"participate with {PROBLEM_DIR}")
    assert any(line.startswith("starting run from") for line in lines)
    assert lines[-1] == "Run is going."
    deadline = time.time() + 60
    while time.time() < deadline:
        if any(l.startswith("run finished") for l in engine.poll_events()):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("run never finished")


def test_operator_failure_offers_cli_and_retries(tmp_path: Path, monkeypatch) -> None:
    # API operator configured but its provider explodes with a JSON-y error
    _configure_scripted_coder(tmp_path, role_models={"orchestrator": "test/broken"})
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude"
                        if name == "claude" else None)

    def exploding(model, messages, **kwargs):
        raise RuntimeError('AuthenticationError - {"error":{"message":"no key"}}')

    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.completion_fn = exploding
    engine.cli_runner_fn = lambda prompt: '{"action":"reply","text":"cli brain here"}'
    engine.ask_fn = lambda question, options: "claude"
    lines = engine.submit("hello chi")
    assert any("chi now thinks via the claude CLI" in line for line in lines)
    assert lines[-1] == "cli brain here"
    assert "{" not in " ".join(lines)  # no raw JSON leaked
    assert load_user_config().operator_cli == "claude"


def test_no_api_model_offers_cli_upfront(tmp_path: Path, monkeypatch) -> None:
    # only a CLI coder configured; claude CLI installed; user accepts the offer
    save_user_config(UserConfig(default_coders=[
        CoderCfg(id="c1", model="claude", adapter="cli_subprocess", command="claude x")
    ]))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude"
                        if name == "claude" else None)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.cli_runner_fn = lambda prompt: '{"action":"reply","text":"ready"}'
    engine.ask_fn = lambda question, options: "claude"
    lines = engine.submit("I want to participate in the gpumode leaderboard")
    assert any("chi now thinks via the claude CLI" in line for line in lines)
    assert lines[-1] == "ready"


def test_declining_fallback_is_graceful(tmp_path: Path, monkeypatch) -> None:
    save_user_config(UserConfig(default_coders=[
        CoderCfg(id="c1", model="claude", adapter="cli_subprocess", command="claude x")
    ]))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude"
                        if name == "claude" else None)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.ask_fn = lambda question, options: "skip"
    lines = engine.submit("hello")
    assert any("conversation stays off" in line for line in lines)
