"""popcorn-cli backend: parallel B200 benchmarking, gated leaderboard submission.

Benchmark = proxy tier: `popcorn-cli` in test/benchmark mode returns a time
without touching your rank; every fleet agent can run it concurrently.
Submit = authoritative tier: a ranked leaderboard submission, forced one-at-a-time
through a SubmissionGate (mutex + rate limit). Real submissions stay behind an
explicit operator approval — chi never spends your submission budget on its own.
"""

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chi.eval.submission import SubmissionGate, gate_for


@dataclass
class BenchResult:
    ok: bool
    score_us: float | None  # geomean latency in microseconds
    detail: str


@dataclass
class SubmitResult:
    ok: bool
    detail: str
    rationed: bool = False


def _parse_score(text: str) -> float | None:
    """Pull a microsecond geomean out of popcorn-cli output (JSON or text)."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        try:
            obj = json.loads(line)
            for key in ("geomean_us", "score_us", "score", "time_us", "us"):
                if key in obj:
                    return float(obj[key])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    match = re.search(r"([\d.]+)\s*(?:us|µs|microseconds)\b", text, re.I)
    return float(match.group(1)) if match else None


class PopcornBackend:
    """Runs popcorn-cli for benchmarking (parallel) and submission (gated)."""

    def __init__(
        self,
        leaderboard: str,
        benchmark_cmd: str,
        submit_cmd: str,
        gate: SubmissionGate | None = None,
        runner: Callable[[list[str], Path], subprocess.CompletedProcess] | None = None,
        timeout_seconds: int = 1800,
    ) -> None:
        self.leaderboard = leaderboard
        self.benchmark_cmd = benchmark_cmd  # template with {candidate}
        self.submit_cmd = submit_cmd
        self.gate = gate or gate_for(leaderboard)
        self._runner = runner or self._default_runner
        self.timeout_seconds = timeout_seconds

    def _default_runner(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=self.timeout_seconds)

    def _gated_runner(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
        """Run popcorn with CHI_GATED_SUBMIT=1 so the shim permits ranked submit."""
        import os

        env = dict(os.environ)
        env["CHI_GATED_SUBMIT"] = "1"
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=self.timeout_seconds, env=env)

    def benchmark(self, candidate: Path) -> BenchResult:
        """Benchmark a candidate on B200 via popcorn — parallel-safe, ungated."""
        candidate = Path(candidate)
        argv = shlex.split(self.benchmark_cmd.format(candidate=candidate.name))
        try:
            proc = self._runner(argv, candidate.parent)
        except subprocess.TimeoutExpired:
            return BenchResult(False, None, "benchmark timed out")
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0:
            return BenchResult(False, None, f"popcorn benchmark failed: {out.strip()[:300]}")
        score = _parse_score(out)
        if score is None:
            return BenchResult(False, None, f"no score parsed from: {out.strip()[:300]}")
        return BenchResult(True, score, "ok")

    def submit(self, candidate: Path, *, wait: bool = False) -> SubmitResult:
        """Submit to the leaderboard — serialized (mutex) and rationed via the gate."""
        from chi.eval.submission import RationDenied

        candidate = Path(candidate)
        argv = shlex.split(self.submit_cmd.format(candidate=candidate.name))

        # chi's own submit is the only gated path — it carries CHI_GATED_SUBMIT=1
        runner = self._gated_runner if self._runner is self._default_runner else self._runner

        def _do() -> SubmitResult:
            try:
                proc = runner(argv, candidate.parent)
            except subprocess.TimeoutExpired:
                return SubmitResult(False, "submission timed out")
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if proc.returncode != 0:
                return SubmitResult(False, f"submission failed: {out.strip()[:300]}")
            return SubmitResult(True, out.strip()[:300])

        try:
            return self.gate.submit(_do, wait=wait)
        except RationDenied as denied:
            return SubmitResult(False, str(denied), rationed=True)
