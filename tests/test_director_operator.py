import json
from pathlib import Path

from chi.config import FleetConfig
from chi.session.director_runner import DirectorHandle

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
NAIVE = "def solve(xs):\n    return [sum(xs[: i + 1]) for i in range(len(xs))]\n"


def _fleet(tmp_path):
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps([NAIVE]))
    return FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR), "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": 1, "eval_recency_iters": 100, "repeat_k": 3}})


def test_director_handle_starts_and_stops(tmp_path):
    h = DirectorHandle(_fleet(tmp_path), runs_root=tmp_path / "runs", brain_fn=None)
    h.start()
    assert h.ready.wait(timeout=60)
    h.request_stop()
    h.join(timeout=60)
    assert not h.alive
    assert h.run_id is not None
    assert h.error is None


def test_operator_dispatch_has_director_tools():
    from chi.session.operator import TOOLS

    names = {t["function"]["name"] for t in TOOLS}
    assert {"start_director", "stop_director", "director_status"} <= names


class _FakeDir:
    alive = True

    def __init__(self, run_dir):
        self.run_dir = run_dir


def test_interject_writes_priority_directive(tmp_path):
    from chi.session.engine import SessionEngine

    eng = SessionEngine(runs_root=tmp_path / "runs")
    rd = tmp_path / "rd"
    rd.mkdir()
    eng._director = _FakeDir(rd)
    out = eng.interject_director("focus on n=8192")
    assert "next round" in out[0]
    text = (rd / "steering.md").read_text()
    assert "focus on n=8192" in text
    assert "§op" in text  # under the operator marker the Strategist preserves


def test_free_text_routes_to_interject_when_director_alive(tmp_path):
    from chi.session.engine import SessionEngine

    eng = SessionEngine(runs_root=tmp_path / "runs")
    rd = tmp_path / "rd"
    rd.mkdir()
    eng._director = _FakeDir(rd)
    out = eng.submit("try bigger tiles")  # plain text, director alive
    assert "next round" in out[0]
    assert "try bigger tiles" in (rd / "steering.md").read_text()


class _StoppableDir:
    """Fake director handle that records a stop request."""

    alive = True

    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.run_id = "r1"
        self.stopped = False

    def request_stop(self):
        self.stopped = True


def test_slash_stop_halts_a_live_director(tmp_path):
    # THE audit's #1 blocker: /stop reported "no active run" because _cmd_stop
    # only inspected self._handle, never self._director.
    from chi.session.engine import SessionEngine

    eng = SessionEngine(runs_root=tmp_path / "runs")
    rd = tmp_path / "rd"
    rd.mkdir()
    d = _StoppableDir(rd)
    eng._director = d
    out = eng.submit("/stop")
    assert d.stopped is True
    assert "director" in out[0].lower()
    assert not (rd / "steering.md").exists()  # not turned into a steering line


def test_bare_stop_word_halts_a_live_director(tmp_path):
    from chi.session.engine import SessionEngine

    eng = SessionEngine(runs_root=tmp_path / "runs")
    rd = tmp_path / "rd"
    rd.mkdir()
    d = _StoppableDir(rd)
    eng._director = d
    out = eng.submit("stop")  # plain text, but a bare stop intent
    assert d.stopped is True
    assert not (rd / "steering.md").exists()


def test_steering_text_with_stop_still_interjects(tmp_path):
    # guard against over-capturing: "stop using recursion" is direction, not a halt
    from chi.session.engine import SessionEngine

    eng = SessionEngine(runs_root=tmp_path / "runs")
    rd = tmp_path / "rd"
    rd.mkdir()
    d = _StoppableDir(rd)
    eng._director = d
    out = eng.submit("stop using recursion, try an iterative form")
    assert d.stopped is False
    assert "next round" in out[0]
    assert "iterative form" in (rd / "steering.md").read_text()


def test_quit_warns_on_a_live_director(tmp_path):
    from chi.session.engine import SessionEngine

    eng = SessionEngine(runs_root=tmp_path / "runs")
    eng.ask_fn = lambda q, opts: None  # non-interactive: no choice made
    eng._director = _StoppableDir(tmp_path / "rd")
    out = eng.submit("/quit")
    assert eng.quit_requested is False  # didn't silently kill the director
    assert "director" in " ".join(out).lower()
