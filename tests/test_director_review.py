from chi.director.types import DirectorState, RoundDigest, RoundResult, StrategyUpdate


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
