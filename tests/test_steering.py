from pathlib import Path

from chi.orchestrator.steering import Steering
from chi.store.db import Store
from chi.store.events import list_events
from chi.store.ledger import add_negative, record_experiment


def test_refresh_creates_template_and_emits_update(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "run")
    steering = Steering(store, "r1")
    state = steering.refresh()
    assert (tmp_path / "run" / "steering.md").exists()
    assert "Operator directives" in state.text
    assert len(list_events(store, "r1", "STEER_UPDATE")) == 1


def test_no_duplicate_update_when_unchanged(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "run")
    steering = Steering(store, "r1")
    steering.refresh()
    steering.refresh()
    assert len(list_events(store, "r1", "STEER_UPDATE")) == 1


def test_operator_edit_detected_and_included(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "run")
    steering = Steering(store, "r1")
    first = steering.refresh()
    (tmp_path / "run" / "steering.md").write_text("## §1 Stop micro-tuning; try numpy\n")
    second = steering.refresh()
    assert second.operator_hash != first.operator_hash
    assert "Stop micro-tuning" in second.text
    assert len(list_events(store, "r1", "STEER_UPDATE")) == 2


def test_auto_digest_reflects_store(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "run")
    record_experiment(
        store, "r1", code_hash="sha256:abc", correct=True, seeds_passed=[1],
        score_value=12.5, noise_std=0.0, agent_id="a1", score_metric="runtime_ms",
    )
    add_negative(store, "r1", approach_class="threads", summary="GIL-bound",
                 evidence={}, authored_by="a1")
    state = Steering(store, "r1").refresh()
    assert "12.5" in state.text and "threads" in state.text
