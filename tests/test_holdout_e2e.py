"""End-to-end: a real run crowns a benchmark-shaped champion and the holdout catches it.

This is the failure mode the gate exists for, reproduced honestly rather than
mocked: OVERFIT below is a correct program that is genuinely faster on the
problem's benchmark and genuinely not faster at anything else.
"""

import json
from pathlib import Path

from chi.config import FleetConfig
from chi.eval.holdout import GENERALIZES, OVERFIT, REGRESSED
from chi.orchestrator.loop import start_run
from chi.store.db import Store
from chi.store.events import HOLDOUT, list_events

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

# Correct everywhere, but only fast on bench.py's fixed 4000-element input — the
# one workload the fleet's score comes from. Every other size falls back to the
# O(n^2) baseline. A harness that scores one frozen benchmark calls this a win.
OVERFIT_SRC = (
    "import itertools\n\n\n"
    "def solve(xs: list[float]) -> list[float]:\n"
    "    if len(xs) == 4000:\n"
    "        return list(itertools.accumulate(xs))\n"
    "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n"
)
# The same speedup, applied to the actual problem instead of the benchmark.
HONEST_SRC = (
    "import itertools\n\n\n"
    "def solve(xs: list[float]) -> list[float]:\n"
    "    return list(itertools.accumulate(xs))\n"
)


def _run(tmp_path: Path, source: str, name: str):
    script = tmp_path / f"{name}.json"
    script.write_text(json.dumps([source]))
    fleet = FleetConfig.model_validate({
        "run_name": name,
        "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                    "script": str(script)}],
        "policies": {"max_iterations": 1},
    })
    return start_run(fleet, runs_root=tmp_path / f"runs-{name}")


def test_benchmark_shaped_champion_is_flagged_overfit(tmp_path: Path) -> None:
    summary = _run(tmp_path, OVERFIT_SRC, "overfit")

    # it really did win the benchmark — this is not a broken candidate
    assert summary.champion_score is not None
    assert summary.champion_score < summary.baseline_score

    verdict = summary.holdout
    assert verdict is not None, "a problem with a holdout must produce a verdict"
    # the contract that matters: the gate withholds it. Which of the two
    # non-shippable labels fires depends on whether the flat held-out result
    # lands a hair above or below the baseline, which is measurement noise on a
    # shared runner — test_holdout_gate.py pins the labelling against synthetic
    # scores, where it is deterministic.
    assert verdict.shippable is False, verdict.detail
    assert verdict.verdict in (OVERFIT, REGRESSED), verdict.detail
    # it claimed a large benchmark gain and realised essentially none of it
    assert verdict.claimed_gain_pct > 50
    assert verdict.generalization < 0.5


def test_real_speedup_passes_the_holdout(tmp_path: Path) -> None:
    summary = _run(tmp_path, HONEST_SRC, "honest")

    verdict = summary.holdout
    assert verdict is not None
    assert verdict.verdict == GENERALIZES, verdict.detail
    assert verdict.shippable is True
    assert verdict.holdout_gain_pct > 50


def test_holdout_files_never_reach_the_agent_workdir(tmp_path: Path) -> None:
    summary = _run(tmp_path, HONEST_SRC, "privacy")

    workdir = summary.run_dir / "workdir"
    assert (workdir / "bench.py").exists()          # the optimised benchmark: visible
    assert not (workdir / "holdout_bench.py").exists()  # the held-out one: never
    # chi keeps its own full copy, outside every agent workdir
    assert (summary.run_dir / "holdout" / "holdout_bench.py").exists()


def test_baseline_and_champion_holdouts_are_both_recorded(tmp_path: Path) -> None:
    summary = _run(tmp_path, OVERFIT_SRC, "events")
    store = Store.open(summary.run_dir)
    payloads = [json.loads(r["payload_json"])
                for r in list_events(store, summary.run_id, HOLDOUT)]

    phases = [p["phase"] for p in payloads]
    assert phases == ["baseline", "champion"]
    assert payloads[0]["score"] > 0  # measured on the untouched candidate
    assert payloads[1]["verdict"] in (OVERFIT, REGRESSED)  # and compared against it


def test_problem_without_a_holdout_still_runs(tmp_path: Path) -> None:
    """The gate is opt-in: an existing problem pack behaves exactly as before."""
    plain = tmp_path / "plain_problem"
    plain.mkdir()
    for f in ("bench.py", "check.py", "candidate.py", "reference.py"):
        (plain / f).write_bytes((PROBLEM_DIR / f).read_bytes())
    manifest = (PROBLEM_DIR / "problem.yaml").read_text()
    manifest = manifest[:manifest.index("holdout:")] + "timeout_seconds: 60\n"
    (plain / "problem.yaml").write_text(manifest)

    script = tmp_path / "plain.json"
    script.write_text(json.dumps([HONEST_SRC]))
    fleet = FleetConfig.model_validate({
        "run_name": "plain", "problem": str(plain),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                    "script": str(script)}],
        "policies": {"max_iterations": 1},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs-plain")
    assert summary.holdout is None
    assert summary.champion_score < summary.baseline_score
    assert not (summary.run_dir / "holdout").exists()


def test_cli_champion_reports_the_verdict_and_warns_on_export(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from chi.cli import app

    summary = _run(tmp_path, OVERFIT_SRC, "cli")
    out = tmp_path / "best.py"
    result = CliRunner().invoke(app, ["champion", str(summary.run_dir),
                                      "--export", str(out)])
    assert result.exit_code == 0, result.output
    verdict = json.loads(result.stdout.splitlines()[0])["holdout"]["verdict"]
    assert verdict in (OVERFIT, REGRESSED)
    # the verdict is evidence, not a veto: it warns loudly and still exports
    assert f"holdout {verdict}" in result.output
    assert out.read_text() == OVERFIT_SRC


def test_director_slices_do_not_pay_for_the_holdout_twice(tmp_path: Path) -> None:
    """run_slice leaves the holdout to the director, which gates noise-verified wins.

    Gating at every slice boundary too would double the benchmark cost and record
    a second, possibly disagreeing verdict for the same champion.
    """
    from chi.orchestrator.loop import run_slice

    script = tmp_path / "slice.json"
    script.write_text(json.dumps([HONEST_SRC]))
    fleet = FleetConfig.model_validate({
        "run_name": "slice", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                    "script": str(script)}],
        "policies": {"max_iterations": 1},
    })
    first = start_run(fleet, runs_root=tmp_path / "runs-slice")
    store = Store.open(first.run_dir)

    def phases():
        return [json.loads(r["payload_json"])["phase"]
                for r in list_events(store, first.run_id, HOLDOUT)]

    assert phases() == ["baseline", "champion"]   # the standalone run gates
    sliced = run_slice(fleet, first.run_dir, iterations=1)
    assert sliced.holdout is None                 # ...a director slice does not
    assert phases() == ["baseline", "champion"]   # and records nothing new
