import os
import subprocess
from pathlib import Path

from chi.eval.popcorn_shim import agent_env, install_shim


def _fake_real_popcorn(tmp_path: Path) -> Path:
    """A stand-in 'real' popcorn-cli that just echoes its mode."""
    real = tmp_path / "real_popcorn"
    real.write_text("#!/bin/bash\necho \"REAL popcorn ran: $*\"\n")
    real.chmod(0o755)
    return real


def _run_shim(shim_dir: Path, real: Path, args: list[str], gated: bool) -> subprocess.CompletedProcess:
    env = agent_env(os.environ, shim_dir, real_popcorn=str(real))
    if gated:
        env["CHI_GATED_SUBMIT"] = "1"
    return subprocess.run([str(shim_dir / "popcorn-cli"), *args],
                          capture_output=True, text=True, env=env)


def test_shim_allows_benchmark_mode(tmp_path: Path, monkeypatch) -> None:
    real = _fake_real_popcorn(tmp_path)
    monkeypatch.setattr("shutil.which", lambda n: str(real))
    shim_dir = tmp_path / "bin"
    install_shim(shim_dir)
    proc = _run_shim(shim_dir, real, ["cand.py", "--leaderboard", "cholesky",
                                      "--gpu", "B200", "--mode", "benchmark"], gated=False)
    assert proc.returncode == 0 and "REAL popcorn ran" in proc.stdout


def test_shim_blocks_ungated_ranked_submit(tmp_path: Path, monkeypatch) -> None:
    real = _fake_real_popcorn(tmp_path)
    monkeypatch.setattr("shutil.which", lambda n: str(real))
    shim_dir = tmp_path / "bin"
    install_shim(shim_dir)
    # an AGENT trying a ranked leaderboard submit (no gated flag) is refused
    proc = _run_shim(shim_dir, real, ["cand.py", "--leaderboard", "cholesky",
                                      "--gpu", "B200", "--mode", "leaderboard"], gated=False)
    assert proc.returncode == 3 and "REFUSED by chi" in proc.stderr
    assert "REAL popcorn ran" not in proc.stdout
    # bare `submit` subcommand is also blocked
    proc2 = _run_shim(shim_dir, real, ["submit", "cand.py"], gated=False)
    assert proc2.returncode == 3


def test_shim_allows_gated_ranked_submit(tmp_path: Path, monkeypatch) -> None:
    real = _fake_real_popcorn(tmp_path)
    monkeypatch.setattr("shutil.which", lambda n: str(real))
    shim_dir = tmp_path / "bin"
    install_shim(shim_dir)
    # chi's own gated path (CHI_GATED_SUBMIT=1) is permitted through
    proc = _run_shim(shim_dir, real, ["cand.py", "--leaderboard", "cholesky",
                                      "--gpu", "B200", "--mode", "leaderboard"], gated=True)
    assert proc.returncode == 0 and "REAL popcorn ran" in proc.stdout


def test_agent_env_strips_gated_flag(tmp_path: Path) -> None:
    # even if the parent somehow had the flag, agents must never inherit it
    base = dict(os.environ, CHI_GATED_SUBMIT="1")
    env = agent_env(base, tmp_path / "bin", real_popcorn="/x/popcorn")
    assert "CHI_GATED_SUBMIT" not in env
    assert env["PATH"].startswith(str(tmp_path / "bin"))
    assert env["CHI_REAL_POPCORN"] == "/x/popcorn"
