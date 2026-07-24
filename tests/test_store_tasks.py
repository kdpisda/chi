from datetime import datetime, timedelta, timezone
from pathlib import Path

from chi.store.db import Store
from chi.store.events import list_events
from chi.store.tasks import (
    claim_task, create_task, expire_stale_leases, get_task, release_task, set_status,
)


def _store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "run")


def test_create_and_claim(tmp_path: Path) -> None:
    s = _store(tmp_path)
    tid = create_task(s, "r1", spec={"goal": "opt"})
    got = claim_task(s, "r1", "agent-a", lease_seconds=60)
    assert got == tid
    row = get_task(s, tid)
    assert row["status"] == "claimed" and row["owner_id"] == "agent-a"
    assert [e["type"] for e in list_events(s, "r1", "CLAIM")] == ["CLAIM"]


def test_claim_is_exclusive(tmp_path: Path) -> None:
    s = _store(tmp_path)
    create_task(s, "r1")
    assert claim_task(s, "r1", "a", 60) is not None
    assert claim_task(s, "r1", "b", 60) is None  # nothing pending left


def test_claim_prefers_priority(tmp_path: Path) -> None:
    s = _store(tmp_path)
    create_task(s, "r1", priority=0)
    high = create_task(s, "r1", priority=5)
    assert claim_task(s, "r1", "a", 60) == high


def test_release_returns_to_pending(tmp_path: Path) -> None:
    s = _store(tmp_path)
    tid = create_task(s, "r1")
    claim_task(s, "r1", "a", 60)
    release_task(s, "r1", tid)
    assert get_task(s, tid)["status"] == "pending"
    assert get_task(s, tid)["owner_id"] is None


def test_expired_lease_returns_to_pending_and_bumps_attempts(tmp_path: Path) -> None:
    s = _store(tmp_path)
    tid = create_task(s, "r1")
    claim_task(s, "r1", "a", lease_seconds=60)
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    s.execute("UPDATE tasks SET lease_expires_at=? WHERE task_id=?", (past, tid))
    assert expire_stale_leases(s, "r1") == [tid]
    row = get_task(s, tid)
    assert row["status"] == "pending" and row["attempts"] == 1


def test_set_status(tmp_path: Path) -> None:
    s = _store(tmp_path)
    tid = create_task(s, "r1")
    set_status(s, tid, "verified")
    assert get_task(s, tid)["status"] == "verified"
