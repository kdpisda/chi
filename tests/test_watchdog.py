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


def test_zero_eval_timeout_mutates_immediately_then_kills_at_recency_cap() -> None:
    # A timed-out iteration with no evals must trigger a mutate on the FIRST
    # occurrence (steer to a smaller edit), well before the recency-half mutate;
    # the recency kill still reaps a perpetual-timeout coder at the cap.
    p = PoliciesCfg(eval_recency_iters=4)
    w = Watchdog(p)
    verdicts = [w.observe_iteration(new_evals=0, candidate_hash=f"h{i}", note="timeout")
                for i in range(4)]
    assert [v.action for v in verdicts] == ["mutate", "mutate", "mutate", "kill"]
    assert "timed out" in verdicts[0].reason
    assert "3 in a row" in verdicts[2].reason  # streak escalates in the message


def test_non_timeout_zero_eval_does_not_fire_timeout_rule() -> None:
    w = Watchdog(PoliciesCfg(eval_recency_iters=4))
    assert w.observe_iteration(new_evals=0, candidate_hash="h0", note="exit 1").action == "ok"


def test_eval_resets_timeout_streak() -> None:
    w = Watchdog(PoliciesCfg(eval_recency_iters=4))
    w.observe_iteration(new_evals=0, candidate_hash="h1", note="timeout")
    w.observe_iteration(new_evals=1, candidate_hash="h2")  # resets the streak
    v = w.observe_iteration(new_evals=0, candidate_hash="h3", note="timeout")
    assert v.action == "mutate"
    assert "1 in a row" in v.reason


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
