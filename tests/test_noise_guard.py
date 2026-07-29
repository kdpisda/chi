from pathlib import Path

from chi.eval.noise import NoiseGuard
from chi.eval.popcorn import BenchResult


def _fake(scores):
    it = iter(scores)
    return lambda candidate: BenchResult(ok=True, score_us=next(it), detail="")


def test_true_improvement_survives_median():
    guard = NoiseGuard(_fake([600.0, 605.0, 598.0]), n=3, promote_margin_pct=0.5)
    r = guard.verify(Path("c.py"), champion_score=636.0)
    assert r.is_real_improvement is True
    assert 598.0 <= r.median_score <= 605.0
    assert r.benchmarks_run == 3


def test_noise_only_win_is_rejected():
    # one lucky-low sample among champion-level samples: median does NOT beat champ
    guard = NoiseGuard(_fake([600.0, 636.0, 640.0]), n=3, promote_margin_pct=0.5)
    r = guard.verify(Path("c.py"), champion_score=636.0)
    assert r.is_real_improvement is False


def test_insufficient_samples_is_not_an_improvement():
    def flaky(candidate):
        return BenchResult(ok=False, score_us=None, detail="timeout")

    guard = NoiseGuard(flaky, n=3)
    r = guard.verify(Path("c.py"), champion_score=636.0)
    assert r.is_real_improvement is False
    assert "insufficient" in r.detail


def test_maximize_direction():
    guard = NoiseGuard(_fake([700.0, 710.0, 705.0]), n=3, direction="maximize",
                       promote_margin_pct=0.5)
    r = guard.verify(Path("c.py"), champion_score=636.0)
    assert r.is_real_improvement is True
