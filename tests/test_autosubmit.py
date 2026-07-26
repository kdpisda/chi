import threading
from dataclasses import dataclass
from pathlib import Path

from chi.eval.autosubmit import AutoSubmitter


@dataclass
class _Result:
    ok: bool
    detail: str
    rationed: bool = False


class _FakeBackend:
    """Records every submit; the SubmissionGate normally serializes these."""

    def __init__(self, result: _Result | None = None) -> None:
        self.result = result or _Result(True, "rank 12")
        self.submitted: list[Path] = []
        self._lock = threading.Lock()

    def submit(self, candidate: Path) -> _Result:
        with self._lock:
            self.submitted.append(Path(candidate))
        return self.result


def _sub(tmp_path: Path, **kw) -> tuple[AutoSubmitter, _FakeBackend]:
    backend = _FakeBackend(kw.pop("result", None))
    return AutoSubmitter(backend, **kw), backend


def test_submits_first_correct_improvement(tmp_path: Path) -> None:
    cand = tmp_path / "c.py"
    cand.write_text("x")
    sub, backend = _sub(tmp_path, direction="minimize", baseline=1000.0)
    decision = sub.consider(cand, 900.0, correct=True)  # 10% better than 1000
    assert decision.submitted and backend.submitted == [cand]
    assert sub.last_submitted == 900.0


def test_never_submits_incorrect(tmp_path: Path) -> None:
    cand = tmp_path / "c.py"
    cand.write_text("x")
    sub, backend = _sub(tmp_path, baseline=1000.0)
    d = sub.consider(cand, 1.0, correct=False)  # amazing score but wrong
    assert not d.submitted and "not correct" in d.reason
    assert backend.submitted == []


def test_holds_sub_margin_improvement(tmp_path: Path) -> None:
    cand = tmp_path / "c.py"
    cand.write_text("x")
    sub, backend = _sub(tmp_path, direction="minimize", margin_pct=0.5, baseline=1000.0)
    # 999.9 is only 0.01% better — inside the noise guard, must NOT submit
    d = sub.consider(cand, 999.9, correct=True)
    assert not d.submitted and "not a >0.5% improvement" in d.reason
    assert backend.submitted == []
    # 990 is 1% better — clears the margin
    assert sub.consider(cand, 990.0, correct=True).submitted


def test_holds_regression(tmp_path: Path) -> None:
    cand = tmp_path / "c.py"
    cand.write_text("x")
    sub, backend = _sub(tmp_path, direction="minimize", baseline=1000.0)
    assert not sub.consider(cand, 1100.0, correct=True).submitted  # worse
    assert backend.submitted == []


def test_maximize_direction(tmp_path: Path) -> None:
    cand = tmp_path / "c.py"
    cand.write_text("x")
    sub, _ = _sub(tmp_path, direction="maximize", margin_pct=1.0, baseline=100.0)
    assert not sub.consider(cand, 100.5, correct=True).submitted  # +0.5% < 1%
    assert sub.consider(cand, 102.0, correct=True).submitted      # +2%


def test_disabled_never_submits(tmp_path: Path) -> None:
    cand = tmp_path / "c.py"
    cand.write_text("x")
    sub, backend = _sub(tmp_path, baseline=1000.0, enabled=False)
    assert not sub.consider(cand, 1.0, correct=True).submitted
    assert backend.submitted == []


def test_rationed_result_is_not_counted_as_submitted(tmp_path: Path) -> None:
    cand = tmp_path / "c.py"
    cand.write_text("x")
    sub, _ = _sub(tmp_path, baseline=1000.0,
                  result=_Result(False, "retry in 900s", rationed=True))
    d = sub.consider(cand, 500.0, correct=True)
    assert not d.submitted and "rationed" in d.reason
    assert sub.last_submitted == 1000.0  # unchanged: the score wasn't actually submitted


def test_concurrent_improvers_serialize(tmp_path: Path) -> None:
    cand = tmp_path / "c.py"
    cand.write_text("x")
    sub, backend = _sub(tmp_path, direction="minimize", margin_pct=0.5, baseline=1000.0)
    scores = [995.0, 990.0, 985.0, 980.0]  # each a real improvement
    decisions: list = []
    lock = threading.Lock()

    def worker(s: float) -> None:
        d = sub.consider(cand, s, correct=True)
        with lock:
            decisions.append(d)

    threads = [threading.Thread(target=worker, args=(s,)) for s in scores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # exactly the best-so-far monotonic improvements submit; last_submitted is the best
    assert sub.last_submitted == 980.0
    assert sum(d.submitted for d in decisions) >= 1


# --- integration: a fleet auto-submits a real improvement through the loop ----

def test_fleet_auto_submits_improvement(tmp_path, monkeypatch) -> None:
    import json
    import shutil
    import yaml
    from chi.config import FleetConfig
    from chi.orchestrator.loop import start_run
    from chi.store.db import Store
    from chi.store.events import list_events

    PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
    BEST = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
            "    return list(itertools.accumulate(xs))\n")

    # a leaderboard-targeting pack with auto_submit on and a beatable current_best
    pack = tmp_path / "pack"
    shutil.copytree(PROBLEM_DIR, pack)
    m = yaml.safe_load((pack / "problem.yaml").read_text())
    m.update({"leaderboard": "gpumode-776", "benchmark_cmd": "popcorn bench {candidate}",
              "submit_cmd": "popcorn submit {candidate}", "auto_submit": True,
              "promote_margin_pct": 0.5, "current_best": 1000.0})
    (pack / "problem.yaml").write_text(yaml.safe_dump(m))

    submitted: list = []

    class _FakeBackend:
        def __init__(self, *a, **k): ...
        def submit(self, candidate):
            submitted.append(Path(candidate))
            from types import SimpleNamespace
            return SimpleNamespace(ok=True, detail="rank 11", rationed=False)

    monkeypatch.setattr("chi.eval.popcorn.PopcornBackend", _FakeBackend)

    script = tmp_path / "s.json"
    script.write_text(json.dumps([BEST]))
    fleet = FleetConfig.model_validate({
        "run_name": "auto", "problem": str(pack), "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script), "strategy": "s"}],
        "policies": {"max_iterations": 1},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    # the toy BEST scores << 1000, so it clears the margin and auto-submits once
    assert len(submitted) == 1
    store = Store.open(summary.run_dir)
    autos = [e for e in list_events(store, summary.run_id, "STATUS")
             if '"submitted": true' in e["payload_json"]]
    assert len(autos) == 1
