---
title: "Goodhart's Law for AI Agents: When the Benchmark Lies"
description: "A coding agent optimizes the score, not your goal. I ran a proxy metric where the faster code loses, and show what a harness can and can't catch."
date: 2026-10-01T02:31:39Z
draft: false
category: "Deep dive"
tags: ["autoresearch", "evaluation integrity", "benchmarking", "proxy metrics", "coding agents"]
toc: true
newsletter:
  subject: "I wrote a benchmark where the faster code loses"
  preheader: "Same opcodes, 303 points on one line and 904 on four. A fleet will find that."
  body: |
    A coding-agent loop doesn't optimize your goal. It optimizes the number your benchmark prints. When that number is only a stand-in for the goal, the agent gets very good at the stand-in.

    I made this concrete with chi's `noisy_bench` pack, which scores code by counting executed Python lines. I ran it today: a prefix-sum loop that runs about 29x faster than the baseline scores slightly worse than the baseline, and the same loop spread over four lines scores three times worse. Identical opcodes.

    That's Goodhart's law with real numbers. The post covers the exact runs, the one cheat chi's scoring code refuses outright, and the kind of proxy bias that no re-run or holdout check can see. [Read the post](https://getchi.dev/blog/goodhart-law-ai-agents-benchmark/).
---

You give an agent a score to push down and leave it running overnight. By
morning the score has dropped a lot. Whether the thing you wanted improved is a
separate question, and the loop has no way to ask it.

That gap is Goodhart's law, usually paraphrased as: when a measure becomes a
target, it stops being a good measure. For an AI coding agent it has a concrete
form. The agent optimizes the number your benchmark prints. If that number is a
proxy for what you care about, the agent will find where the two come apart.
This post shows one such place in a benchmark I built on purpose, then covers
what a harness like chi can catch and what it can't.

## What is Goodhart's law for AI agents?

It means the benchmark score is a stand-in, and an optimizer with enough tries
finds the cases where the stand-in and the real goal disagree. Three common
forms in coding-agent loops:

- **The measurement is gameable.** A candidate that freezes the benchmark's
  clock reports a runtime of zero.
- **The measurement is noisy.** A lucky sample reads as progress. (I covered
  that in [Is your benchmark improvement just noise?](/blog/agent-benchmark-improvement-is-noise/).)
- **The measurement is biased.** It's stable and honest, and it still ranks
  candidates differently than you would. This is the hard one, and the rest of
  the post is about it.

Overfitting the benchmark's exact input is a fourth form, covered in
[How to catch a coding agent that overfits its benchmark](/blog/coding-agent-overfits-benchmark/).

## What does a biased proxy look like?

chi ships a test pack called
[`noisy_bench`](https://github.com/kdpisda/chi/blob/main/problems/noisy_bench/problem.yaml).
The task is prefix sums. Its benchmark doesn't time the code. It counts the
Python line events that fire while `solve()` runs on a fixed 300-element input
([`bench.py:30`](https://github.com/kdpisda/chi/blob/main/problems/noisy_bench/bench.py#L30)).
That's deterministic, so there's no wall-clock jitter. It's also a proxy for
"how much work does this do," and it has a bias.

I ran four candidates through that counter and also timed each one with
`timeit`. Python 3.11.15, 1 Oct 2026, a shared sandbox, so treat the wall times
as approximate:

| Candidate | Line events (the score) | Wall time per call |
|---|---|---|
| Baseline: `[sum(xs[:i+1]) for i in range(len(xs))]`, O(n²) | 302 | about 311 µs |
| Loop with `out.append`, written on one line (`for x in xs: t += x; out.append(t)`) | 303 | about 11 µs |
| The same loop, one statement per line | 904 | about 11 µs |
| `list(itertools.accumulate(xs))` | 1 | about 8 µs |

All four pass the pack's correctness check (`max_abs_error` 0.0 on seed 3).
Lower is better for this score, so read it like this:

- The loop that runs about 29x faster than the baseline scores *worse* than the
  baseline, 303 against 302.
- The two loop variants compile to the same sequence of 22 opcodes. I
  compared them with `dis`. Yet reformatting one into four lines moves the
  score from 303 to 904.
- `itertools.accumulate` scores 1, because the tracer only sees Python lines and
  the work happens in C. It's also the fastest here, but only by about 1.3x
  over the loop. The metric rates it as 300x better than the baseline's 302.

A fleet pushing this number down learns the wrong lesson from the first three
rows. It would also learn that "put everything on one line" is an
optimization. Nothing in the loop knows that wall time is what you wanted.

## Does noise make it worse?

Yes, on top of the bias. `noisy_bench` multiplies the base count by a factor
between 0.9 and 1.1, hashed from the candidate source and a run counter
([`bench.py:57-60`](https://github.com/kdpisda/chi/blob/main/problems/noisy_bench/bench.py#L57-L60)).
That simulates a noisy machine. I ran the baseline five times in a fresh
directory:

```text
306.2  276.8  317.1  308.1  295.4
```

The true count is 302, and the samples span about 277 to 317. A single sample
can show a "gain" of 8% for identical code. That's the noise problem, and
chi's NoiseGuard answers it with a median-of-N re-benchmark. It does nothing
for the bias in the table above, because the bias is in every sample.

## What can a harness catch?

Some of Goodhart's law is checkable, and chi checks it in code.

**A score that can't be true.** chi refuses a runtime that isn't finite and
strictly positive, in both the main benchmark path
([`runner.py:137`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L137))
and the shared sampler used by NoiseGuard and the holdout gate
([`sample.py:39`](https://github.com/kdpisda/chi/blob/main/chi/eval/sample.py#L39)).
I fed the sampler a benchmark that always prints `{"score": 0.0}`, as a frozen
clock would:

```text
BenchResult(ok=False, score_us=None, detail='invalid score 0.0: must be finite and > 0')
```

The measurement is rejected rather than crowned. That's the clock-gaming case
only. It says nothing about a score that's wrong but positive.

**Noise.** A median-of-N re-benchmark before an apparent win is believed. See
the noise post for what that does and doesn't buy.

**Overfitting to one input.** The holdout gate re-scores a
champion on a workload the agent never sees and compares the gain it claimed
with the gain it delivered
([docs/holdout.md](https://github.com/kdpisda/chi/blob/main/docs/holdout.md)).

## Where this falls short

None of those checks can see a biased proxy. NoiseGuard repeats the same biased
measurement. The holdout gate's job is a different workload, and it scores
with a command you wrote. I didn't run a holdout against `noisy_bench`, so I
won't claim what it would say. Reasoning from the design: if the holdout
command uses the same line-counting metric, it inherits the same bias.

What does help is a setup choice, not a feature:

- Use the real goal as the authoritative score where you can afford it. chi's
  [two-tier evaluation](/docs/problems/) keeps a cheap proxy for the inner loop
  and rations an authoritative measurement for the claims that matter.
- Make the holdout command measure the goal differently from the benchmark,
  for example wall time on larger inputs where the benchmark counts operations.
- Read the champion yourself. A fast score on a candidate that looks like a
  formatting trick is a signal about the metric, not the code.

chi doesn't detect a bad proxy automatically. The known gaps are listed in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Try it

The pack is in the repo, so you can reproduce the table. Copy the pack's
files to a scratch directory, add the candidates, and run `bench.py` against each
one. The
[evaluator guide](/blog/write-an-evaluator-for-autoresearch/) walks through the
three commands a problem needs, which is where a proxy gets chosen in the first
place.
