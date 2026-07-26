import json
import shutil
import time
from pathlib import Path

from chi.config import CoderCfg
from chi.session.engine import SessionEngine
from chi.session.operator import CliOperatorChat, explore_path, fetch_url
from chi.userconfig import UserConfig, data_dir, save_user_config

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")


def test_explore_lists_dirs_and_reads_files(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "notes.txt").write_text("hello evaluator")
    listing = explore_path(str(tmp_path))
    assert "d sub" in listing and "f notes.txt" in listing
    content = explore_path(str(tmp_path / "notes.txt"))
    assert "hello evaluator" in content
    assert explore_path(str(tmp_path / "nope")).startswith("ERROR")


def test_fetch_strips_tags_via_opener() -> None:
    html = "<html><head><style>x{}</style></head><body><h1>GPU MODE</h1>" \
           "<script>var a=1;</script><p>Cholesky on B200</p></body></html>"
    out = fetch_url("https://example.com/x", opener=lambda url: html)
    assert "GPU MODE" in out and "Cholesky on B200" in out
    assert "script" not in out and "var a=1" not in out
    assert fetch_url("ftp://nope").startswith("ERROR")


def test_cli_operator_chains_explore_then_reply(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    (tmp_path / "proj").mkdir()
    calls: list[str] = []

    def runner(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({"action": "explore", "path": str(tmp_path / "proj")})
        return json.dumps({"action": "reply", "text": "it's an empty project dir"})

    chat = CliOperatorChat(engine, "claude", runner=runner)
    lines = chat.turn("what's in my proj dir?")
    assert lines == ["it's an empty project dir"]
    assert len(calls) == 2
    assert "dir " in calls[1]  # the explore result fed the second call
    progress = engine.poll_events()
    assert any(line.startswith("→ explore(") for line in progress)


def _fake_setup_agent_ok(prompt: str, cwd: Path) -> str:
    for name in ("problem.yaml", "reference.py", "check.py", "bench.py", "candidate.py"):
        shutil.copy(PROBLEM_DIR / name, cwd / name)
    return "pack written and verified"


def test_scaffold_problem_builds_and_verifies(tmp_path: Path) -> None:
    src = tmp_path / "legacy-evaluator"
    src.mkdir()
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.setup_agent_fn = _fake_setup_agent_ok
    lines = engine.scaffold_problem("My Cool Problem!", str(src), "wrap it")
    assert any(line.startswith("problem pack ready:") for line in lines)
    target = data_dir() / "problems" / "my-cool-problem"
    assert (target / "problem.yaml").exists()
    # scaffolded pack is immediately runnable with the configured coders
    script = tmp_path / "script.json"
    script.write_text(json.dumps([GOOD]))
    save_user_config(UserConfig(default_coders=[
        CoderCfg(id="c1", model="scripted", adapter="scripted", script=str(script))
    ]))
    out = engine.launch_problem(str(target), max_iterations=1)
    assert out[-1].startswith("starting run from")
    deadline = time.time() + 60
    while time.time() < deadline:
        if any(l.startswith("run finished") for l in engine.poll_events()):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("scaffolded run never finished")


def test_scaffold_problem_reports_agent_failure(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.setup_agent_fn = lambda prompt, cwd: "I gave up"
    lines = engine.scaffold_problem("broken", str(src))
    assert lines[0].startswith("error: setup agent finished without a problem.yaml")
    assert any("I gave up" in line for line in lines)


def test_scaffold_problem_missing_source(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    lines = engine.scaffold_problem("x", str(tmp_path / "missing"))
    assert lines[0].startswith("error: source")
