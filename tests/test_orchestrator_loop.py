import json
from pathlib import Path

from chi.config import FleetConfig
from chi.orchestrator.loop import start_run
from chi.store.db import Store
from chi.store.events import list_events

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")
NAIVE = ("def solve(xs: list[float]) -> list[float]:\n"
         "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


def _fleet(tmp_path: Path, script: list[str], max_iterations: int = 3) -> FleetConfig:
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script))
    return FleetConfig.model_validate({
        "run_name": "t",
        "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script_path)}],
        "policies": {"max_iterations": max_iterations, "eval_recency_iters": 6,
                      "repeat_k": 3},
    })


def test_scripted_run_improves_champion(tmp_path: Path) -> None:
    summary = start_run(_fleet(tmp_path, [NAIVE, GOOD]), runs_root=tmp_path / "runs")
    assert summary.status == "done"
    assert summary.baseline_score is not None
    assert summary.champion_score < summary.baseline_score
    store = Store.open(summary.run_dir)
    assert len(list_events(store, summary.run_id, "ITERATION_START")) == 3
    assert len(list_events(store, summary.run_id, "STEER_UPDATE")) >= 1


def test_watchdog_kills_unchanging_agent(tmp_path: Path) -> None:
    # Script always replays the same (baseline) candidate: eval dedup means
    # 0 new evals per iteration -> eval-recency kill fires.
    fleet = _fleet(tmp_path, [NAIVE], max_iterations=20)
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    assert summary.status == "stalled"
    store = Store.open(summary.run_dir)
    kills = list_events(store, summary.run_id, "WATCHDOG_KILL")
    assert len(kills) == 1
    # the task went back to pending on kill
    row = store.query("SELECT status FROM tasks")[0]
    assert row["status"] == "pending"


def test_watchdog_keeps_agent_that_evaluates_distinct_candidates(tmp_path: Path, monkeypatch) -> None:
    # Regression: a real coder reverts candidate.py to the champion after a
    # losing benchmark, so the on-disk file hash is constant every iteration —
    # even though the agent evaluated a NEW distinct candidate each round. The
    # watchdog must read the evaluated candidate (from the store), not the file,
    # or it falsely reaps a productive agent as "candidate unchanged N iterations".
    from chi.agents.protocol import IterationOutcome
    from chi.agents.scripted import ScriptedAdapter
    from chi.eval.runner import evaluate

    def reverting_run_iteration(self, seed) -> IterationOutcome:
        self.ack_steering(seed.steering_hash)
        self.heartbeat()
        cand = self.workdir / self.problem.candidate
        seed_src = cand.read_text()
        # a distinct-but-correct candidate each iteration (new code hash; a
        # trailing comment changes the hash without beating the baseline)
        cand.write_text(seed_src + f"\n# distinct attempt {seed.iteration}\n")
        evaluate(self.problem, self.workdir, store=self.store, run_id=self.run_id,
                 agent_id=self.agent_id, strategy=seed.strategy)
        cand.write_text(seed_src)  # coder reverts the losing attempt to the seed
        return IterationOutcome(evals_run=1)

    monkeypatch.setattr(ScriptedAdapter, "run_iteration", reverting_run_iteration)
    script_path = tmp_path / "s.json"
    script_path.write_text(json.dumps([NAIVE]))  # unused by the patched method

    fleet = FleetConfig.model_validate({
        "run_name": "t",
        "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script_path)}],
        # eval-recency disabled so ONLY the repeat-hash rule can act; 8 rounds is
        # past the old 2*repeat_k=6 kill threshold.
        "policies": {"max_iterations": 8, "eval_recency_iters": 100, "repeat_k": 3},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(summary.run_dir)
    kills = list_events(store, summary.run_id, "WATCHDOG_KILL")
    assert kills == [], "productive agent (8 distinct evaluated candidates) was falsely killed"
    assert summary.status == "done"
    # it really did evaluate distinct candidates every iteration
    distinct = store.query(
        "SELECT COUNT(DISTINCT code_hash) n FROM experiments WHERE author='c1'")[0]["n"]
    assert distinct >= 8


def test_zero_eval_timeout_steers_next_iteration_to_smaller_edit(
        tmp_path: Path, monkeypatch) -> None:
    # Observed live: a coder editing a 97KB kernel lost whole iterations to the
    # 600s timeout with 0 evals, then repeated the same doomed large edit. The
    # very next seed must carry a mutation_note steering it to a smaller edit.
    from chi.agents.protocol import IterationOutcome
    from chi.agents.scripted import ScriptedAdapter
    from chi.eval.runner import evaluate

    seed_notes: list[str] = []

    def timeout_then_eval(self, seed) -> IterationOutcome:
        self.ack_steering(seed.steering_hash)
        self.heartbeat()
        seed_notes.append(seed.mutation_note)
        if seed.iteration == 0:
            return IterationOutcome(evals_run=0, note="timeout")
        (self.workdir / self.problem.candidate).write_text(GOOD)
        evaluate(self.problem, self.workdir, store=self.store, run_id=self.run_id,
                 agent_id=self.agent_id, strategy=seed.strategy)
        return IterationOutcome(evals_run=1)

    monkeypatch.setattr(ScriptedAdapter, "run_iteration", timeout_then_eval)
    summary = start_run(_fleet(tmp_path, [NAIVE], max_iterations=2),
                        runs_root=tmp_path / "runs")
    assert summary.status == "done"
    assert seed_notes[0] == ""  # iteration 0 seeded clean
    note = seed_notes[1].lower()
    assert "timed out" in note and "smallest" in note


def test_agent_acked_steering(tmp_path: Path) -> None:
    summary = start_run(_fleet(tmp_path, [NAIVE, GOOD]), runs_root=tmp_path / "runs")
    steering_path = summary.run_dir / "steering.md"
    assert steering_path.exists()
    store = Store.open(summary.run_dir)
    acks = list_events(store, summary.run_id, "STEER_ACK")
    assert len(acks) >= 1  # agent acknowledged the steering version it ran under


def test_steer_command_appends(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from chi.cli import app

    summary = start_run(_fleet(tmp_path, [NAIVE, GOOD]), runs_root=tmp_path / "runs")
    r = CliRunner().invoke(app, ["steer", str(summary.run_dir), "try numpy"])
    assert r.exit_code == 0
    assert "try numpy" in (summary.run_dir / "steering.md").read_text()
