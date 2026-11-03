---
title: "Speed Up a Slow Test Suite With an AI Coding Agent, Safely"
description: "A test suite's runtime is a clean score for an agent loop if the gate is right. A $0 run where a cached fixture passed one test order and failed another."
date: 2026-11-03T02:33:48Z
draft: false
category: "Guide"
tags: ["autoresearch", "evaluators", "coding agents", "evaluation integrity", "benchmarking"]
toc: true
newsletter:
  subject: "The fastest test suite my agent found was wrong"
  preheader: "A cached fixture passed in one test order and failed in another. Only a shuffled gate saw it."
  body: |
    A slow test suite is a nice target for a coding agent: the score is seconds, lower is better, and "the tests pass" is a built-in correctness check. The catch is that a fast suite and a correct suite are different things.

    I built a small test-suite problem and ran a scripted fleet on it, no API key. A shared, cached fixture took the suite from about 7.4 s to about 60 ms. It also leaked state between tests, and it passed in the benchmark's test order.

    The post shows the 25-line gate that catches it, with the numbers from my run. Read it before you point an agent at your own CI time: [how to speed up a slow test suite with an AI coding agent](https://getchi.dev/blog/speed-up-slow-test-suite-ai-coding-agent/).
---

You can ask a coding agent to make a slow test suite faster, and it often will.
The risk is how it gets there. The cheapest speedups for a suite are caching a
fixture, skipping setup and sharing state between tests. All three make the
suite faster, and all three can make the tests stop testing anything. The safe
version of the task is a loop that measures runtime, and that rejects any
candidate whose tests don't pass in *every* order they might run in.

This post builds that loop for a toy suite, runs it offline at $0 with
[chi](/blog/what-is-chi-autoresearch-harness/), and shows what the gate catches.

## Why is a test suite a good target for an agent loop?

An [autoresearch](/blog/what-is-autoresearch-ai-agents/) loop needs two facts
about every candidate: is it correct, and how good is it. A test suite hands you
both. Correctness is "the tests pass". The score is wall-clock seconds, and
lower is better. That is the same shape as chi's reference problem, described in
[How to Write an Evaluator for Autoresearch](/blog/write-an-evaluator-for-autoresearch/).

The difference from a benchmark function is where the cheating lives. A function
benchmark is gamed by returning early. A suite is gamed by sharing state, and
sharing state looks like a legitimate optimization right up to the point where
a test depends on another one.

## What does the evaluator look like?

I made a problem pack with one editable file, `catalog.py`, and a 63-test
`unittest` suite. Every test's `setUp` calls `load_catalog()`, which dedupes
3,000 rows with a quadratic scan, so the suite is slow for a boring and common
reason: expensive setup repeated per test.

```yaml
name: slow_tests
candidate: catalog.py
entrypoints:
  correctness: "{python} run_tests.py {candidate} --seed {seed}"
  benchmark: "{python} bench.py {candidate}"
score:
  metric: suite_ms
  direction: minimize
  repeats: 5
correctness:
  seeds: [11, 27, 43]
timeout_seconds: 120
```

Two choices in `run_tests.py` do the real work:

- **The candidate can't touch the tests.** The runner copies the candidate and
  the pack's `tests/` directory into a fresh temp directory and runs the suite
  from there. The agent edits `catalog.py` and nothing else.
- **`--seed` shuffles the test order.** Each held-out seed runs all 63 tests in
  a different order, with `random.Random(seed).shuffle(tests)`.

The benchmark runs the same runner once with `--seed 0` and prints the elapsed
milliseconds as JSON. chi runs the correctness command once per seed in
`correctness.seeds` and stops at the first failure
([`chi/eval/runner.py:91-108`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L91-L108)).
Only a candidate that passes every seed gets benchmarked.

## What happened when I ran a fleet on it?

I replayed three candidates through chi's `scripted` adapter, which writes each
canned source and evaluates it for real, with no model involved (the same
mechanism as the [offline demo](/blog/first-autoresearch-loop-no-api-key/)).
Run on 2026-11-03 with `chi run slow-tests.yaml`, then read back with `chi ledger`:

| Candidate | Correct on seeds 11, 27, 43? | Suite time (median of 5) | Noise std |
|---|---|---|---|
| Baseline, quadratic dedupe | yes | 7,380 ms | 165 ms |
| Dedupe with a dict | yes | 121 ms | 12 ms |
| Module-level cache, returns the shared dict | **no** | not benchmarked | |
| Same cache, returns `dict(_CACHE)` | yes | 62 ms | 7 ms |

The `score_value` and `noise_std` columns are from the run's ledger. Timings
move between runs, so read them as "about 7 seconds down to about 60 ms", not as
a promise. Your suite will not drop by that much, because mine was built to be slow in
one place.

The third row is the interesting one. Its cache returns the same dictionary to
every test:

```python
_CACHE = None

def load_catalog():
    global _CACHE
    if _CACHE is None:
        ...  # build once
    return _CACHE
```

One test in the suite edits its catalog (`self.cat["SKU1"] = 0.0`), and another
asserts `SKU1` still costs 10.5. Run the first before the second against a shared
dict and the second fails. I checked each order directly:

```
seed 11: FAIL test_sku1_price_untouched   AssertionError: 0.0 != 10.5
seed 27: OK (63 tests)
seed 43: OK (63 tests)
seed 0:  OK (63 tests)
```

It fails on one of three held-out orders, and it passes the order the benchmark
uses. A gate that ran the suite once in file order would have crowned it, and
at 54 ms in a standalone benchmark run it would have won by a wide margin. The
fourth candidate returns a copy, keeps the speedup, and passes all three orders.

## Why does the runtime score need a correctness gate this strict?

Because an agent loop optimizes whatever the evaluator can tell apart. If the
evaluator can't tell a shared-state cache from a correct one, the loop has no
reason to prefer the correct one, and the shared-state cache is faster. This is
the same pattern as [Goodhart's law for AI agents](/blog/goodhart-law-ai-agents-benchmark/),
and the same family as the
[exit-code hole](/blog/agent-passes-correctness-check-exit-code/): the score
is real and the correctness is fake.

chi's contract makes that a hard line. Correctness is a gate, not a term in the
score, so a candidate that fails any seed is never benchmarked and can't become
the champion ([problems docs](/docs/problems/)).

## How do flaky tests and cached runs break the evaluator?

Two failure modes are worth designing for before you let an agent run unattended.

**Flaky tests look like regressions.** If a test fails one run in twenty on its
own, a correct candidate will sometimes fail the gate, and the loop will throw
away good work. I did not measure this in my toy, because the toy suite is
deterministic. Quarantine known-flaky tests out of the gated set first, or the
gate will be noisy in the wrong direction.

**Cached runs make timings meaningless.** If your runner uses a result cache,
build cache, or a warmed `.pyc` directory that persists between evaluations, the
second benchmark measures the cache. Mine avoids this by running every
evaluation in a fresh temp directory; the process start is still inside the
timed region, which is why even a no-op suite has a floor. The `repeats: 5` in
`problem.yaml` and the recorded noise standard deviation are there so that a
gain smaller than the spread isn't read as a win.

## Where this falls short

- **The suite is a toy.** 63 generated tests, one slow function, stdlib
  `unittest`. I did not run this against a real pytest project, a database
  fixture or a parallel runner such as xdist, so I can't tell you how it behaves
  there.
- **No model was involved.** The three candidates are canned. This post shows
  what the evaluator accepts and rejects, not whether a given LLM would find the
  shared-cache trick. It says nothing on that question.
- **Shuffling finds order dependence it happens to hit.** Three seeds caught this
  bug on one of the three. A rarer dependency could survive three shuffles; more
  seeds cost more evaluation time.
- **Wall-clock includes interpreter start.** For a suite this fast the floor is
  a real fraction of the score. On a long suite it fades.
- **chi has known gaps.** See the
  [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md)
  for what isn't built.

## What should I take from this for my own CI?

Write the evaluator before you launch the agent, and make it reject by
construction: tests outside the candidate's reach, the order shuffled per seed,
and a benchmark that is separate from the gate. Then check the evaluator by
hand-feeding it the cheap speedup you are most afraid of, as I did with the
shared cache. If it accepts that, fix the evaluator first.

To see the loop itself with no key, run the
[offline demo](/blog/first-autoresearch-loop-no-api-key/):

```sh
uv tool install getchi
chi run examples/offline.yaml
```

The pack files for this post are small enough to rebuild from the description
above: one editable module, a `tests/` directory, a shuffling runner and a timing
script.
