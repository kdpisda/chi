---
title: "A Flaky Benchmark Timeout Cost My Agent Loop Its Best Candidate"
description: "One flaky benchmark timeout in an agent loop can bury your best candidate for the whole run. A $0 chi repro, why the cache keeps it, and a 15-line retry fix."
date: 2026-11-25T02:34:40Z
draft: false
category: "Case study"
tags: ["evaluation integrity", "benchmarking", "autoresearch", "coding agents", "agent loops"]
toc: true
newsletter:
  subject: "The fastest candidate lost because the machine hiccuped"
  preheader: "One 30-second stall, and the loop never measured that code again."
  body: |
    An agent loop is only as good as its worst measurement. I gave chi a host that stalls exactly once, on one benchmark call, and watched what happened to the candidate that happened to be running.

    It was the best candidate in the run, about 500x faster than the baseline. The loop recorded it as correct with no score, cached that by code hash, and every resubmission of the same code came back from the cache without being timed again. The run ended with the slow baseline as champion.

    The post has the repro, the two lines of chi that cause it, a query that finds these rows after any run, and a small retry wrapper that rescued the candidate. [Read the post](https://getchi.dev/blog/flaky-benchmark-timeout-agent-loop/).
---

Your agent loop ran overnight and the champion is the baseline. No candidate
beat it, so maybe the problem is just hard. Or maybe the best candidate did
run, and the machine stalled while timing it. A flaky benchmark timeout in an
agent loop is easy to miss, because the loop records it, caches it, and moves
on. I reproduced this in chi, my autoresearch harness, at $0, and the fastest
candidate in the run lost to code about 500 times slower.

## What does a flaky benchmark timeout do to an agent loop?

In chi today, it can remove a candidate from the race for the rest of the run.
A benchmark timeout marks the candidate correct with no score. That row is
cached by the candidate's code hash. If the same code is evaluated again, chi
returns the cached row and never times it. A candidate with no score can't
become champion.

That's two decisions in
[`chi/eval/runner.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py),
and each one is reasonable on its own:

- **Lines 122-128.** A benchmark that times out sets `bench_failed` and keeps
  `correct=True`. The comment above it explains why: a live leaderboard once
  closed mid-run, every benchmark started erroring, and the champion got
  recorded as incorrect. A benchmark failure is an eval failure, not a wrong
  answer.
- **Lines 70-78.** Before doing any work, `evaluate()` looks up the code hash
  in the experiments table and returns whatever is there, marked `cached`. It
  doesn't check whether the stored score is null.

The cache makes sense too: agents resubmit the same code all the time. The hash
([`chi/eval/hashing.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/hashing.py))
ignores line endings and trailing whitespace, so a copy that differs only
there is still a cache hit. Together, the two rules mean a one-off measurement failure
is stored like a result.

## The repro: a host that stalls exactly once

I copied chi's shipped `optimize_function` pack (make prefix sums fast; the
baseline is O(n²)) into a scratch folder and changed two things. The timeout
went from 60 seconds to 10. The benchmark entrypoint went through a small
wrapper that plays a noisy machine: it counts calls in a file outside the
workdir, sleeps 30 seconds on the call number I pick, and otherwise runs the
real `bench.py` unchanged.

```yaml
entrypoints:
  correctness: "{python} check.py {candidate} --seed {seed}"
  benchmark: "{python} flaky_host.py bench.py {candidate}"
timeout_seconds: 10
```

The candidate is innocent: the `itertools.accumulate` one-liner from the
offline demo. chi's scripted adapter submits that same source four times. No
model is involved, but the evaluator, the store and the champion logic are the
real ones.

The baseline takes benchmark calls 1-5 (`score.repeats` is 5), so I stalled
call 7: the second timing of the fast candidate. Results from runs on
2026-11-25, on a 4-vCPU container:

| Run | Benchmark calls | Champion |
|---|---|---|
| No stall (control) | 10 | 0.084 ms, the fast candidate |
| Stall on call 7 | 7 | 43.9 ms, the baseline |

With the stall, the ledger has two rows:

```text
author    correct  seeds_passed   score_value
baseline  1        [11, 27, 43]   43.86
replay    1        [11, 27, 43]   null
```

