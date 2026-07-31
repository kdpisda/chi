import subprocess
from pathlib import Path

import pytest

from chi.agents.registry import build_adapter, known_adapters
from chi.agents.sandbox import (
    DockerSandbox, LocalSandbox, build_docker_command, cli_auth_mounts,
    make_sandbox,
)
from chi.config import CoderCfg


# --- sandbox ----------------------------------------------------------------

def test_docker_command_jails_to_workdir(tmp_path: Path) -> None:
    cmd = build_docker_command("chi-agent:latest", ["claude", "-p", "hi"], tmp_path)
    assert cmd[:4] == ["docker", "run", "--rm", "--network"]
    assert "none" in cmd  # default no network → no exfiltration/direct submit
    # only the workdir is mounted; $HOME (popcorn/CLI auth) is NOT
    mount = f"{tmp_path.resolve()}:/workspace"
    assert mount in cmd
    assert cmd[-3:] == ["claude", "-p", "hi"]
    assert "chi-agent:latest" in cmd
    # exactly one -v mount (the workdir); $HOME is not separately mounted
    assert cmd.count("-v") == 1


def test_docker_command_honors_network_and_mounts(tmp_path: Path) -> None:
    cmd = build_docker_command("img", ["x"], tmp_path, network="host",
                               extra_mounts=[(str(tmp_path), "/extra")])
    assert cmd[cmd.index("--network") + 1] == "host"
    assert f"{tmp_path.resolve()}:/extra" in cmd


def test_local_sandbox_runs_with_injected_runner(tmp_path: Path) -> None:
    calls = {}

    def runner(argv, **kw):
        calls["argv"] = argv
        calls["env"] = kw.get("env")
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    sb = LocalSandbox(runner=runner)
    proc = sb.run(["echo", "hi"], tmp_path, {"X": "1"}, 10)
    assert proc.stdout == "ok" and calls["argv"] == ["echo", "hi"]
    assert calls["env"] == {"X": "1"}


def test_docker_sandbox_wraps_command(tmp_path: Path) -> None:
    seen = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "out", "")

    sb = DockerSandbox(image="img", runner=runner)
    sb.run(["claude", "-p", "x"], tmp_path, {}, 10)
    assert seen["argv"][:2] == ["docker", "run"]
    assert seen["argv"][-3:] == ["claude", "-p", "x"]


def test_make_sandbox_kinds() -> None:
    assert isinstance(make_sandbox("none"), LocalSandbox)
    assert isinstance(make_sandbox("docker", image="img"), DockerSandbox)
    with pytest.raises(ValueError):
        make_sandbox("docker")  # missing image
    with pytest.raises(ValueError):
        make_sandbox("firecracker")  # unknown


# --- docker-cli preset --------------------------------------------------------

def test_docker_command_readonly_mounts(tmp_path: Path) -> None:
    auth = tmp_path / ".claude"
    auth.mkdir()
    cmd = build_docker_command(
        "img", ["x"], tmp_path,
        readonly_mounts=[(str(auth), "/home/agent/.claude")])
    assert f"{auth.resolve()}:/home/agent/.claude:ro" in cmd
    # workdir mount stays read-write (no :ro suffix)
    assert f"{tmp_path.resolve()}:/workspace" in cmd


def test_docker_command_readonly_and_extra_mounts_coexist(tmp_path: Path) -> None:
    cmd = build_docker_command(
        "img", ["x"], tmp_path,
        extra_mounts=[(str(tmp_path), "/extra")],
        readonly_mounts=[(str(tmp_path), "/ro")])
    assert f"{tmp_path.resolve()}:/extra" in cmd
    assert f"{tmp_path.resolve()}:/ro:ro" in cmd


def test_cli_auth_mounts_only_existing(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".grok").mkdir()
    # ~/.codex deliberately absent
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    mounts = cli_auth_mounts()
    assert (str(tmp_path / ".claude"), "/home/agent/.claude") in mounts
    assert (str(tmp_path / ".grok"), "/home/agent/.grok") in mounts
    assert all("/home/agent/.codex" != cont for _, cont in mounts)


def test_cli_auth_mounts_empty_when_no_auth(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert cli_auth_mounts() == []


def test_make_sandbox_docker_cli(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    sb = make_sandbox("docker-cli", image="chi-agent")
    assert isinstance(sb, DockerSandbox)
    assert sb.network == "bridge"  # CLI needs its vendor API
    assert (str(tmp_path / ".claude"), "/home/agent/.claude") in sb.readonly_mounts


def test_make_sandbox_docker_cli_requires_image() -> None:
    with pytest.raises(ValueError):
        make_sandbox("docker-cli")  # missing image


def test_docker_cli_sandbox_renders_ro_auth_mount(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    seen = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    sb = make_sandbox("docker-cli", image="chi-agent")
    sb.runner = runner
    sb.run(["claude", "-p", "x"], tmp_path, {}, 10)
    assert f"{(tmp_path / '.claude').resolve()}:/home/agent/.claude:ro" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--network") + 1] == "bridge"


# --- registry ---------------------------------------------------------------

def test_registry_knows_builtins() -> None:
    names = known_adapters()
    assert {"scripted", "litellm_loop", "cli_subprocess", "json_stream"} <= set(names)


def test_build_adapter_unknown_raises(tmp_path: Path) -> None:
    from chi.config import ProblemConfig

    class _Coder:
        adapter = "nope"
        id = "c1"
        model = "m"
        command = None
        script = None
        sandbox = "none"
        sandbox_image = None
        sandbox_network = "none"

    with pytest.raises(ValueError, match="unknown adapter"):
        build_adapter(_Coder(), store=None, run_id="r", workdir=tmp_path,
                      problem=None, budget=None, policies=None)


def test_build_adapter_cli_requires_command(tmp_path: Path) -> None:
    coder = CoderCfg(id="c1", model="claude", adapter="json_stream")  # no command
    with pytest.raises(ValueError, match="requires 'command'"):
        build_adapter(coder, store=None, run_id="r", workdir=tmp_path,
                      problem=None, budget=None, policies=None)


def test_build_adapter_wires_sandbox(tmp_path: Path) -> None:
    import json
    import shutil

    from chi.config import load_problem
    from chi.store.db import Store

    PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
    run_dir = tmp_path / "run"
    store = Store.open(run_dir)
    shutil.copytree(PROBLEM_DIR, run_dir / "workdir")
    script = tmp_path / "s.json"
    script.write_text(json.dumps(["x=1\n"]))
    from chi.config import PoliciesCfg
    from chi.providers.budgets import BudgetTracker

    coder = CoderCfg(id="c1", model="scripted", adapter="scripted", script=str(script))
    a = build_adapter(coder, store=store, run_id="run", workdir=run_dir / "workdir",
                      problem=load_problem(run_dir / "workdir"),
                      budget=BudgetTracker(1.0), policies=PoliciesCfg())
    assert isinstance(a.sandbox, LocalSandbox)  # default sandbox wired in
