"""Eval-side sandboxing: an untrusted candidate must not reach the host.

A dogfood run produced a candidate that froze the benchmark's perf_counter and
hijacked `list` in the caller frame — evaluating hostile code on the host is an
attack surface. These tests pin: (a) eval_sandbox="none" keeps today's direct
subprocess behavior; (b) any other kind routes correctness AND benchmark runs
through the Sandbox seam with {python} rendered for the container's PATH;
(c) the docker argv for the eval path is the expected jail.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from chi.agents.sandbox import DockerSandbox, build_docker_command, make_sandbox
from chi.config import load_problem
from chi.eval.runner import evaluate

CHECK = """
import sys
text = open(sys.argv[1]).read()
sys.exit(0 if "GOOD" in text else 1)
"""

BENCH = """
import json, sys
text = open(sys.argv[1]).read()
print(json.dumps({"score": 5.0 if "FAST" in text else 20.0}))
"""

MANIFEST = {
    "name": "stub",
    "candidate": "candidate.py",
    "entrypoints": {
        "correctness": "{python} check.py {candidate} --seed {seed}",
        "benchmark": "{python} bench.py {candidate}",
    },
    "score": {"metric": "runtime_ms", "direction": "minimize", "repeats": 3},
    "correctness": {"seeds": [1, 2], "tolerance": 1e-6},
}


def _mkproblem(tmp_path: Path, candidate_src: str, **manifest_extra) -> Path:
    wd = tmp_path / "wd"
    wd.mkdir()
    manifest = {**MANIFEST, **manifest_extra}
    (wd / "problem.yaml").write_text(yaml.safe_dump(manifest))
    (wd / "check.py").write_text(CHECK)
    (wd / "bench.py").write_text(BENCH)
    (wd / "candidate.py").write_text(candidate_src)
    return wd


class RecordingSandbox:
    """Fake Sandbox: records every dispatched run, returns canned success."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, int]] = []

    def run(self, argv: list[str], cwd: Path, env: dict,
            timeout: int) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), Path(cwd), timeout))
        return subprocess.CompletedProcess(argv, 0, stdout='{"score": 5.0}', stderr="")


# --- (a) default path: eval_sandbox="none" is byte-for-byte today's behavior --

def test_none_kind_is_default_and_unchanged(tmp_path: Path) -> None:
    wd = _mkproblem(tmp_path, "# GOOD FAST\n")
    prob = load_problem(wd)
    assert prob.eval_sandbox == "none"  # absent from problem.yaml → off
    res = evaluate(prob, wd)
    assert res.correct and res.seeds_passed == [1, 2]
    assert res.score_value == 5.0 and res.noise_std == 0.0


def test_none_kind_never_constructs_a_sandbox(tmp_path: Path, monkeypatch) -> None:
    # the default path must not even touch make_sandbox — zero change for
    # existing packs, and no accidental interpreter rewrite on the host
    import chi.eval.runner as runner_mod

    def bomb(*args, **kwargs):
        raise AssertionError("make_sandbox called on the eval_sandbox='none' path")

    monkeypatch.setattr(runner_mod, "make_sandbox", bomb)
    wd = _mkproblem(tmp_path, "# GOOD FAST\n")
    res = evaluate(load_problem(wd), wd)
    assert res.correct and res.score_value == 5.0


# --- (b) sandboxed path: runs dispatch through the seam, {python} → python3 ---

def test_sandboxed_eval_routes_all_runs_through_sandbox(tmp_path: Path) -> None:
    wd = _mkproblem(tmp_path, "# GOOD FAST\n",
                    eval_sandbox="docker", eval_sandbox_image="chi-eval")
    fake = RecordingSandbox()
    res = evaluate(load_problem(wd), wd, sandbox=fake)
    assert res.correct and res.seeds_passed == [1, 2]
    assert res.score_value == 5.0
    # 2 correctness seeds + 3 benchmark repeats, all through the sandbox
    assert len(fake.calls) == 5
    correctness = [argv for argv, _, _ in fake.calls if "check.py" in argv]
    benchmark = [argv for argv, _, _ in fake.calls if "bench.py" in argv]
    assert len(correctness) == 2 and len(benchmark) == 3
    assert correctness[0][-2:] == ["--seed", "1"]
    assert correctness[1][-2:] == ["--seed", "2"]
    for argv, cwd, timeout in fake.calls:
        assert cwd == wd
        assert timeout == load_problem(wd).timeout_seconds


def test_sandboxed_eval_renders_python_as_python3(tmp_path: Path) -> None:
    # sys.executable is a host path that doesn't exist inside the container —
    # sandboxed entrypoints must resolve {python} to python3 on the image PATH
    wd = _mkproblem(tmp_path, "# GOOD FAST\n",
                    eval_sandbox="docker", eval_sandbox_image="chi-eval")
    fake = RecordingSandbox()
    evaluate(load_problem(wd), wd, sandbox=fake)
    for argv, _, _ in fake.calls:
        assert argv[0] == "python3"
        assert sys.executable not in argv


def test_sandboxed_correctness_failure_still_gates(tmp_path: Path) -> None:
    class FailingSandbox(RecordingSandbox):
        def run(self, argv, cwd, env, timeout):
            super().run(argv, cwd, env, timeout)
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    wd = _mkproblem(tmp_path, "# BAD\n",
                    eval_sandbox="docker", eval_sandbox_image="chi-eval")
    fake = FailingSandbox()
    res = evaluate(load_problem(wd), wd, sandbox=fake)
    assert not res.correct and res.score_value is None
    assert len(fake.calls) == 1  # gate stops at the first failing seed


def test_docker_kind_without_image_raises(tmp_path: Path) -> None:
    # wiring check: without an injected sandbox the kind goes to make_sandbox,
    # which enforces the image requirement before anything runs
    wd = _mkproblem(tmp_path, "# GOOD FAST\n", eval_sandbox="docker")
    with pytest.raises(ValueError, match="sandbox_image"):
        evaluate(load_problem(wd), wd)


# --- (c) the eval jail: expected docker argv, no docker needed ----------------

def test_eval_sandbox_docker_jail_argv(tmp_path: Path) -> None:
    sb = make_sandbox("docker", image="chi-eval")
    assert isinstance(sb, DockerSandbox)
    assert sb.network == "none"  # hostile candidate gets no exfiltration path
    cmd = build_docker_command(
        "chi-eval", ["python3", "check.py", "candidate.py", "--seed", "1"], tmp_path)
    assert cmd[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert f"{tmp_path.resolve()}:/workspace" in cmd
    assert cmd[cmd.index("-w") + 1] == "/workspace"
    # exactly one -v mount (the workdir); $HOME/popcorn auth is not mounted
    assert cmd.count("-v") == 1
    assert cmd[-5:] == ["python3", "check.py", "candidate.py", "--seed", "1"]
