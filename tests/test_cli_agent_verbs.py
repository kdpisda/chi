import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from chi.cli import app
from chi.store.db import Store
from chi.store.ledger import list_negatives

runner = CliRunner()
PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"


def _mkrun(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run1"
    Store.open(run_dir).close()
    shutil.copytree(PROBLEM_DIR, run_dir / "workdir")
    return run_dir


def test_eval_verb_prints_result_json(tmp_path: Path) -> None:
    run_dir = _mkrun(tmp_path)
    result = runner.invoke(app, ["eval", "--run-dir", str(run_dir), "--agent", "a1"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["correct"] is True and payload["score_value"] > 0


def test_deadend_requires_evidence(tmp_path: Path) -> None:
    run_dir = _mkrun(tmp_path)
    result = runner.invoke(app, [
        "deadend", "--run-dir", str(run_dir), "--agent", "a1",
        "--approach-class", "x", "--summary", "vague vibes",
    ])
    assert result.exit_code == 1
    ok = runner.invoke(app, [
        "deadend", "--run-dir", str(run_dir), "--agent", "a1",
        "--approach-class", "x", "--summary", "measured regression",
        "--evidence-json", '{"delta_pct": 4.2}',
    ])
    assert ok.exit_code == 0
    store = Store.open(run_dir)
    assert len(list_negatives(store, "run1")) == 1


def test_query_verb_returns_knowledge(tmp_path: Path) -> None:
    run_dir = _mkrun(tmp_path)
    runner.invoke(app, ["eval", "--run-dir", str(run_dir), "--agent", "a1"])
    result = runner.invoke(app, ["query", "--run-dir", str(run_dir), "optimize"])
    assert result.exit_code == 0
    assert "experiments" in json.loads(result.output)


def test_task_claim_and_release(tmp_path: Path) -> None:
    run_dir = _mkrun(tmp_path)
    store = Store.open(run_dir)
    from chi.store.tasks import create_task

    tid = create_task(store, "run1")
    store.close()
    claim = runner.invoke(app, ["task", "claim", "--run-dir", str(run_dir), "--agent", "a1"])
    assert claim.exit_code == 0 and tid in claim.output
    rel = runner.invoke(app, ["task", "release", "--run-dir", str(run_dir), "--task-id", tid])
    assert rel.exit_code == 0
