"""One benchmark sample from a score-printing command.

Both the NoiseGuard (re-benchmark an apparent winner) and the HoldoutGate
(re-benchmark the champion on a workload it never optimised against) need the
same primitive: run a command in a directory, parse the trailing
`{"score": <float>}` line, and refuse a measurement that can't be true. Keeping
it in one place means the anti-gaming rule — a runtime must be finite and
strictly positive — is enforced identically everywhere a score enters chi.
"""

import json
import math
import shlex
import subprocess
import sys
from pathlib import Path

from chi.eval.popcorn import BenchResult


def sample_score(cmd_template: str, workdir: Path, candidate_name: str,
                 timeout_seconds: int) -> BenchResult:
    """Run one scoring command in workdir and parse its trailing score line."""
    cmd = cmd_template.format(candidate=candidate_name, python=sys.executable)
    try:
        proc = subprocess.run(shlex.split(cmd), cwd=workdir, capture_output=True,
                              text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return BenchResult(False, None, "benchmark timed out")
    if proc.returncode != 0:
        out = ((proc.stdout or "") + " " + (proc.stderr or "")).strip()
        return BenchResult(False, None, f"benchmark failed: {out[:300]}")
    try:
        score = float(json.loads(proc.stdout.strip().splitlines()[-1])["score"])
    except (IndexError, KeyError, TypeError, ValueError):
        return BenchResult(False, None,
                           f"no score parsed from: {proc.stdout.strip()[:300]}")
    # a frozen/negative/inf "runtime" is gaming or breakage, not speed
    if not math.isfinite(score) or score <= 0:
        return BenchResult(False, None,
                           f"invalid score {score!r}: must be finite and > 0")
    return BenchResult(True, score, "ok")
