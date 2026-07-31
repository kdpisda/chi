"""`champion --export` must ship the VERIFIED champion source, not the last-written file.

Data-integrity regression: the champion command printed the champion's score but
copied whatever the coder last left in workdir/candidate.py. On any non-monotonic run
that live file is a slower/reverted/unverified candidate, and the true champion source
was unrecoverable. Fix: evaluate() archives every scored+correct candidate's exact bytes
under run_dir/champions/<code_hash>.py, and export copies from that archive with a hash
check — never the live file (unless it still hashes to the champion).
"""

import json
import shutil
from pathlib import Path

import yaml
from typer.testing import CliRunner

from chi.cli import app
from chi.eval.hashing import code_hash

# Stub problem (mirrors tests/test_eval_runner.py): "GOOD" == correct, "FAST" == fast.
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

FAST = b"# GOOD FAST\n"  # correct + fast  -> score 5.0  (champion)
SLOW = b"# GOOD SLOW\n"  # correct + slow  -> score 20.0 (regression)


def _setup_run(tmp_path: Path, candidate: bytes) -> Path:
    """Lay out run_dir/workdir with the stub problem and a starting candidate."""
    run_dir = tmp_path / "run"
    workdir = run_dir / "workdir"
    workdir.mkdir(parents=True)
    (workdir / "problem.yaml").write_text(yaml.safe_dump(MANIFEST))
    (workdir / "check.py").write_text(CHECK)
    (workdir / "bench.py").write_text(BENCH)
    (workdir / "candidate.py").write_bytes(candidate)
    return run_dir


def _eval(run_dir: Path, agent: str = "c1"):
    """Evaluate the current workdir candidate through the real CLI path."""
    return CliRunner().invoke(app, ["eval", "--run-dir", str(run_dir), "--agent", agent])


def test_export_is_champion_after_regression(tmp_path: Path) -> None:
    run_dir = _setup_run(tmp_path, FAST)
    assert _eval(run_dir).exit_code == 0

    # coder overwrites the workdir with a slower (still correct) candidate and evals it
    (run_dir / "workdir" / "candidate.py").write_bytes(SLOW)
    assert _eval(run_dir).exit_code == 0
    # the live file is now the SLOW loser; the champion is still FAST
    assert (run_dir / "workdir" / "candidate.py").read_bytes() == SLOW

    out = tmp_path / "out.py"
    r = CliRunner().invoke(app, ["champion", str(run_dir), "--export", str(out)])
    assert r.exit_code == 0, r.output
    champ = json.loads(r.output.strip().splitlines()[0])
    assert champ["score_value"] == 5.0  # FAST won
    # exported bytes are FAST (not the last-written SLOW) and match the champion hash
    assert out.read_bytes() == FAST
    assert code_hash(out.read_text()) == champ["code_hash"]


def test_export_refuses_non_champion_when_archive_missing(tmp_path: Path) -> None:
    run_dir = _setup_run(tmp_path, FAST)
    assert _eval(run_dir).exit_code == 0

    # simulate an older run that never archived the champion source
    shutil.rmtree(run_dir / "champions")
    # ...and the live file on disk is NOT the champion
    (run_dir / "workdir" / "candidate.py").write_bytes(SLOW)

    out = tmp_path / "out.py"
    r = CliRunner().invoke(app, ["champion", str(run_dir), "--export", str(out)])
    assert r.exit_code != 0                 # errors loudly
    assert not out.exists()                 # never ships a wrong file


def test_every_scored_candidate_is_archived(tmp_path: Path) -> None:
    run_dir = _setup_run(tmp_path, FAST)
    assert _eval(run_dir).exit_code == 0
    (run_dir / "workdir" / "candidate.py").write_bytes(SLOW)
    assert _eval(run_dir).exit_code == 0

    champions = run_dir / "champions"
    # every scored+correct candidate is recoverable, not just the current best
    assert (champions / f"{code_hash(FAST.decode())}.py").read_bytes() == FAST
    assert (champions / f"{code_hash(SLOW.decode())}.py").read_bytes() == SLOW
