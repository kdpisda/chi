"""Task ledger: state machine with atomic claim/lease semantics."""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from chi.store import events
from chi.store.db import Store, utcnow


def _lease_ts(lease_seconds: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def create_task(
    store: Store,
    run_id: str,
    *,
    spec: dict | None = None,
    strategy_hash: str | None = None,
    priority: int = 0,
) -> str:
    """Create a pending task; returns its id."""
    task_id = f"t_{uuid.uuid4().hex[:8]}"
    now = utcnow()
    store.execute(
        "INSERT INTO tasks (task_id, run_id, strategy_hash, priority, spec_json,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (task_id, run_id, strategy_hash, priority, json.dumps(spec or {}), now, now),
    )
    return task_id


def claim_task(store: Store, run_id: str, agent_id: str, lease_seconds: int) -> str | None:
    """Atomically claim the highest-priority pending task; None if none available."""
    # RETURNING cursors must be fully consumed before commit, so run this
    # against the raw connection and commit after the fetch.
    cur = store.conn.execute(
        "UPDATE tasks SET status='claimed', owner_id=?, lease_expires_at=?, updated_at=?"
        " WHERE task_id = (SELECT task_id FROM tasks WHERE run_id=? AND status='pending'"
        "                  ORDER BY priority DESC, created_at ASC LIMIT 1)"
        " AND status='pending' RETURNING task_id",
        (agent_id, _lease_ts(lease_seconds), utcnow(), run_id),
    )
    row = cur.fetchone()
    store.conn.commit()
    if row is None:
        return None
    events.append_event(store, run_id, events.CLAIM, agent_id=agent_id, task_id=row["task_id"])
    return str(row["task_id"])


def release_task(store: Store, run_id: str, task_id: str, status: str = "pending") -> None:
    """Release a task back to the pool (or to a terminal status)."""
    store.execute(
        "UPDATE tasks SET status=?, owner_id=NULL, lease_expires_at=NULL, updated_at=?"
        " WHERE task_id=?",
        (status, utcnow(), task_id),
    )
    events.append_event(store, run_id, events.RELEASE, task_id=task_id, payload={"to": status})


def renew_lease(store: Store, task_id: str, lease_seconds: int) -> None:
    """Extend the lease of a claimed/running task."""
    store.execute(
        "UPDATE tasks SET lease_expires_at=?, updated_at=? WHERE task_id=?",
        (_lease_ts(lease_seconds), utcnow(), task_id),
    )


def expire_stale_leases(store: Store, run_id: str) -> list[str]:
    """Return expired claimed/running tasks to pending; bump attempts."""
    now = utcnow()
    rows = store.query(
        "SELECT task_id FROM tasks WHERE run_id=? AND status IN ('claimed','running')"
        " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
        (run_id, now),
    )
    expired = [r["task_id"] for r in rows]
    for task_id in expired:
        store.execute(
            "UPDATE tasks SET status='pending', owner_id=NULL, lease_expires_at=NULL,"
            " attempts=attempts+1, updated_at=? WHERE task_id=?",
            (now, task_id),
        )
    return expired


def set_status(store: Store, task_id: str, status: str) -> None:
    """Set a task's status."""
    store.execute(
        "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?", (status, utcnow(), task_id)
    )


def get_task(store: Store, task_id: str) -> sqlite3.Row | None:
    """Fetch one task row."""
    rows = store.query("SELECT * FROM tasks WHERE task_id=?", (task_id,))
    return rows[0] if rows else None