Submissions two to four were the same code. Each was a cache hit. The call
counter stopped at 7, so none of them ran the benchmark, and none wrote a new
`RESULT` event. The candidate file was still sitting in the workdir. When I ran
`chi eval` on it after the run, with no stall configured, it came straight back
from the cache:

```json
{"correct": true, "score_value": null, "detail": "cached", "cached": true}
```

Notice the `detail`. The first evaluation said `benchmark timeout`, but the
experiments table has no column for the reason
([`chi/store/schema.sql`](https://github.com/kdpisda/chi/blob/main/chi/store/schema.sql)).
Every later look at that candidate says only `cached`.

## Does a correctness timeout do the same?

It's worse. I moved the wrapper to the correctness entrypoint and stalled the
fast candidate's second seed. A correctness timeout sets `correct=False`
(lines 97-100). The ledger row came out `correct 0`, `seeds_passed [11]`,
score null. A correct candidate is now cached as wrong. Anything that reads
the ledger later sees an approach that failed, not a measurement that never
finished.

## Would a real agent hit this?

Less often than my scripted coder, because a model usually edits something
before it re-evaluates. Any change that alters the hash, even a new comment,
gets a fresh evaluation. I checked that: same stall, but the second submission
added one word to a comment, and that version became champion at 0.078 ms.

The cases that do bite are the ones where the code comes back byte-for-byte.
An agent reverts to a version it liked. Two coders in a fleet land on the same
idiomatic solution, and the cache is shared across the run, not kept per agent.
Or an agent sees the timeout, decides the host was busy, and resubmits as-is,
which is the right instinct and exactly what the cache defeats.

## How to protect a run today

Three things, all checked on the same repro.

**Find the rows after a run.** "Correct but never timed" is a state that should
not exist in a healthy run. This query against the run's `chi.db` (the path is
in `chi run`'s output) printed one line for the stalled run:

```python
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
rows = db.execute(
    "SELECT author, substr(code_hash, 8, 12), seeds_passed_json FROM experiments"
    " WHERE correct = 1 AND score_value IS NULL"
).fetchall()
for r in rows:
    print("passed correctness, never timed:", *r)
```

```text
passed correctness, never timed: replay 3b0056916372 [11, 27, 43]
```

Correctness timeouts don't show up here, since they're stored as plain
failures. If a run's failures look suspicious, re-run those candidates' checks
by hand.

**Retry inside the entrypoint.** chi times the whole entrypoint command, so
the pack can absorb one hiccup itself. I put a 15-line `retry.py` in the pack:
it runs the real command with a 4-second limit per attempt, and tries once more
if the first attempt hangs.

```yaml
benchmark: "{python} retry.py 4 flaky_host.py bench.py {candidate}"
```

Same stall on call 7: the wrapper killed the hung attempt, the retry
succeeded, and the fast candidate became champion at 0.097 ms. Keep two
attempts inside chi's own `timeout_seconds` budget, or chi kills the wrapper
first.

**Leave headroom in `timeout_seconds`.** The
[problem pack docs](/docs/problems/) say "a hung candidate is a failed
candidate", and that's the intent. The harness can't tell a hung candidate
from a hung host, though, so set the timeout well above your slowest honest
benchmark, not just above the typical one.

## Where this falls short

The retry wrapper also gives a genuinely flaky candidate a second chance. If a
candidate hangs one time in three, that's a real defect, and the retry hides it
from the score. A truly hung candidate still fails, after two attempts instead
of one.

None of this fixes chi. The fix belongs in the runner: don't serve a cache hit
whose score is null, and store why an eval failed. Until that lands on `main`,
the query above is the check I run. This sits next to the measurement gaps I
already track in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md),
such as noise that gets measured and then thrown away.

My stall is also fake, at a fixed position, on a fast pack. I haven't measured
how often real timeouts happen on a GPU leaderboard or a remote eval.

## Try the loop yourself

If you haven't run chi yet, start with the
[offline demo with no API key](/blog/first-autoresearch-loop-no-api-key/).
It's the same pack and the same candidate as this post, without the stall, and
it takes about two seconds. Then read
[how to inspect a run's history](/blog/inspect-coding-agent-run-history/)
to see where rows like these live.
