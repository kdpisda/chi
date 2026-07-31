"""NoiseGuard for LOCAL noisy problems (no leaderboard).

The director runner must guard ANY problem whose bench can lie — not just
leaderboard-backed ones. These tests cover: guard construction for a local
problem, the local benchmark probe (one entrypoint run -> BenchResult), the
noisy_bench pack's deterministic ~±10% spread, and the director refusing to
advance its baseline on a noise-only "win".
"""

import itertools
import json
import shutil
import threading
from pathlib import Path

from chi.config import CorrectnessCfg, EntrypointsCfg, ProblemConfig, load_problem
from chi.director.types import RoundDigest, RoundResult, StrategyUpdate
from chi.eval.noise import NoiseGuard
from chi.eval.popcorn import BenchResult, PopcornBackend
from chi.session.director_runner import build_noise_guard, local_benchmark_fn
from chi.store.db import Store
from chi.store.events import list_events

NOISY_BENCH = Path(__file__).parent.parent / "problems" / "noisy_bench"


def _local_problem(benchmark: str = "{python} bench.py {candidate}") -> ProblemConfig:
    return ProblemConfig(
        name="local",
        entrypoints=EntrypointsCfg(correctness="true", benchmark=benchmark),
        correctness=CorrectnessCfg(seeds=[1]),
    )


# --- guard construction -------------------------------------------------------


def test_local_problem_gets_a_noise_guard():
    problem = load_problem(NOISY_BENCH)
    assert problem.leaderboard is None
    assert problem.score.repeats >= 1
    guard = build_noise_guard(problem, problem.score.direction)
    assert guard is not None


def test_leaderboard_problem_keeps_popcorn_guard():
    problem = _local_problem().model_copy(update={
        "leaderboard": "lb", "benchmark_cmd": "popcorn-cli bench {candidate}"})
    guard = build_noise_guard(problem, "minimize")
    assert guard is not None
    assert isinstance(guard._benchmark.__self__, PopcornBackend)


def test_leaderboard_problem_without_benchmark_cmd_stays_unguarded():
    # a leaderboard problem missing its popcorn benchmark previously ran
    # unguarded — the local fallback must not silently take over that path
    problem = _local_problem().model_copy(update={"leaderboard": "lb"})
    assert build_noise_guard(problem, "minimize") is None


# --- the local benchmark probe ------------------------------------------------


def _write_bench(tmp_path: Path, body: str) -> Path:
    (tmp_path / "bench.py").write_text(body)
    candidate = tmp_path / "candidate.py"
    candidate.write_text("def solve(xs):\n    return xs\n")
    return candidate


def test_local_benchmark_fn_parses_trailing_score_line(tmp_path):
    candidate = _write_bench(
        tmp_path,
        "import json\nprint('warmup chatter')\nprint(json.dumps({'score': 123.5}))\n",
    )
    result = local_benchmark_fn(_local_problem())(candidate)
    assert result.ok
    assert result.score_us == 123.5


def test_local_benchmark_fn_not_ok_on_nonzero_exit(tmp_path):
    candidate = _write_bench(tmp_path, "import sys\nsys.exit(2)\n")
    result = local_benchmark_fn(_local_problem())(candidate)
    assert not result.ok
    assert result.score_us is None


def test_local_benchmark_fn_not_ok_without_score_line(tmp_path):
    candidate = _write_bench(tmp_path, "print('no json here')\n")
    result = local_benchmark_fn(_local_problem())(candidate)
    assert not result.ok
    assert result.score_us is None


# --- the noisy_bench pack -----------------------------------------------------


def _pack_copy(tmp_path: Path, name: str) -> tuple[ProblemConfig, Path]:
    workdir = tmp_path / name
    shutil.copytree(NOISY_BENCH, workdir)
    problem = load_problem(workdir)
    return problem, workdir / problem.candidate


def test_noisy_bench_spreads_deterministically(tmp_path):
    problem, candidate = _pack_copy(tmp_path, "w1")
    bench = local_benchmark_fn(problem)
    results = [bench(candidate) for _ in range(4)]
    assert all(r.ok for r in results)
    scores = [r.score_us for r in results]
    # noisy: repeated benchmarks of the SAME candidate return different samples...
    assert len(set(scores)) > 1
    # ...but bounded: the noise factor lives in [0.9, 1.1] around one base cost
    assert max(scores) <= min(scores) * (1.1 / 0.9) + 1e-9
    # and the whole sequence replays identically from a fresh workdir
    problem2, candidate2 = _pack_copy(tmp_path, "w2")
    bench2 = local_benchmark_fn(problem2)
    assert [bench2(candidate2).score_us for _ in range(4)] == scores


