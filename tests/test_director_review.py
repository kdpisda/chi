import json
from pathlib import Path

from chi.config import FleetConfig
from chi.director.review import build_digest, classify_state
from chi.director.types import DirectorState, RoundDigest, RoundResult, StrategyUpdate
from chi.orchestrator.loop import start_run
from chi.store import ledger
from chi.store.db import Store

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
GOOD = "import itertools\n\n\ndef solve(xs):\n    return list(itertools.accumulate(xs))\n"
NAIVE = "def solve(xs):\n    return [sum(xs[: i + 1]) for i in range(len(xs))]\n"


def test_types_construct_and_state_enum_values():
    r = RoundResult(round_index=0, new_experiments=[], best_score=None,
                    benchmarks_run=0, cost_usd=0.0)
    assert r.round_index == 0
    d = RoundDigest(round_index=0, best_score=636.0, champion_score=636.0,
                    prev_best=None, dead_classes=[], repeated_dead_classes=[],
                    near_misses=[], distinct_new_classes=0)
    assert d.best_score == 636.0
    assert DirectorState.STUCK.value == "stuck"
    u = StrategyUpdate(steering_text="x", per_coder_strategy={"c1": "s"},
                       new_dead_classes=[], promoted_near_misses=[], researched=False)
    assert u.per_coder_strategy["c1"] == "s"


def _digest(best, prev, dead=None, repeated=None, distinct=0):
    return RoundDigest(round_index=0, best_score=best, champion_score=best,
                       prev_best=prev, dead_classes=dead or [],
                       repeated_dead_classes=repeated or [], near_misses=[],
                       distinct_new_classes=distinct)


def test_classify_improving_beyond_margin():
    d = _digest(best=600.0, prev=636.0)  # ~5.7% better > 0.5%
    assert classify_state(d) == DirectorState.IMPROVING


def test_classify_stuck_on_repeated_dead_class():
    d = _digest(best=636.0, prev=636.0, repeated=["bf16"])
    assert classify_state(d) == DirectorState.STUCK


def test_classify_stuck_when_no_new_classes_for_k_rounds():
    hist = [_digest(636.0, 636.0, distinct=0), _digest(637.0, 636.0, distinct=0)]
    d = _digest(best=638.0, prev=636.0, distinct=0)
    assert classify_state(d, history=hist, stuck_k=2) == DirectorState.STUCK


def test_classify_plateaued_when_flat_but_still_exploring():
    d = _digest(best=640.0, prev=636.0, distinct=1)  # worse, but a new class tried
    assert classify_state(d) == DirectorState.PLATEAUED


def test_perma_plateau_escalates_to_stuck():
    # A fleet that keeps trying NEW distinct classes each round (distinct=1), each
    # below the improvement margin: never a repeated dead class, never zero-new-
    # classes — so the old rules stay PLATEAUED forever and research never fires.
    # After stuck_k consecutive non-improving rounds the escalation must say STUCK.
    hist = [_digest(636.0, 636.0, distinct=1), _digest(637.0, 636.0, distinct=1)]
    d = _digest(best=638.0, prev=636.0, distinct=1)
    assert classify_state(d, history=hist, stuck_k=2) == DirectorState.STUCK


def test_improving_latest_round_survives_flat_history():
    # Regression: a genuinely improving latest round must still classify IMPROVING
    # even when the recent history is flat/non-improving — the escalation must not
    # mask a real win.
    hist = [_digest(636.0, 636.0, distinct=1), _digest(636.0, 636.0, distinct=1)]
    d = _digest(best=600.0, prev=636.0, distinct=1)  # ~5.7% better > margin
    assert classify_state(d, history=hist, stuck_k=2) == DirectorState.IMPROVING


def _fleet(tmp_path):
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps([NAIVE, GOOD]))
    return FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR), "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": 2, "eval_recency_iters": 100, "repeat_k": 3}})


def test_build_digest_reads_champion_and_dead_classes(tmp_path):
    summary = start_run(_fleet(tmp_path), runs_root=tmp_path / "runs")
    store = Store.open(summary.run_dir)
    d = build_digest(store, summary.run_id, round_index=0, prev_best=None)
    assert d.champion_score is not None
    assert d.best_score == d.champion_score


def test_near_miss_round_trip(tmp_path):
    store = Store.open(tmp_path / "r")
    store.execute("INSERT INTO runs (run_id, problem, fleet_config_json, started_at)"
                  " VALUES ('r1','p','{}','t')")
    ledger.mark_near_miss(store, "r1", "sha256:abc", 637.8)
    got = ledger.list_near_misses(store, "r1")
    assert got == [{"code_hash": "sha256:abc", "score": 637.8}]
