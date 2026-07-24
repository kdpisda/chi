import shutil
import stat
from pathlib import Path

from chi.agents.cli_subprocess import CliSubprocessAdapter
from chi.agents.context import build_seed_context
from chi.config import PoliciesCfg, load_problem
from chi.orchestrator.steering import Steering
from chi.providers.budgets import BudgetTracker
from chi.store.db import Store
from chi.store.ledger import champion

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

FAKE_CLI = """#!/bin/bash
# Stand-in for a vendor CLI: reads the prompt path, improves the candidate,
# then records the result through the enforced chi verb, like a real agent would.
set -e
cat > candidate.py <<'EOF'
import itertools


def solve(xs: list[float]) -> list[float]:
    return list(itertools.accumulate(xs))
EOF
chi eval --run-dir "$CHI_RUN_DIR" --agent "$CHI_AGENT_ID"
"""


def test_cli_adapter_runs_fake_cli_and_counts_evals(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    store = Store.open(run_dir)
    shutil.copytree(PROBLEM_DIR, run_dir / "workdir")
    prob = load_problem(run_dir / "workdir")
    fake = tmp_path / "fake_coder.sh"
    fake.write_text(FAKE_CLI)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CHI_RUN_DIR", str(run_dir))
    monkeypatch.setenv("CHI_AGENT_ID", "a1")

    adapter = CliSubprocessAdapter(
        store=store, run_id="run", agent_id="a1", model="fake", workdir=run_dir / "workdir",
        problem=prob, budget=BudgetTracker(total_usd=1.0), policies=PoliciesCfg(),
        command=f"{fake} {{prompt_file}}",
    )
    state = Steering(store, "run").refresh()
    seed = build_seed_context(store, "run", prob, run_dir / "workdir", state, 0, None)
    out = adapter.run_iteration(seed)
    assert out.evals_run == 1
    assert (run_dir / "workdir" / ".chi" / "prompt.md").exists()
    assert champion(store, "run") is not None


def test_heartbeat_thread_beats_during_long_cli_run(tmp_path: Path) -> None:
    # Regression: heartbeats fire from a thread; the store connection must be
    # usable across threads (first real claude run crashed here).
    run_dir = tmp_path / "run"
    store = Store.open(run_dir)
    shutil.copytree(PROBLEM_DIR, run_dir / "workdir")
    prob = load_problem(run_dir / "workdir")
    store.execute(
        "INSERT INTO agents (agent_id, run_id, adapter, model, started_at)"
        " VALUES ('a1','run','cli_subprocess','m','2026-01-01T00:00:00Z')")
    slow = tmp_path / "slowish.sh"
    slow.write_text("#!/bin/bash\nsleep 2.5\n")
    slow.chmod(slow.stat().st_mode | stat.S_IEXEC)
    policies = PoliciesCfg(heartbeat_seconds=1, iteration_timeout_seconds=10)
    adapter = CliSubprocessAdapter(
        store=store, run_id="run", agent_id="a1", model="fake", workdir=run_dir / "workdir",
        problem=prob, budget=BudgetTracker(total_usd=1.0), policies=policies,
        command=f"{slow} {{prompt_file}}",
    )
    state = Steering(store, "run").refresh()
    seed = build_seed_context(store, "run", prob, run_dir / "workdir", state, 0, None)
    adapter.run_iteration(seed)
    from chi.store.events import list_events

    beats = list_events(store, "run", "HEARTBEAT")
    assert len(beats) >= 3  # initial beat + at least two thread beats


def test_cli_adapter_timeout_is_survivable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    store = Store.open(run_dir)
    shutil.copytree(PROBLEM_DIR, run_dir / "workdir")
    prob = load_problem(run_dir / "workdir")
    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/bash\nsleep 30\n")
    slow.chmod(slow.stat().st_mode | stat.S_IEXEC)
    policies = PoliciesCfg(iteration_timeout_seconds=1)
    adapter = CliSubprocessAdapter(
        store=store, run_id="run", agent_id="a1", model="fake", workdir=run_dir / "workdir",
        problem=prob, budget=BudgetTracker(total_usd=1.0), policies=policies,
        command=f"{slow} {{prompt_file}}",
    )
    state = Steering(store, "run").refresh()
    seed = build_seed_context(store, "run", prob, run_dir / "workdir", state, 0, None)
    out = adapter.run_iteration(seed)
    assert out.note == "timeout" and out.evals_run == 0
