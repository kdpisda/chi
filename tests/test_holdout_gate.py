"""The holdout gate: does a champion's claimed win survive an unseen workload?"""

from pathlib import Path

from chi.config import HoldoutCfg, ProblemConfig, load_problem
from chi.eval.holdout import (GENERALIZES, OVERFIT, REGRESSED, UNAVAILABLE, HoldoutGate,
                              build_holdout_gate, gain_pct)
from chi.eval.popcorn import BenchResult
from chi.orchestrator.loop import holdout_ignore, prepare_holdout_dir

CANDIDATE = Path("candidate.py")


def _fake(scores):
    it = iter(scores)
    return lambda candidate: BenchResult(ok=True, score_us=next(it), detail="")


def _gate(scores, **kw):
    kw.setdefault("repeats", 3)
    return HoldoutGate(_fake(scores), **kw)


def test_real_speedup_generalizes():
    # baseline 100 -> champion 50 on the benchmark (50% claimed); the held-out
    # workload goes 200 -> 104, a 48% gain: the improvement is real
    gate = _gate([104.0, 105.0, 103.0])
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=50.0)
    assert v.verdict == GENERALIZES
    assert v.generalizes is True and v.shippable is True
    assert round(v.claimed_gain_pct) == 50
    assert 47 <= v.holdout_gain_pct <= 49
    assert 0.9 < v.generalization < 1.0


def test_benchmark_shaped_win_is_caught_as_overfit():
    # claims 50% but the unseen workload barely moves (200 -> 197, 1.5%)
    gate = _gate([197.0, 198.0, 196.0])
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=50.0)
    assert v.verdict == OVERFIT
    assert v.generalizes is False and v.shippable is False
    assert v.generalization < 0.5
    assert "realises only" in v.detail


def test_champion_that_slows_the_unseen_workload_is_regressed():
    gate = _gate([260.0, 255.0, 258.0])
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=50.0)
    assert v.verdict == REGRESSED
    assert v.shippable is False
    assert v.holdout_gain_pct < 0


def test_partial_generalization_at_the_floor_passes():
    # 30% realised against a 50% claim = 0.6, above the default 0.5 floor
    gate = _gate([140.0, 140.0, 140.0])
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=50.0)
    assert v.verdict == GENERALIZES
    assert 0.59 < v.generalization < 0.61


def test_floor_is_configurable():
    gate = _gate([140.0, 140.0, 140.0], min_generalization=0.8)
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=50.0)
    assert v.verdict == OVERFIT


def test_maximize_direction():
    # higher is better: baseline 100 -> champion 150 (50% claim); holdout 200 -> 290
    gate = _gate([290.0, 289.0, 291.0], direction="maximize")
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=150.0)
    assert v.verdict == GENERALIZES
    assert v.holdout_gain_pct > 0

    slower = _gate([201.0, 201.0, 201.0], direction="maximize")
    bad = slower.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                        champion_score=150.0)
    assert bad.verdict == OVERFIT


def test_no_claimed_gain_is_not_overfit():
    # a champion equal to the baseline claims nothing, so there is nothing to
    # overfit — only a regression on the unseen workload would be a finding
    gate = _gate([200.0, 200.0, 200.0])
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=100.0)
    assert v.verdict == GENERALIZES
    assert v.generalization is None


def test_median_ignores_one_noisy_holdout_sample():
    gate = _gate([104.0, 480.0, 103.0])
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=50.0)
    assert v.champion_holdout == 104.0
    assert v.verdict == GENERALIZES


def test_unmeasurable_holdout_reports_unavailable_and_does_not_block():
    def broken(candidate):
        return BenchResult(ok=False, score_us=None, detail="boom")

    gate = HoldoutGate(broken, repeats=3)
    v = gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=100.0,
                    champion_score=50.0)
    assert v.verdict == UNAVAILABLE
    assert v.generalizes is False       # never an affirmative pass...
    assert v.shippable is True          # ...but chi's own breakage blocks nothing
    assert "boom" in v.detail


def test_missing_baselines_are_unavailable_not_a_failure():
    gate = _gate([104.0])
    assert gate.verify(CANDIDATE, baseline_holdout=None, baseline_score=100.0,
                       champion_score=50.0).verdict == UNAVAILABLE
    assert gate.verify(CANDIDATE, baseline_holdout=200.0, baseline_score=None,
                       champion_score=50.0).verdict == UNAVAILABLE


def test_gain_pct_directions():
    assert gain_pct(100.0, 50.0, "minimize") == 50.0
    assert gain_pct(100.0, 150.0, "maximize") == 50.0
    assert gain_pct(100.0, 150.0, "minimize") == -50.0
    assert gain_pct(0.0, 5.0, "minimize") == 0.0  # no baseline, no claim


def test_no_holdout_declared_builds_no_gate(tmp_path):
    problem = ProblemConfig(
        name="p", entrypoints={"correctness": "c", "benchmark": "b"},
        correctness={"seeds": [1]},
    )
    assert build_holdout_gate(problem, tmp_path) is None
    assert holdout_ignore(problem) is None
    assert prepare_holdout_dir(tmp_path, tmp_path, problem) is None


def test_holdout_files_are_hidden_from_agent_workdirs(tmp_path):
    problem = ProblemConfig(
        name="p", entrypoints={"correctness": "c", "benchmark": "b"},
        correctness={"seeds": [1]},
        holdout=HoldoutCfg(benchmark="b", files=["holdout_bench.py", "secret.json"]),
    )
    ignore = holdout_ignore(problem)
    hidden = ignore(tmp_path, ["candidate.py", "bench.py", "holdout_bench.py",
                               "secret.json"])
    assert hidden == {"holdout_bench.py", "secret.json"}


def test_shared_demo_problem_has_no_holdout():
    """optimize_function stays holdout-free: every run that uses it would pay a
    baseline measurement for a feature it does not exercise."""
    assert load_problem(Path("problems/optimize_function")).holdout is None


def test_overfit_demo_problem_declares_a_holdout():
    problem = load_problem(Path("problems/overfit_demo"))
    assert problem.holdout is not None
    assert problem.holdout.files == ["holdout_bench.py"]
