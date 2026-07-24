from chi.config import PoliciesCfg
from chi.orchestrator.watchdog import Watchdog


def test_healthy_iterations_are_ok() -> None:
    w = Watchdog(PoliciesCfg())
    assert w.observe_iteration(new_evals=1, candidate_hash="h1").action == "ok"
    assert w.observe_iteration(new_evals=2, candidate_hash="h2").action == "ok"


def test_no_evals_mutates_then_kills() -> None:
    p = PoliciesCfg(eval_recency_iters=4)
    w = Watchdog(p)
    actions = [w.observe_iteration(new_evals=0, candidate_hash=f"h{i}").action
               for i in range(4)]
    assert actions[1] == "mutate"  # at eval_recency_iters // 2 == 2nd zero-eval iteration
    assert actions[3] == "kill"    # at eval_recency_iters


def test_eval_resets_recency() -> None:
    w = Watchdog(PoliciesCfg(eval_recency_iters=4))
    w.observe_iteration(new_evals=0, candidate_hash="h1")
    w.observe_iteration(new_evals=1, candidate_hash="h2")  # resets
    assert w.observe_iteration(new_evals=0, candidate_hash="h3").action == "ok"


def test_repeated_hash_mutates_then_kills() -> None:
    p = PoliciesCfg(repeat_k=2, eval_recency_iters=100)
    w = Watchdog(p)
    assert w.observe_iteration(new_evals=1, candidate_hash="same").action == "ok"
    assert w.observe_iteration(new_evals=1, candidate_hash="same").action == "mutate"
    w.observe_iteration(new_evals=1, candidate_hash="same")
    assert w.observe_iteration(new_evals=1, candidate_hash="same").action == "kill"


def test_changed_hash_resets_streak() -> None:
    w = Watchdog(PoliciesCfg(repeat_k=2, eval_recency_iters=100))
    w.observe_iteration(new_evals=1, candidate_hash="a")
    w.observe_iteration(new_evals=1, candidate_hash="a")
    assert w.observe_iteration(new_evals=1, candidate_hash="b").action == "ok"
