from pathlib import Path

import yaml

from chi.config import load_problem
from chi.eval.hashing import code_hash
from chi.eval.runner import evaluate
from chi.store.db import Store

CHECK = """
import sys
text = open(sys.argv[1]).read()
sys.exit(0 if "GOOD" in text else 1)
"""

BENCH = """
import json, sys
text = open(sys.argv[1]).read()
print(json.dumps({"score": 5.0 if "FAST" in text else 20.0}))
"""

MANIFEST = {
    "name": "stub",
    "candidate": "candidate.py",
    "entrypoints": {
        "correctness": "python check.py {candidate} --seed {seed}",
        "benchmark": "python bench.py {candidate}",
    },
    "score": {"metric": "runtime_ms", "direction": "minimize", "repeats": 3},
    "correctness": {"seeds": [1, 2], "tolerance": 1e-6},
}


def _mkproblem(tmp_path: Path, candidate_src: str) -> Path:
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "problem.yaml").write_text(yaml.safe_dump(MANIFEST))
    (wd / "check.py").write_text(CHECK)
    (wd / "bench.py").write_text(BENCH)
    (wd / "candidate.py").write_text(candidate_src)
    return wd


def test_python_placeholder_resolves_to_interpreter(tmp_path: Path) -> None:
    wd = _mkproblem(tmp_path, "# GOOD FAST\n")
    manifest = dict(MANIFEST)
    manifest["entrypoints"] = {
        "correctness": "{python} check.py {candidate} --seed {seed}",
        "benchmark": "{python} bench.py {candidate}",
    }
    (wd / "problem.yaml").write_text(yaml.safe_dump(manifest))
    res = evaluate(load_problem(wd), wd)
    assert res.correct and res.score_value == 5.0


def test_code_hash_normalizes_whitespace() -> None:
    assert code_hash("x = 1\n") == code_hash("x = 1   \r\n\n\n")
    assert code_hash("x = 1") != code_hash("x = 2")


def test_correct_candidate_scores(tmp_path: Path) -> None:
    wd = _mkproblem(tmp_path, "# GOOD FAST\n")
    res = evaluate(load_problem(wd), wd)
    assert res.correct and res.seeds_passed == [1, 2]
    assert res.score_value == 5.0 and res.noise_std == 0.0


def test_incorrect_candidate_gated_no_score(tmp_path: Path) -> None:
    wd = _mkproblem(tmp_path, "# BAD\n")
    res = evaluate(load_problem(wd), wd)
    assert not res.correct and res.score_value is None


def test_benchmark_infra_failure_is_not_marked_incorrect(tmp_path: Path) -> None:
    # bench.py failing is an EVAL failure, not candidate incorrectness: the
    # candidate passed the correctness gate. Recording correct=False here polluted
    # the ledger live — the rank-14 champion kernel got marked "incorrect" when the
    # leaderboard closed and popcorn started erroring on every benchmark.
    wd = _mkproblem(tmp_path, "# GOOD FAST\n")
    (wd / "bench.py").write_text("import sys\nsys.exit(3)\n")
    res = evaluate(load_problem(wd), wd)
    assert res.correct is True          # correctness DID pass
    assert res.score_value is None      # but no score — never fabricated
    assert "benchmark failed" in res.detail


def test_nonpositive_score_is_rejected_not_crowned(tmp_path: Path) -> None:
    # Dogfood found a coder that froze the benchmark's perf_counter so runtime_ms
    # reported 0.0 — a physically impossible score that would be crowned champion
    # in a minimize problem. A non-positive (or non-finite) measurement is invalid:
    # keep correct=True (it computed the right answer) but refuse the score.
    wd = _mkproblem(tmp_path, "# GOOD FAST\n")
    (wd / "bench.py").write_text('import json\nprint(json.dumps({"score": 0.0}))\n')
    res = evaluate(load_problem(wd), wd)
    assert res.score_value is None
    assert "invalid" in res.detail.lower() or "non-positive" in res.detail.lower()


def test_negative_and_inf_scores_rejected(tmp_path: Path) -> None:
    for i, bad in enumerate(("-1.0", "1e999")):  # negative, and inf (json 1e999 -> Infinity)
        parent = tmp_path / f"case{i}"
        parent.mkdir()
        wd = _mkproblem(parent, "# GOOD FAST\n")
        (wd / "bench.py").write_text(f'import json\nprint(json.dumps({{"score": {bad}}}))\n')
        res = evaluate(load_problem(wd), wd)
        assert res.score_value is None


def test_dedup_returns_cached(tmp_path: Path) -> None:
    wd = _mkproblem(tmp_path, "# GOOD\n")
    store = Store.open(tmp_path / "run")
    prob = load_problem(wd)
    first = evaluate(prob, wd, store=store, run_id="r1", agent_id="a1")
    second = evaluate(prob, wd, store=store, run_id="r1", agent_id="a1")
    assert first.cached is False and second.cached is True
    assert second.score_value == first.score_value
