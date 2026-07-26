"""A popcorn-cli shim that lets fleet agents benchmark but not rank-submit.

Coder agents have a shell, so they can call popcorn-cli directly — which would
bypass chi's submission gate (mutex + ration + approval) entirely. The dogfood
showed exactly this: agents fired their own popcorn benchmarks. Benchmarking is
fine and expected (it's their feedback loop), but an ungated RANKED submission
could spam the rate limit or drop the user's rank.

The shim intercepts by being first on the agents' PATH. It forwards test/
benchmark/profile calls to the real popcorn-cli, but refuses `--mode leaderboard`
(and bare `submit`) unless CHI_GATED_SUBMIT=1 is set — which only chi's own
controlled submit path sets. So ranked submission can only happen through chi.
"""

import os
import shutil
import stat
from pathlib import Path

SHIM_SOURCE = r'''#!/usr/bin/env python3
"""chi popcorn-cli shim — see chi/eval/popcorn_shim.py. Blocks ungated ranked submits."""
import os
import sys

REAL = os.environ.get("CHI_REAL_POPCORN", "")
args = sys.argv[1:]


def _is_ranked_submit(argv):
    # `--mode leaderboard` anywhere, or the bare `submit` subcommand
    for i, a in enumerate(argv):
        if a == "--mode" and i + 1 < len(argv) and argv[i + 1] == "leaderboard":
            return True
        if a.startswith("--mode=") and a.split("=", 1)[1] == "leaderboard":
            return True
    if argv and argv[0] == "submit":
        return True
    return False


if _is_ranked_submit(args) and os.environ.get("CHI_GATED_SUBMIT") != "1":
    sys.stderr.write(
        "REFUSED by chi: ranked leaderboard submission must go through chi's gate "
        "(mutex + ration + approval). Use --mode benchmark for feedback; chi submits "
        "real improvements itself.\n")
    sys.exit(3)

if not REAL or not os.path.exists(REAL):
    sys.stderr.write("chi shim: real popcorn-cli not found (CHI_REAL_POPCORN unset)\n")
    sys.exit(1)
os.execv(REAL, [REAL] + args)
'''


def install_shim(bin_dir: Path) -> Path | None:
    """Write a popcorn-cli shim into bin_dir; returns its path (None if no real cli)."""
    real = shutil.which("popcorn-cli") or shutil.which("popcorn")
    if real is None:
        return None
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "popcorn-cli"
    shim.write_text(SHIM_SOURCE)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def agent_env(base_env: dict, bin_dir: Path, real_popcorn: str | None = None) -> dict:
    """Env for an agent subprocess: shim first on PATH, real popcorn recorded,
    and the gated-submit flag explicitly OFF so agents can never rank-submit."""
    real = real_popcorn or shutil.which("popcorn-cli") or shutil.which("popcorn") or ""
    env = dict(base_env)
    env["PATH"] = f"{Path(bin_dir)}{os.pathsep}{env.get('PATH', '')}"
    env["CHI_REAL_POPCORN"] = real
    env.pop("CHI_GATED_SUBMIT", None)  # agents never carry the gated flag
    return env
