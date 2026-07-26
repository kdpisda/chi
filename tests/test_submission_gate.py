import subprocess
import threading
import time
from pathlib import Path

import pytest

from chi.eval.popcorn import PopcornBackend, _parse_score
from chi.eval.submission import RationDenied, SubmissionGate, gate_for


def test_gate_serializes_submissions() -> None:
    # prove one-at-a-time: track concurrent entries; must never exceed 1
    gate = SubmissionGate(max_per_window=100, window_seconds=1000)
    concurrent = {"now": 0, "max": 0}
    lock = threading.Lock()

    def action() -> None:
        with lock:
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
        time.sleep(0.05)
        with lock:
            concurrent["now"] -= 1

    threads = [threading.Thread(target=lambda: gate.submit(action)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert concurrent["max"] == 1  # submissions never overlapped


def test_ration_denies_second_in_window() -> None:
    clock = {"t": 0.0}
    gate = SubmissionGate(max_per_window=1, window_seconds=1800,
                          clock=lambda: clock["t"])
    assert gate.submit(lambda: "first") == "first"
    with pytest.raises(RationDenied) as exc:
        gate.submit(lambda: "second")
    assert exc.value.retry_after_seconds == pytest.approx(1800.0)
    clock["t"] = 1801.0  # window elapsed
    assert gate.submit(lambda: "third") == "third"


def test_tokens_and_retry_after() -> None:
    clock = {"t": 100.0}
    gate = SubmissionGate(max_per_window=2, window_seconds=600,
                          clock=lambda: clock["t"])
    assert gate.tokens_available() == 2 and gate.retry_after() == 0.0
    gate.submit(lambda: None)
    gate.submit(lambda: None)
    assert gate.tokens_available() == 0
    clock["t"] = 400.0
    assert gate.retry_after() == pytest.approx(300.0)  # 600 - (400-100)


def test_gate_for_is_shared_per_leaderboard() -> None:
    a = gate_for("lb-xyz", max_per_window=1)
    b = gate_for("lb-xyz")
    assert a is b  # same process-wide gate


def _fake_runner(stdout: str, rc: int = 0, record: list | None = None,
                 delay: float = 0.0):
    def run(argv, cwd) -> subprocess.CompletedProcess:
        if record is not None:
            record.append(time.monotonic())
        if delay:
            time.sleep(delay)
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")
    return run


def test_benchmark_parses_score_and_runs_parallel(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.py"
    candidate.write_text("x=1")
    starts: list[float] = []
    backend = PopcornBackend(
        "lb-bench", "popcorn-cli bench {candidate}", "popcorn-cli submit {candidate}",
        gate=SubmissionGate(), runner=_fake_runner('{"geomean_us": 641.1}',
                                                   record=starts, delay=0.1),
    )
    results: list = []

    def bench() -> None:
        results.append(backend.benchmark(candidate))

    threads = [threading.Thread(target=bench) for _ in range(4)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0
    assert all(r.ok and r.score_us == 641.1 for r in results)
    assert elapsed < 0.3  # 4×0.1s ran concurrently, not serialized (would be ~0.4s)


def test_submit_goes_through_gate(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.py"
    candidate.write_text("x=1")
    clock = {"t": 0.0}
    gate = SubmissionGate(max_per_window=1, window_seconds=1800,
                          clock=lambda: clock["t"])
    backend = PopcornBackend(
        "lb-sub", "bench {candidate}", "submit {candidate}",
        gate=gate, runner=_fake_runner("submitted: rank 12"),
    )
    first = backend.submit(candidate)
    assert first.ok and "rank 12" in first.detail
    second = backend.submit(candidate)  # window full
    assert not second.ok and second.rationed


def test_benchmark_reports_failure(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.py"
    candidate.write_text("x=1")
    backend = PopcornBackend("lb-f", "bench {candidate}", "submit {candidate}",
                             gate=SubmissionGate(),
                             runner=_fake_runner("boom", rc=1))
    result = backend.benchmark(candidate)
    assert not result.ok and "failed" in result.detail


def test_parse_score_formats() -> None:
    assert _parse_score('{"geomean_us": 500.5}') == 500.5
    assert _parse_score("best time: 641.1 us") == 641.1
    assert _parse_score("no number here") is None
