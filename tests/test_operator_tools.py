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


def test_explore_refuses_secret_files(tmp_path: Path) -> None:
    (tmp_path / "credentials.env").write_text("ANTHROPIC_API_KEY=sk-secret")
    (tmp_path / "my_token.txt").write_text("ghp_secret")
    assert explore_path(str(tmp_path / "credentials.env")).startswith("REFUSED")
    assert explore_path(str(tmp_path / "my_token.txt")).startswith("REFUSED")


def test_fetch_blocks_internal_hosts() -> None:
    from chi.session.operator import _blocked_host

    assert _blocked_host("http://localhost:8080/x") == "localhost"
    assert _blocked_host("http://127.0.0.1/x") == "127.0.0.1"
    assert _blocked_host("http://169.254.169.254/latest/meta-data") == "169.254.169.254"
    assert fetch_url("http://169.254.169.254/latest/meta-data/").startswith("ERROR")


def test_cli_operator_forces_reply_at_action_cap(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    (tmp_path / "d").mkdir()
    calls = {"n": 0}

    def runner(prompt: str) -> str:
        calls["n"] += 1
        # a model that only ever wants to explore; must be forced to answer
        if "used your action budget" in prompt:
            return '{"action":"reply","text":"Here is what I found so far."}'
        return json.dumps({"action": "explore", "path": str(tmp_path / "d")})

    chat = CliOperatorChat(engine, "claude", runner=runner)
    chat.MAX_ACTIONS = 3
    lines = chat.turn("dig around")
    assert lines == ["Here is what I found so far."]
    assert not any("action limit reached" in line for line in lines)


def test_submit_leaderboard_requires_approval_and_champion(tmp_path: Path) -> None:
    import yaml

    from chi.config import CoderCfg
    from chi.userconfig import UserConfig, save_user_config

    # a problem pack with a leaderboard configured
    pack = tmp_path / "pack"
    shutil.copytree(PROBLEM_DIR, pack)
    manifest = yaml.safe_load((pack / "problem.yaml").read_text())
    manifest["leaderboard"] = "gpumode-776"
    manifest["benchmark_cmd"] = "popcorn-cli bench {candidate}"
    manifest["submit_cmd"] = "popcorn-cli submit {candidate}"
    (pack / "problem.yaml").write_text(yaml.safe_dump(manifest))

    script = tmp_path / "s.json"
    script.write_text(json.dumps([GOOD]))
    save_user_config(UserConfig(default_coders=[
        CoderCfg(id="c1", model="scripted", adapter="scripted", script=str(script))
    ]))
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.launch_problem(str(pack), max_iterations=1)
    deadline = time.time() + 60
    while time.time() < deadline:
        if any(l.startswith("run finished") for l in engine.poll_events()):
            break
        time.sleep(0.05)

    # declined approval → not submitted
    engine.ask_fn = lambda q, options: "hold"
    assert any("held" in line for line in engine.submit_leaderboard("1.5% win"))

    # approved → routes to the (mocked) submission path
    engine.ask_fn = lambda q, options: "submit"
    engine.submit_fn = lambda champ, problem: [f"✓ submitted {champ['score_value']}"]
    lines = engine.submit_leaderboard("real improvement")
    assert any(line.startswith("✓ submitted") for line in lines)


def test_submit_leaderboard_no_leaderboard_configured(tmp_path: Path) -> None:
    from chi.config import CoderCfg
    from chi.userconfig import UserConfig, save_user_config

    script = tmp_path / "s.json"
    script.write_text(json.dumps([GOOD]))
    save_user_config(UserConfig(default_coders=[
        CoderCfg(id="c1", model="scripted", adapter="scripted", script=str(script))
    ]))
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.launch_problem(str(PROBLEM_DIR), max_iterations=1)
    deadline = time.time() + 60
    while time.time() < deadline:
        if any(l.startswith("run finished") for l in engine.poll_events()):
            break
        time.sleep(0.05)
    lines = engine.submit_leaderboard("x")
    assert any("no leaderboard configured" in line for line in lines)


def test_query_ledger_returns_ruled_out_approaches(tmp_path: Path) -> None:
    # audit F: the query_ledger tool routed to /ledger (which ignores the text and
    # picks a table by the literal substring "negative"), so "what's been ruled
    # out?" dumped the experiments table. It must LIKE-match the negative ledger.
    from chi.session.operator import dispatch_tool
    from chi.store import ledger
    from chi.store.db import Store, utcnow

    run_dir = tmp_path / "run"
    store = Store.open(run_dir)
    store.execute("INSERT INTO runs (run_id, problem, fleet_config_json, started_at)"
                  " VALUES ('r1','p','{}',?)", (utcnow(),))
    ledger.add_negative(store, "r1", approach_class="deeper-trees",
                        summary="deeper recursion regressed", evidence={},
                        authored_by="c1")
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine._last_run_dir = run_dir

    output, _ = dispatch_tool(engine, "query_ledger", {"text": "ruled out deeper"})
    assert "deeper-trees" in output