def test_local_guard_over_real_pack_confirms_and_refutes(tmp_path):
    # End-to-end over the real bench.py: a champion far above the candidate's
    # noise band is beaten for real; a champion far below it never is — the
    # ±10% noise cannot flip either verdict.
    problem, candidate = _pack_copy(tmp_path, "w")
    guard = build_noise_guard(problem, "minimize")
    sample = local_benchmark_fn(problem)(candidate).score_us
    confirmed = guard.verify(candidate, champion_score=1.5 * sample)
    refuted = guard.verify(candidate, champion_score=0.5 * sample)
    assert confirmed.is_real_improvement
    assert confirmed.median_score is not None
    assert not refuted.is_real_improvement
    assert confirmed.benchmarks_run == refuted.benchmarks_run == 3


# --- director: a noise-only win must not advance the baseline ------------------
# (fake runner / strategist patterns from tests/test_director_loop.py)


class _FakeRunner:
    """Yields canned scores and stops the loop after `stop_after` rounds."""

    def __init__(self, scores, stop_event, stop_after):
        self.scores = scores
        self.stop_event = stop_event
        self.stop_after = stop_after
        self.i = 0

    def __call__(self, iterations):
        score = self.scores[min(self.i, len(self.scores) - 1)]
        self.i += 1
        if self.i >= self.stop_after:
            self.stop_event.set()
        return RoundResult(round_index=self.i - 1, new_experiments=[],
                           best_score=score, benchmarks_run=1, cost_usd=0.01)


class _FakeStrategist:
    def plan(self, digest, state, per_coder_strategy, research_findings=""):
        return StrategyUpdate(steering_text="x", per_coder_strategy=per_coder_strategy)

    def apply(self, update):
        pass


def _seed_run(tmp_path):
    store = Store.open(tmp_path / "r")
    store.execute("INSERT INTO runs (run_id, problem, fleet_config_json, started_at)"
                  " VALUES ('r1','p','{}','t')")
    return store


def _improving_digest_factory(prev_bests):
    """Digest fn: round 0 scores 700, later rounds 636 — an apparent improvement."""

    def digest(store_, run_id_, round_index, prev_best, direction="minimize",
               noise_band_pct=8.0):
        prev_bests.append(prev_best)
        score = 700.0 if round_index == 0 else 636.0
        return RoundDigest(round_index=round_index, best_score=score,
                           champion_score=score, prev_best=prev_best)

    return digest


def _run_director_with_noisy_guard(tmp_path, monkeypatch, guard_samples):
    store = _seed_run(tmp_path)
    import chi.director.loop as loopmod

    prev_bests = []
    monkeypatch.setattr(loopmod, "build_digest", _improving_digest_factory(prev_bests))
    stop = threading.Event()
    runner = _FakeRunner([700.0, 636.0, 636.0], stop, stop_after=3)
    samples = itertools.cycle(guard_samples)

    def noisy_benchmark(candidate):  # spread like the pack: samples disagree
        return BenchResult(ok=True, score_us=next(samples), detail="")

    director = loopmod.Director(
        store, "r1", tmp_path / "r", runner, _FakeStrategist(), researcher=None,
        emit=lambda line: None,
        noise_guard=NoiseGuard(noisy_benchmark, n=3, promote_margin_pct=0.5))
    director.run(stop)
    payload = json.loads(list_events(store, "r1", "DIRECTOR_ROUND")[1]["payload_json"])
    return payload, prev_bests


def test_noise_only_win_does_not_advance(tmp_path, monkeypatch):
    # One lucky-low sample (612) sold the "win", but the median re-benchmark
    # (702) says champion-level noise: demote to plateaued, baseline stays put.
    payload, prev_bests = _run_director_with_noisy_guard(
        tmp_path, monkeypatch, [612.0, 702.0, 706.0])
    assert payload["state"] == "plateaued"
    assert payload["noise_verified"] is False
    assert prev_bests == [None, 700.0, 700.0]


def test_real_improvement_survives_noisy_median(tmp_path, monkeypatch):
    # The same ±% spread centred on a genuinely better score: the median (640)
    # clears the champion, the win is believed and the baseline advances.
    payload, prev_bests = _run_director_with_noisy_guard(
        tmp_path, monkeypatch, [598.0, 640.0, 660.0])
    assert payload["state"] == "improving"
    assert payload["noise_verified"] is True
    assert prev_bests == [None, 700.0, 636.0]
