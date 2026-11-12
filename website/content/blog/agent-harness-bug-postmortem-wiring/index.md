---
title: "Agent Harness Bug Postmortem: The Guard That Never Ran"
description: "An agent harness bug postmortem: my noise guard was built and tested but never ran on the command most people use. How I found it, and a 4-step wiring audit."
date: 2026-11-12T02:33:18Z
draft: false
category: "Case study"
tags: ["evaluation integrity", "loop reliability", "autoresearch", "coding agents", "benchmarking"]
toc: true
newsletter:
  subject: "My harness had a noise guard. It never ran."
  preheader: "Built, unit-tested, documented, and absent from the path most runs take."
  body: |
    I wrote a deliberately noisy benchmark to test the part of chi that re-checks lucky wins. Then I ran it with the command most people use, and the re-check never happened. Not once.

    The guard wasn't broken. It works, and it has tests. It is just only wired into one entry point, and `chi run` isn't that one. If you build agent loops, your harness probably has a mechanism like this: real code, real tests, and not on the path your runs actually take.

    The postmortem has the run, the three places I found this pattern in my own harness, and the 4-step audit I now use before I trust any safety mechanism. [Read the postmortem](https://getchi.dev/blog/agent-harness-bug-postmortem-wiring/).
---

You add a safety check to your agent harness: a re-benchmark for suspicious
wins, a lease that frees a dead worker's task, a stop rule. It has unit tests.
The docs describe it. Then you find out that the command you actually run never
calls it. This agent harness bug postmortem is about one of those in chi, and
the audit I now run so I find the next one before a user does.

## What was the bug?

chi is an open-source autoresearch harness: it points coding agents at a problem
with a programmatic score and keeps the candidates that measurably improve it.
Benchmarks are noisy, so chi has a NoiseGuard, a check that re-runs an apparent
winner several times and believes the win only if the median beats the current
best ([`chi/eval/noise.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/noise.py),
`n=3` by default at line 28, median test at lines 44-48).

The guard works. It just isn't on every path. It is constructed in exactly one
place, `build_noise_guard` in
[`chi/session/director_runner.py`](https://github.com/kdpisda/chi/blob/main/chi/session/director_runner.py)
(lines 41-57), called from `DirectorHandle` (line 138). That handle is created
only by `start_director` in `chi/session/engine.py` (line 559), which the
interactive session's operator calls when you hand it an autonomous goal. The
director is the long-running mode that plans rounds of work and reviews each
one.

`chi run`, the non-interactive command the getting-started guide uses, goes
somewhere else: `chi/cli.py:389` calls `start_run` in
[`chi/orchestrator/loop.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py).
At the end of the run, line 437 picks the champion with `ledger.champion()`,
which is a single SQL query: the lowest correct score, `ORDER BY score_value
ASC LIMIT 1` (`chi/store/ledger.py:58-66`). No re-benchmark. Whatever number a
candidate drew on its one evaluation is the number it is ranked by.

## How did a noisy benchmark expose it?

The repo ships a problem pack built for this, `problems/noisy_bench`. It scores a
prefix-sum function by counting the Python line events `solve()` executes on a
fixed input, then multiplies that count by a noise factor between 0.9 and 1.1,
hashed from the source and a per-run counter
([`bench.py`](https://github.com/kdpisda/chi/blob/main/problems/noisy_bench/bench.py)).
Its `problem.yaml` sets `repeats: 1` on purpose and says the pack "exercises the
Director's median-of-N NoiseGuard end to end."

I ran it through `chi run` with the offline scripted adapter, which replays
three canned candidates from `examples/demo_candidates.json` and needs no API
key. The config is `examples/offline.yaml` with the problem path swapped to
`problems/noisy_bench`. On 2026-11-12, chi 0.2.0, Python 3.11:

```text
$ chi run noisy.yaml --runs-root ./runs
{
  "run_id": "noisy-demo-1472a9",
  "iterations": 3,
  "baseline_score": 306.15466638616164,
  "champion_score": 1.0772676031052293,
  "total_cost_usd": 0,
  "status": "done",
  "holdout": null
}
```

It took about 0.7 seconds. The store tells the rest:

| Candidate | Line events | Recorded score | Evaluations |
|---|---|---|---|
| Baseline, O(n²) comprehension | 302 | 306.15 | 1 |
| Same function, different comment | 302 | 315.12 | 1 |
| Explicit O(n) loop | 904 | 933.14 | 1 |
| `itertools.accumulate` | 1 | 1.08 | 1 |

I counted the line events separately with the pack's own tracer. Every
`ITERATION_COMPLETE` event in the run's log says `"evals_run": 1`, and there are
exactly four `RESULT` events, one per candidate. The pack also keeps its own
tally: the per-run counter file in the workdir reads `4`. One benchmark for the
baseline, one per candidate, and none for a re-check. A guard that fired would
have run the benchmark three more times for each apparent win.

The second row is the one to stare at. Same code, a different comment, so a
different hash, and the score moved 2.9% on noise alone. In this run the noise
happened to push it the wrong way, so it lost. Had it drawn a factor below the
baseline's, `chi run` would have recorded a comment edit as an improvement and
ranked it as champion until something better came along. The real win here,
302 events down to 1, is so large that noise doesn't matter. Small wins, a few percent, are
where it does.

The `"holdout": null` is honest too: this pack has no held-out workload, so the
other gate had nothing to check.

## Was it a bug or a design choice?

Partly a design choice, and I'd rather say exactly which part. The holdout gate,
which re-scores the champion on a workload the agents never saw, *is* wired into
`chi run` (`chi/orchestrator/loop.py:442-444`). In a director slice it is turned
off on purpose (`gate_holdout=False`, line 496) because the director runs it
itself, and only on a win that already survived the NoiseGuard. The docstring at
lines 389-392 explains why: running it twice would double the cost and could
record two disagreeing verdicts.

So someone thought hard about where the holdout runs. The NoiseGuard never got
the same treatment for `chi run`. The noise post already lists it as a known
limit ([why most agent benchmark wins are
noise](/blog/agent-benchmark-improvement-is-noise/)). What made it a bug in
practice is that the docs page on problem packs says improvements "get
re-benchmarked by NoiseGuard" without naming the entry point, and the pack
itself is described as an end-to-end test of a guard that its most obvious
command never calls.

## Where else did the same pattern show up?

Once I had a name for it, I found it twice more in my own harness.

**A lease sweeper nobody calls.** `expire_stale_leases` in
[`chi/store/tasks.py`](https://github.com/kdpisda/chi/blob/main/chi/store/tasks.py)
(line 75) returns a dead worker's task to `pending`. Its only callers are in
`tests/test_store_tasks.py`. I found this while killing runs for [what a crashed
agent run leaves behind](/blog/resume-coding-agent-run-after-crash/): a killed
run's task stays `claimed` and nothing sweeps it.

**A stop rule that skips the verdict.** The director computes the NoiseGuard
verdict (`chi/director/loop.py:103-110`), but its target-score stop reads the
raw best score from the store (`_target_met(digest.best_score)`, line 176). So
the guard can reject a lucky win and the director can still stop on it. The
details and a $0 repro are in [when the agent loop stops on one lucky
benchmark](/blog/agent-loop-stops-lucky-benchmark-score/).

Three mechanisms, all tested, all real code. None of the tests were wrong. They
proved the function works when called. None of them proved it gets called.

## How do you audit an agent harness for unwired mechanisms?

This is the checklist I use now. It takes minutes per mechanism.

1. **Grep for construction and call sites, not definitions.** `grep -rn
   "NoiseGuard(" chi/` and `grep -rn "expire_stale_leases(" .` answer a different
   question than "does the class exist". If every hit is in `tests/`, it isn't
   wired.
2. **Trace from each entry point down.** List the ways a run starts (here: `chi
   run`, the director, an interactive `/run`) and follow each to where it makes
   the decision the mechanism is supposed to guard. Write down which mechanisms
   each path passes through.
3. **Build an input that must trigger it.** A noisy pack for a noise guard, a
   killed worker for a lease sweeper. If the mechanism can't fire on that input,
   it can't fire on yours.
4. **Check the run's own log for evidence that it fired.** Not the code, the
   events. Here, `evals_run: 1` on every iteration was the proof. If your harness
   doesn't log when a guard runs, add that first, because you can't audit what
   leaves no trace.

Step 4 is the one I'd skip if I were in a hurry, and it's the one that caught
this.

## Where this falls short

- **Still unfixed.** As of chi 0.2.0, `chi run` promotes the best recorded score
  with no re-benchmark. If your benchmark is noisy, keep `repeats` above 1 in
  `problem.yaml` (each stored score is then the median of that many samples,
  `chi/eval/runner.py:122-143`, default 5), or run the problem under the
  director. Repeats cost a benchmark per sample on every candidate, not just
  the winners.
- **The lease sweeper and the target stop are unfixed too.** They are known and
  written up, not solved.
- **One small run.** Four candidates on a synthetic pack. It shows the wiring,
  not how often noise fools `chi run` on a real kernel.
- **The bigger gap is upstream.** chi has no standing noise estimate that tells
  you, before a run, whether a problem is measurable at all. That's item 5 in
  the [public gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Run the audit on a loop of your own

The quickest way to see what chi records per iteration, and to check step 4 on
a real event log, is the offline demo: [your first autoresearch loop, no API key
needed](/blog/first-autoresearch-loop-no-api-key/). Swap the problem path to
`problems/noisy_bench` and you have this run.
