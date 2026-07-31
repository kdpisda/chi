"""Deterministic round digest + rule-based state classification.

The brain's qualitative read is advisory; these rules DECIDE the state so the
loop can't talk itself in circles (a lesson from the manual campaign's
confounded rule-outs).
"""

from chi.director.types import DirectorState, RoundDigest
from chi.store import ledger
from chi.store.db import Store


def build_digest(store: Store, run_id: str, round_index: int, prev_best: float | None,
                 direction: str = "minimize", noise_band_pct: float = 8.0) -> RoundDigest:
    """Deterministic snapshot of the run's state for one round, from the store."""
    champ = ledger.champion(store, run_id, direction)
    champ_score = None if champ is None else champ["score_value"]
    classes = ledger.dead_classes(store, run_id)
    repeated = sorted(k for k, n in classes.items() if n >= 2)
    # a class ruled out exactly once is "fresh" — a distinct new approach was tried
    distinct_new = sum(1 for n in classes.values() if n == 1)
    return RoundDigest(
        round_index=round_index, best_score=champ_score, champion_score=champ_score,
        prev_best=prev_best, dead_classes=sorted(classes),
        repeated_dead_classes=repeated,
        near_misses=ledger.list_near_misses(store, run_id),
        distinct_new_classes=distinct_new,
    )


def _is_improving(digest: RoundDigest, direction: str,
                  promote_margin_pct: float) -> bool:
    """True if this round's best beats its own prev_best by the promote margin."""
    best, prev = digest.best_score, digest.prev_best
    if best is None or prev is None:
        return False
    if direction == "minimize":
        return best < prev * (1 - promote_margin_pct / 100)
    if direction == "maximize":
        return best > prev * (1 + promote_margin_pct / 100)
    return False


def classify_state(digest: RoundDigest, prev_digest: RoundDigest | None = None,
                   noise_band_pct: float = 8.0, promote_margin_pct: float = 0.5,
                   stuck_k: int = 2, plateau_window: int = 2,
                   history: list | None = None,
                   direction: str = "minimize") -> DirectorState:
    """Rule-based state machine (improving | plateaued | stuck).

    NoiseGuard verification of an apparent improvement happens in the Director
    loop, not here — this only reads the recorded best.
    """
    if _is_improving(digest, direction, promote_margin_pct):
        return DirectorState.IMPROVING
    recent = ((history or [])[-(stuck_k - 1):] + [digest]) if stuck_k > 1 else [digest]
    no_new_classes = len(recent) >= stuck_k and all(
        d.distinct_new_classes == 0 for d in recent)
    # perma-plateau escalation: a fleet can churn a NEW distinct class every round,
    # each below the improvement margin, so it is never "improving" yet never
    # repeats a dead class or runs out of new classes — the two rules above never
    # fire and research never runs. After stuck_k consecutive non-improving rounds,
    # escalate to STUCK so the research call fires even while classes keep churning.
    perma_plateau = len(recent) >= stuck_k and not any(
        _is_improving(d, direction, promote_margin_pct) for d in recent)
    if digest.repeated_dead_classes or no_new_classes or perma_plateau:
        return DirectorState.STUCK
    return DirectorState.PLATEAUED
