"""Experiment registry (dedup cache) and the negative-results ledger."""

import json
import sqlite3
import uuid

from chi.store import events
from chi.store.db import Store, utcnow


def record_experiment(
    store: Store,
    run_id: str,
    *,
    code_hash: str,
    correct: bool,
    seeds_passed: list[int],
    score_value: float | None,
    noise_std: float | None,
    agent_id: str,
    task_id: str | None = None,
    eval_tier: str = "proxy",
    score_metric: str | None = None,
    strategy: str | None = None,
    parent_code_hash: str | None = None,
    artifacts_path: str | None = None,
    config_hash: str | None = None,
) -> bool:
    """Insert one experiment; returns False (no write) if code_hash already exists."""
    if get_experiment(store, code_hash) is not None:
        return False
    store.execute(
        "INSERT INTO experiments (code_hash, config_hash, run_id, task_id, strategy,"
        " author, eval_tier, correct, seeds_passed_json, score_metric, score_value,"
        " noise_std, parent_code_hash, artifacts_path, ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code_hash, config_hash, run_id, task_id, strategy, agent_id, eval_tier,
         int(correct), json.dumps(seeds_passed), score_metric, score_value,
         noise_std, parent_code_hash, artifacts_path, utcnow()),
    )
    _mirror(store, "experiments.jsonl", {
        "code_hash": code_hash, "run_id": run_id, "author": agent_id,
        "correct": correct, "score_value": score_value, "ts": utcnow(),
    })
    events.append_event(
        store, run_id, events.RESULT, agent_id=agent_id, task_id=task_id,
        payload={"code_hash": code_hash, "correct": correct, "score_value": score_value},
    )
    return True


def get_experiment(store: Store, code_hash: str) -> sqlite3.Row | None:
    """Fetch one experiment by code hash."""
    rows = store.query("SELECT * FROM experiments WHERE code_hash=?", (code_hash,))
    return rows[0] if rows else None


def champion(store: Store, run_id: str, direction: str = "minimize") -> sqlite3.Row | None:
    """Best correct experiment for the run, per score direction."""
    order = "ASC" if direction == "minimize" else "DESC"
    rows = store.query(
        f"SELECT * FROM experiments WHERE run_id=? AND correct=1 AND score_value IS NOT NULL"
        f" ORDER BY score_value {order} LIMIT 1",
        (run_id,),
    )
    return rows[0] if rows else None


def latest_experiment(store: Store, run_id: str, author: str) -> sqlite3.Row | None:
    """The most recently recorded experiment by one author in a run.

    The watchdog's loop-detection uses this — NOT the on-disk candidate file — to
    tell whether an agent is genuinely exploring. Coders revert candidate.py to
    the champion after a losing benchmark, so the file hash looks "unchanged"
    every iteration even when the agent evaluated a new, distinct candidate. The
    store is the source of truth for what was actually tried.
    """
    rows = store.query(
        "SELECT * FROM experiments WHERE run_id=? AND author=?"
        " ORDER BY ts DESC, rowid DESC LIMIT 1",
        (run_id, author),
    )
    return rows[0] if rows else None


def dead_classes(store: Store, run_id: str) -> dict:
    """approach_class -> number of times ruled out (any status)."""
    rows = store.query(
        "SELECT approach_class, COUNT(*) n FROM negative_ledger WHERE run_id=?"
        " GROUP BY approach_class", (run_id,))
    return {r["approach_class"]: r["n"] for r in rows}


def mark_near_miss(store: Store, run_id: str, code_hash: str, score_value: float) -> None:
    """Flag an experiment as a promising near-parity base (no schema change:
    recorded as a STATUS event the Strategist can promote to a new base)."""
    events.append_event(store, run_id, events.STATUS,
                        payload={"near_miss": code_hash, "score": score_value})


def list_near_misses(store: Store, run_id: str) -> list[dict]:
    """Near-miss bases recorded via mark_near_miss, newest first, de-duplicated."""
    rows = store.query(
        "SELECT payload_json FROM events WHERE run_id=? AND type=? ORDER BY event_id DESC",
        (run_id, events.STATUS))
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        payload = json.loads(r["payload_json"])
        code_hash = payload.get("near_miss")
        if code_hash and code_hash not in seen:
            seen.add(code_hash)
            out.append({"code_hash": code_hash, "score": payload.get("score")})
    return out


def add_negative(
    store: Store,
    run_id: str,
    *,
    approach_class: str,
    summary: str,
    evidence: dict,
    authored_by: str,
    ruled_out_scope: str = "",
    confidence: str = "medium",
    experiment_context: dict | None = None,
) -> str:
    """Record a ruled-out approach with evidence and scope; emits DEAD_END."""
    neg_id = f"n_{uuid.uuid4().hex[:8]}"
    store.execute(
        "INSERT INTO negative_ledger (neg_id, run_id, approach_class, summary,"
        " evidence_json, ruled_out_scope, confidence, experiment_context_json,"
        " authored_by, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (neg_id, run_id, approach_class, summary, json.dumps(evidence), ruled_out_scope,
         confidence, json.dumps(experiment_context or {}), authored_by, utcnow()),
    )
    _mirror(store, "negative_ledger.jsonl", {
        "neg_id": neg_id, "approach_class": approach_class, "summary": summary,
        "ruled_out_scope": ruled_out_scope, "authored_by": authored_by, "ts": utcnow(),
    })
    events.append_event(
        store, run_id, events.DEAD_END, agent_id=authored_by,
        payload={"neg_id": neg_id, "approach_class": approach_class, "summary": summary},
    )
    return neg_id


def list_negatives(store: Store, run_id: str, status: str = "active") -> list[sqlite3.Row]:
    """List negative-ledger entries by status."""
    return store.query(
        "SELECT * FROM negative_ledger WHERE run_id=? AND status=? ORDER BY ts", (run_id, status)
    )


def query_knowledge(store: Store, run_id: str, text: str) -> dict:
    """LIKE-match experiments (strategy) and negatives (approach_class/summary)."""
    like = f"%{text}%"
    exps = store.query(
        "SELECT * FROM experiments WHERE run_id=? AND strategy LIKE ?", (run_id, like)
    )
    negs = store.query(
        "SELECT * FROM negative_ledger WHERE run_id=? AND (approach_class LIKE ? OR summary"
        " LIKE ?)",
        (run_id, like, like),
    )
    return {"experiments": [dict(r) for r in exps], "negatives": [dict(r) for r in negs]}


def add_challenge(store: Store, run_id: str, *, neg_id: str, agent_id: str, hypothesis: str) -> str:
    """File a distinguishing hypothesis against a negative entry; marks it challenged."""
    challenge_id = f"ch_{uuid.uuid4().hex[:8]}"
    store.execute(
        "INSERT INTO challenges (challenge_id, neg_id, agent_id, distinguishing_hypothesis,"
        " ts) VALUES (?,?,?,?,?)",
        (challenge_id, neg_id, agent_id, hypothesis, utcnow()),
    )
    store.execute("UPDATE negative_ledger SET status='challenged' WHERE neg_id=?", (neg_id,))
    events.append_event(
        store, run_id, events.STATUS, agent_id=agent_id,
        payload={"challenge_id": challenge_id, "neg_id": neg_id, "hypothesis": hypothesis},
    )
    return challenge_id


def _mirror(store: Store, filename: str, record: dict) -> None:
    """Append one JSON line to a mirror file."""
    with open(store.run_dir / "mirror" / filename, "a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
