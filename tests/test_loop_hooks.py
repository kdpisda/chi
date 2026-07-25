import json
import threading
from pathlib import Path

from chi.config import FleetConfig
from chi.orchestrator.loop import start_run
from chi.store.db import Store
from chi.store.events import list_events

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

NAIVE = ("def solve(xs: list[float]) -> list[float]:\n"
         "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


def _fleet(tmp_path: Path, max_iterations: int = 2) -> FleetConfig:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([NAIVE]))
    return FleetConfig.model_validate({
        "run_name": "hooks", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script)}],
        "policies": {"max_iterations": max_iterations},
    })


def test_on_run_created_fires_before_return(tmp_path: Path) -> None:
    order: list[str] = []

    def on_created(run_id: str, run_dir: Path) -> None:
        order.append(f"created:{run_id}")
        assert run_dir.exists()

    summary = start_run(_fleet(tmp_path), runs_root=tmp_path / "runs",
                        on_run_created=on_created)
    order.append("returned")
    assert order[0].startswith("created:hooks-") and order[1] == "returned"
    assert order[0] == f"created:{summary.run_id}"


def test_stop_event_ends_run_as_stopped(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()  # pre-set: run should stop before iteration 0
    summary = start_run(_fleet(tmp_path, max_iterations=5), runs_root=tmp_path / "runs",
                        stop_event=stop)
    assert summary.status == "stopped" and summary.iterations == 0
    store = Store.open(summary.run_dir)
    stops = list_events(store, summary.run_id, "STOP")
    assert any(json.loads(e["payload_json"]).get("reason") == "operator" for e in stops)
    assert store.query("SELECT status FROM tasks")[0]["status"] == "pending"
