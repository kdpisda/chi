"""Append-only typed event log with a JSONL mirror."""

import json

from chi.store.db import Store, utcnow

ITERATION_START = "ITERATION_START"
ITERATION_COMPLETE = "ITERATION_COMPLETE"
RESULT = "RESULT"
DEAD_END = "DEAD_END"
STATUS = "STATUS"
HEARTBEAT = "HEARTBEAT"
CLAIM = "CLAIM"
RELEASE = "RELEASE"
BREAKTHROUGH = "BREAKTHROUGH"
STOP = "STOP"
WATCHDOG_KILL = "WATCHDOG_KILL"
BUDGET_BLOCK = "BUDGET_BLOCK"
STEER_UPDATE = "STEER_UPDATE"
STEER_ACK = "STEER_ACK"


def append_event(
    store: Store,
    run_id: str,
    type_: str,
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
    payload: dict | None = None,
    cost_usd: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> int:
    """Append one event to the db and the events.jsonl mirror; returns event_id."""
    ts = utcnow()
    payload_json = json.dumps(payload or {}, sort_keys=True)
    cur = store.execute(
        "INSERT INTO events (run_id, ts, agent_id, type, task_id, payload_json,"
        " cost_usd, tokens_in, tokens_out) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, ts, agent_id, type_, task_id, payload_json, cost_usd, tokens_in, tokens_out),
    )
    line = json.dumps(
        {"event_id": cur.lastrowid, "run_id": run_id, "ts": ts, "agent_id": agent_id,
         "type": type_, "task_id": task_id, "payload": payload or {},
         "cost_usd": cost_usd, "tokens_in": tokens_in, "tokens_out": tokens_out},
        sort_keys=True,
    )
    with open(store.run_dir / "mirror" / "events.jsonl", "a") as f:
        f.write(line + "\n")
    return int(cur.lastrowid)


def list_events(store: Store, run_id: str, type_: str | None = None) -> list:
    """List events for a run, optionally filtered by type, in insertion order."""
    if type_ is None:
        return store.query("SELECT * FROM events WHERE run_id=? ORDER BY event_id", (run_id,))
    return store.query(
        "SELECT * FROM events WHERE run_id=? AND type=? ORDER BY event_id", (run_id, type_)
    )
