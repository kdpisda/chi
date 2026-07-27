from chi.eval.popcorn_score import parse_geomean_us, parse_shape_times_us

# the real 15-shape benchmark stdout for codex_v652 (ranked score 0.000637 s = 637 µs)
REAL_OUTPUT = """
n: 32; cond: 2; seed: 41032; batch: 4096
 ⏱ 26.3 ± 0.70 µs
n: 64; cond: 2; seed: 41064; batch: 1024
 ⏱ 32.0 ± 0.19 µs
n: 128; cond: 2; seed: 41128; batch: 256
 ⏱ 60.9 ± 0.06 µs
n: 256; cond: 2; seed: 41256; batch: 64
 ⏱ 120 ± 0.1 µs
n: 512; cond: 2; seed: 41512; batch: 16
 ⏱ 217 ± 0.2 µs
n: 512; cond: 2; seed: 510512; batch: 640
 ⏱ 644 ± 0.6 µs
n: 1024; cond: 2; seed: 42024; batch: 4
 ⏱ 408 ± 0.2 µs
n: 1024; cond: 2; seed: 511024; batch: 60
 ⏱ 601 ± 0.2 µs
n: 2048; cond: 2; seed: 44048; batch: 2
 ⏱ 859 ± 0.8 µs
n: 2048; cond: 2; seed: 512048; batch: 8
 ⏱ 935 ± 0.2 µs
n: 4096; cond: 2; seed: 48096; batch: 1
 ⏱ 1527 ± 0.8 µs
n: 4096; cond: 2; seed: 514096; batch: 2
 ⏱ 1928 ± 1.8 µs
n: 8192; cond: 2; seed: 48192; batch: 1
 ⏱ 4.98 ± 0.005 ms
n: 16384; cond: 2; seed: 48284; batch: 1
 ⏱ 13.0 ± 0.01 ms
n: 32768; cond: 2; seed: 48368; batch: 1
 ⏱ 35.2 ± 0.01 ms

{"done": true, "runs": [{"mode": "benchmark", "passed": true, "score": null}]}
"""


def test_parses_all_fifteen_shapes() -> None:
    times = parse_shape_times_us(REAL_OUTPUT)
    assert len(times) == 15
    assert times[0] == 26.3  # µs stays µs
    assert times[-1] == 35200.0  # 35.2 ms -> 35200 µs


def test_geomean_reproduces_the_ranked_score() -> None:
    # the decisive verification: geomean of the ⏱ timings == the real leaderboard
    # score of 637 µs (codex_v652 ranked at 0.000637 s)
    score = parse_geomean_us(REAL_OUTPUT)
    assert score is not None
    assert 630 <= score <= 645, f"expected ~637µs, got {score}"


def test_mixed_units() -> None:
    text = " ⏱ 500 ± 1 ns\n ⏱ 2 ± 0 ms"  # 0.5µs and 2000µs
    times = parse_shape_times_us(text)
    assert times == [0.5, 2000.0]


def test_no_timings_returns_none_not_a_fabrication() -> None:
    # popcorn's null-score JSON alone must NOT yield a number
    assert parse_geomean_us('{"score": null, "passed": true}') is None
    assert parse_geomean_us("no timing here") is None
