---
title: "Speed Up a Slow Test Suite With an AI Coding Agent, Safely"
description: "A test suite is a clean score for an agent loop: seconds to minimize, tests passing as the gate. How to wire that up, and three ways the number lies."
date: 2026-11-02T02:33:09Z
draft: false
category: "Guide"
tags: ["coding agents", "evaluators", "autoresearch", "benchmarking", "getting started"]
toc: true
newsletter:
  subject: "Same code, three timings: 0.38s, 0.47s, 0.48s"
  preheader: "Why a faster test suite is a harder number to trust than it looks."
  body: |
    A slow test suite looks like the perfect job for a coding agent: the score is seconds, lower is better, and "all tests pass" is a built-in correctness gate. I wired one up as a chi problem pack to see what an agent loop would actually be optimizing.

    Two things surprised me. The unchanged code timed at 0.38s, 0.47s and 0.48s on three back-to-back runs, so a 19% "win" is not much bigger than the noise. And a candidate that calls `os._exit(0)` before any test runs passes the gate and posts a score five to six times better.

    The post has the pack, the commands, and the output from my run, plus where chi can't help you yet: it wants one editable file, not a whole repo.
---

If you want an AI coding agent to make your test suite faster, you already
have most of what the agent needs. The score is wall-clock seconds, lower is
better. The correctness check is the suite itself: if the tests pass, the
change is allowed. That is the shape an
[autoresearch loop](/blog/what-is-autoresearch-ai-agents/) wants, an editable
file, a hard gate and one number. The trouble is that "tests pass" and "the
suite got faster" are both easier to fake than they look.

I built a small version of this as a [chi problem pack](/docs/problems/) and
ran it offline, with scripted candidates instead of a model, so every number
below comes from my run on 2026-11-02 and cost $0.

## What does a test-suite evaluator look like?

An evaluator is three things: a correctness command, a benchmark command that
prints one number, and a direction. For a test suite, the correctness command
is the test runner and the benchmark times it. My pack optimizes a
`slugify()` function in `slugify.py`, with five fixed unittest cases (one
feeds it a 6.6 MB string). This is the whole `problem.yaml`:

```yaml
name: faster_suite
candidate: slugify.py
entrypoints:
  correctness: "{python} -m unittest discover -s . -p test_*.py -q"
  benchmark: "{python} bench.py"
score:
  metric: suite_seconds
  direction: minimize
  repeats: 5
correctness:
  seeds: [0]
timeout_seconds: 120
```

`bench.py` runs the same `unittest` command under `time.perf_counter()`, exits 1 if
the suite fails, and prints `{"score": <seconds>}`. chi's runner treats the
correctness command's exit code as the gate and stops at the first non-zero
exit ([`chi/eval/runner.py:91-101`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py)).
Only a candidate that passes is benchmarked, five times, and scored by the
median ([`runner.py:122-143`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py)).
The tests never change. The agent edits `slugify.py` and nothing else.

I scripted three candidates: the loop-and-replace baseline, a regex version
using `[^a-z0-9]+`, and a regex version using `[^\w]+`. `chi run` evaluates
each one for real:

```
$ chi run fleet.yaml   # output trimmed
{
  "iterations": 3,
  "baseline_score": 0.418545936000001,
  "champion_score": 0.33841479300001254,
  "total_cost_usd": 0,
  "status": "done"
}
```

`chi ledger` shows what happened to each candidate:

| Candidate | correct | score (median of 5) | noise\_std |
|---|---|---|---|
| loop and replace (baseline) | 1 | 0.419 s | 0.039 |
| regex `[^a-z0-9]+` | 0 | none | none |
| regex `[^\w]+` | 1 | 0.338 s | 0.053 |

The `[^a-z0-9]+` version is wrong: it turns `Café au lait` into
`caf-au-lait`, so `test_unicode_alnum` fails and the gate rejects it
before any timing happens. That is the loop doing its job. A champion is
only ever picked from correct rows
([`chi/store/ledger.py:58-65`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py)).

## Why is a test suite a noisy score?

Because you are timing a process, not a function. Before the run I timed the
unmodified baseline three times with the same command:

```
{"score": 0.3839}
{"score": 0.4715}
{"score": 0.4770}
```

That is 0.38 to 0.48 seconds for identical code, a spread of about 25%. The "win" above, 0.419 to 0.338, is a 19% drop with a
`noise_std` of 0.04 to 0.05 on each side. It may be real, but when I timed `slugify()` alone five times per version the
ranges overlapped (0.30 to 0.45 s for the baseline, 0.22 to 0.35 s for the
regex), so I would not merge it on one run. Two things help: more repeats, and requiring
a margin over the champion's spread before promoting.
[Most agent-loop wins are noise](/blog/agent-benchmark-improvement-is-noise/)
covers that gate in detail.

There is also a ceiling on what the agent can do. An empty Python interpreter
starts in about 15 ms, and in my pack nearly all the rest is the one big test.
An agent can only shrink the part of the suite that depends on the code you
let it edit.

## How can an agent cheat a "tests pass" gate?

The gate trusts an exit code, and the candidate runs inside the process that
produces it. I replaced `slugify.py` with two lines:

```python
import os
os._exit(0)
```

`python -m unittest discover` exited 0 and ran no tests, and `bench.py`
printed a score of 0.072 s against a 0.38 to 0.48 s baseline. A real
agent is unlikely to write this on purpose; an agent told to minimize
runtime and given no other signal has an incentive to find it by accident,
for example a bad import guard that exits early. I walked through the same hole
and a child-process fix in
[can a coding agent pass your correctness check by exiting](/blog/agent-passes-correctness-check-exit-code/).
For a test suite, the cheap defense is a checker that reads the runner's
report and requires a minimum number of tests executed, instead of trusting
the exit status alone. I did not build that variant here, so treat it as
the fix to write, not a fix I measured.

## Where this falls short

- **One file, not a repo.** chi's problem packs assume one candidate file.
  Pointing it at your actual repository, with its own test layout and CI
  cache, is the biggest known adoption gap, tracked in the
  [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).
  My example works only because I shaped the problem to fit.
- **Flaky tests poison the score.** A test that sometimes fails will
  reject good candidates at random. Quarantine or fix flaky tests before
  any loop touches the suite.
- **Cached runs are not faster code.** If your runner caches results between
  invocations, the second run is quick for reasons the agent did not cause.
  Clear caches in the benchmark command.
- **I used a scripted replay, not a model.** This post shows the evaluator
  and its failure modes. It does not show how well any model optimizes a
  suite.

If you can reduce your suite to a file and a number, start with the
[problems and evaluators docs](/docs/problems/), then run the
[offline demo](/blog/first-autoresearch-loop-no-api-key/) to see the loop
work before pointing it at your own tests.
