from pathlib import Path

from chi.config import load_problem
from chi.eval.runner import evaluate

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"


def _copy_problem(tmp_path: Path) -> Path:
    import shutil

    wd = tmp_path / "wd"
    shutil.copytree(PROBLEM_DIR, wd)
    return wd


def test_baseline_is_correct_and_scored(tmp_path: Path) -> None:
    wd = _copy_problem(tmp_path)
    res = evaluate(load_problem(wd), wd)
    assert res.correct and res.seeds_passed == [11, 27, 43]
    assert res.score_value is not None and res.score_value > 0


def test_fast_candidate_beats_baseline(tmp_path: Path) -> None:
    wd = _copy_problem(tmp_path)
    prob = load_problem(wd)
    baseline = evaluate(prob, wd)
    (wd / "candidate.py").write_text(
        "import itertools\n\n\n"
        "def solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n"
    )
    fast = evaluate(prob, wd)
    assert fast.correct and fast.score_value < baseline.score_value


def test_wrong_candidate_fails_gate(tmp_path: Path) -> None:
    wd = _copy_problem(tmp_path)
    (wd / "candidate.py").write_text("def solve(xs):\n    return xs\n")
    res = evaluate(load_problem(wd), wd)
    assert not res.correct
