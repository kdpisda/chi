import threading

from chi.director.types import DirectorState, RoundDigest, RoundResult, StrategyUpdate
from chi.store.db import Store
from chi.store.events import list_events


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
    def __init__(self):
        self.calls = 0

    def plan(self, digest, state, per_coder_strategy, research_findings=""):
        self.calls += 1
        return StrategyUpdate(steering_text="x", per_coder_strategy=per_coder_strategy)

    def apply(self, update):
        pass


def _seed_run(tmp_path):
    store = Store.open(tmp_path / "r")
    store.execute("INSERT INTO runs (run_id, problem, fleet_config_json, started_at)"
                  " VALUES ('r1','p','{}','t')")
    return store


def test_director_emits_round_events_and_counts_spend(tmp_path, monkeypatch):
    store = _seed_run(tmp_path)
    import chi.director.loop as loopmod

    def fake_digest(store_, run_id_, round_index, prev_best, direction="minimize",
                    noise_band_pct=8.0):
        return RoundDigest(round_index=round_index, best_score=636.0,
                           champion_score=636.0, prev_best=prev_best)

    monkeypatch.setattr(loopmod, "build_digest", fake_digest)
    stop = threading.Event()
    runner = _FakeRunner([636.0, 636.0, 636.0], stop, stop_after=3)
    strat = _FakeStrategist()

    director = loopmod.Director(store, "r1", tmp_path / "r", runner, strat,
                                researcher=None, emit=lambda line: None)
    director.run(stop)

    rounds = list_events(store, "r1", "DIRECTOR_ROUND")
    assert len(rounds) == 3
    assert strat.calls == 3
    assert director.cumulative_benchmarks == 3
    assert round(director.cumulative_cost, 2) == 0.03


def test_director_skips_brain_when_nothing_changed(tmp_path, monkeypatch):
    # Pathological fast-spin guard: a slice that produces no new benchmarks and the
    # same score must NOT re-invoke the (expensive) strategist/brain every round.
    store = _seed_run(tmp_path)
    import chi.director.loop as loopmod

    def flat_digest(store_, run_id_, round_index, prev_best, direction="minimize",
                    noise_band_pct=8.0):
        return RoundDigest(round_index=round_index, best_score=636.0,
                           champion_score=636.0, prev_best=636.0)

    monkeypatch.setattr(loopmod, "build_digest", flat_digest)
    stop = threading.Event()

    class _ZeroRunner:  # every round: no new benchmarks, identical score
        def __init__(self):
            self.i = 0

        def __call__(self, iterations):
            self.i += 1
            if self.i >= 5:
                stop.set()
            return RoundResult(round_index=self.i - 1, new_experiments=[],
                               best_score=636.0, benchmarks_run=0, cost_usd=0.0)

    strat = _FakeStrategist()
    director = loopmod.Director(store, "r1", tmp_path / "r", _ZeroRunner(), strat,
                                researcher=None, emit=lambda line: None,
                                idle_sleep_seconds=0.0)
    director.run(stop)

    # every round still emits an event (liveness), but the strategist is invoked
    # only on the FIRST round (the only one with new information)
    assert len(list_events(store, "r1", "DIRECTOR_ROUND")) == 5
    assert strat.calls == 1


def test_director_halts_on_dead_eval(tmp_path, monkeypatch):
    # If K consecutive rounds end with NO scored champion (best is None), the eval
    # itself is broken — e.g. the leaderboard CLOSED and every benchmark errors —
    # so halt loudly instead of burning coder iterations forever under
    # run-until-stopped. (Found live: cholesky deadline passed 2026-07-30.)
    store = _seed_run(tmp_path)
    import chi.director.loop as loopmod

    def none_digest(store_, run_id_, round_index, prev_best, direction="minimize",
                    noise_band_pct=8.0):
        return RoundDigest(round_index=round_index, best_score=None,
                           champion_score=None, prev_best=None)

    monkeypatch.setattr(loopmod, "build_digest", none_digest)
    stop = threading.Event()

    class _Runner:
        def __init__(self):
            self.i = 0

        def __call__(self, iterations):
            self.i += 1
            return RoundResult(round_index=self.i - 1, new_experiments=[],
                               best_score=None, benchmarks_run=1, cost_usd=0.0)

    director = loopmod.Director(store, "r1", tmp_path / "r", _Runner(),
                                _FakeStrategist(), researcher=None,
                                emit=lambda line: None, idle_sleep_seconds=0.0,
                                dead_eval_rounds=3)
    director.run(stop)  # must return on its own — stop_event never set

    assert len(list_events(store, "r1", "DIRECTOR_ROUND")) == 3
    assert director.halted_reason is not None
    assert "no scored" in director.halted_reason


def test_director_researches_when_stuck(tmp_path, monkeypatch):
    store = _seed_run(tmp_path)
    import chi.director.loop as loopmod

    def stuck_digest(store_, run_id_, round_index, prev_best, direction="minimize",
                     noise_band_pct=8.0):
        # repeated dead class => classify_state returns STUCK
        return RoundDigest(round_index=round_index, best_score=636.0,
                           champion_score=636.0, prev_best=636.0,
                           dead_classes=["bf16"], repeated_dead_classes=["bf16"])

    monkeypatch.setattr(loopmod, "build_digest", stuck_digest)
    stop = threading.Event()
    runner = _FakeRunner([636.0], stop, stop_after=1)

    calls = []

    class _Researcher:
        def research(self, champion_score, dead_classes):
            calls.append((champion_score, list(dead_classes)))
            return "try recursive-right-looking-syrk"

    director = loopmod.Director(store, "r1", tmp_path / "r", runner, _FakeStrategist(),
                                researcher=_Researcher(), emit=lambda line: None)
    director.run(stop)

    assert calls == [(636.0, ["bf16"])]  # research fired once, with the dead class
