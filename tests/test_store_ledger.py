from pathlib import Path

from chi.store.db import Store
from chi.store.events import list_events
from chi.store.ledger import (
    add_challenge, add_negative, champion, get_experiment, list_negatives,
    query_knowledge, record_experiment,
)


def _store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "run")


def _exp(s: Store, h: str, score: float, correct: bool = True) -> bool:
    return record_experiment(
        s, "r1", code_hash=h, correct=correct, seeds_passed=[11, 27],
        score_value=score, noise_std=0.1, agent_id="a1", score_metric="runtime_ms",
    )


def test_record_and_dedup(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert _exp(s, "sha256:aaa", 10.0) is True
    assert _exp(s, "sha256:aaa", 9.0) is False  # duplicate ignored
    assert get_experiment(s, "sha256:aaa")["score_value"] == 10.0
    assert len(list_events(s, "r1", "RESULT")) == 1


def test_champion_minimize_ignores_incorrect(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _exp(s, "sha256:a", 10.0)
    _exp(s, "sha256:b", 5.0)
    _exp(s, "sha256:c", 1.0, correct=False)
    assert champion(s, "r1", "minimize")["code_hash"] == "sha256:b"


def test_champion_maximize(tmp_path: Path) -> None:
    s = _store(tmp_path)
    _exp(s, "sha256:a", 10.0)
    _exp(s, "sha256:b", 50.0)
    assert champion(s, "r1", "maximize")["code_hash"] == "sha256:b"


def test_negative_ledger_and_challenge(tmp_path: Path) -> None:
    s = _store(tmp_path)
    neg = add_negative(
        s, "r1", approach_class="precision_fp16", summary="fp16 panel fails tolerance",
        evidence={"max_abs_error": 0.03, "seed": 27}, authored_by="verifier",
        ruled_out_scope="n<=32",
    )
    assert list_negatives(s, "r1")[0]["approach_class"] == "precision_fp16"
    assert len(list_events(s, "r1", "DEAD_END")) == 1
    add_challenge(s, "r1", neg_id=neg, agent_id="a2", hypothesis="different regime: n=512")
    assert list_negatives(s, "r1", status="challenged")[0]["neg_id"] == neg


def test_query_knowledge_matches_both(tmp_path: Path) -> None:
    s = _store(tmp_path)
    record_experiment(
        s, "r1", code_hash="sha256:x", correct=True, seeds_passed=[1], score_value=2.0,
        noise_std=0.0, agent_id="a1", strategy="vectorize_numpy",
    )
    add_negative(
        s, "r1", approach_class="vectorize_gpu", summary="no gpu available",
        evidence={}, authored_by="a1",
    )
    out = query_knowledge(s, "r1", "vectorize")
    assert len(out["experiments"]) == 1 and len(out["negatives"]) == 1
