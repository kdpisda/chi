---
title: "Is Your LLM Agent's Benchmark Improvement Just Noise?"
description: "A coding agent's best run is a lucky draw, not proof. I measured how often a noisy benchmark crowns a fake win, and how much median-of-3 re-checking fixes."
date: 2026-09-29T02:36:56Z
draft: false
category: "Deep dive"
tags: ["autoresearch", "evaluation integrity", "benchmarking", "NoiseGuard", "coding agents"]
toc: true
newsletter:
  subject: "Ten identical programs. One \"won\" by 6%."
  preheader: "Picking the best of a noisy benchmark always finds a winner. Here's how often it's fake."
  body: |
    When an agent loop tries many candidates against a noisy benchmark, the best score is a lucky draw. I fed chi's noisy test pack ten programs with identical behavior. The best one looked about 6% faster than the champion, in all 60 trials.

    It matters if you run loops overnight and trust the leaderboard in the morning: the "win" you wake up to is often the luckiest sample, not the fastest code. chi re-benchmarks an apparent winner three times and only believes the median.

    The post has my measurements of how much that catches (a lot) and how much it still lets through (more than I expected), plus the one config knob that moves the number.
---

An agent loop that keeps the best of many benchmark runs will report an
improvement even when every candidate is the same program. The best of a noisy
set is a lucky draw, and the loop is built to pick it. If your benchmark
wobbles by a few percent, most "wins" an overnight run finds are the wobble.

I measured how bad this gets and how much re-checking helps, using the noisy
test problem that ships with chi. The short version: the best-of-ten single
sample beat the champion in 60 of 60 trials, and a median-of-3 re-check still
believed about one in five of them. Numbers, code and limits below.

## Why does the best score of a noisy benchmark mislead?

Because taking the minimum of noisy samples is a biased estimate. Suppose a
benchmark returns the true cost times a random factor between 0.9 and 1.1. Try
ten candidates and keep the lowest score: you have selected for the candidate
that drew the smallest factor, whatever the code did. Autoresearch (an agent
proposes code changes, a harness scores them, the best one becomes the new
champion) does exactly this selection, hundreds of times.

This is not exotic. chi's NoiseGuard docstring cites the same cholesky kernel
measuring 636, 652 and 686 µs across runs, which is about 8% of spread on one
piece of code
([`chi/eval/noise.py:1-8`](https://github.com/kdpisda/chi/blob/main/chi/eval/noise.py#L1-L8)).
That is the docstring's own observation on B200 hardware, not something I re-measured here.

## How noisy is the noisy_bench pack?

Noisy on purpose, and reproducible. The `noisy_bench` problem scores a
prefix-sum function by counting Python line events, then multiplies the count
by a factor in [0.9, 1.1] hashed from the candidate source plus a per-run
counter
([`problems/noisy_bench/bench.py`](https://github.com/kdpisda/chi/blob/main/problems/noisy_bench/bench.py)).
So repeated runs of one candidate spread like real hardware would, and a fresh
checkout replays the same sequence.

On 2026-09-29 I ran the baseline candidate through `bench.py` 30 times. The
scores ran from 273 to 331 around a median of about 305, a spread near 19% of
the median from identical code. (A second batch of 30 gave a median of 294.8;
the noise is the point.)

## How often does a single sample crown a fake winner?

Every time, in my test. I built ten variants of the baseline that differ only
by a trailing comment, so all ten behave identically and have the same true
cost. I benchmarked each once, kept the lowest as the loop would, and compared
it to a champion score of 294.8 (the median of 30 baseline draws). Sixty
trials:

| Check | Believed the "win" |
|---|---|
| Single sample, best of 10 | 60 / 60 |
| Median of 3 fresh samples, 0.5% margin | 13 / 60 |
| Median of 3, 2% margin | 8 / 60 |
| Median of 3, 5% margin | 4 / 60 |

The average best single sample sat 6.0% below the champion. Every one of those
"improvements" is fabricated: the ten programs are the same program.

## What does NoiseGuard do about it?

It re-benchmarks an apparent winner and believes only the median. When the
director sees a round improve, it calls `NoiseGuard.verify`, which takes `n`
fresh samples of the exported champion and requires the median to beat the
previous best by the promote margin
([`chi/eval/noise.py:35-50`](https://github.com/kdpisda/chi/blob/main/chi/eval/noise.py#L35-L50)):

```python
median = statistics.median(samples)
if self._direction == "minimize":
    real = median < champion_score * (1 - self._margin / 100)
```

If the win is refuted, the round is demoted to plateaued and the improvement
baseline stays where it was
([`chi/director/loop.py:98-110`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L98-L110)).
It fires only on apparent winners, so the extra benchmarks are bounded. The
director builds it with `n=3` for both leaderboard and local problems
([`chi/session/director_runner.py:41-58`](https://github.com/kdpisda/chi/blob/main/chi/session/director_runner.py#L41-L58)).

I ran the real `NoiseGuard` class over the samples above, not a
re-implementation. It took the fake-win rate from 60/60 to 13/60. That is the
good news, and 13/60 is the bad news.

## Where this falls short

The default margin is too small for a noisy benchmark, and the guard is
director-only. Three limits, from the numbers and the code:

- **A 0.5% margin against ~10% noise lets luck through.** The median of three
  draws from the same program still lands below the champion about half the
  time before the margin is applied, so a tiny margin filters little. In my run
  it was 13 of 60. Raising `promote_margin_pct` to 5% cut that to 4 of 60. The
  field is per-problem (`promote_margin_pct` in `chi/config.py:118`, default
  0.5), so you can set it in `problem.yaml`. That also means real gains under
  5% would be rejected. It is a tradeoff, not a free fix.
- **The champion score is itself one noisy number.** My test fixed it at a
  30-draw median. In a live run it is whatever the store recorded, which can
  be a lucky low or unlucky high. In an earlier version of my test the champion was
  a median of only 3 draws (304.6, 288.7 and 322.2 in three runs), and the
  believed-win count came out at 20/40, 8/40 and 39/40 for \`n\` = 3, 5 and 9.
  That swing is mostly the champion's luck, not the guard's.
- **`chi run` has no NoiseGuard.** It is wired into the director only
  (`chi/session/director_runner.py`, `chi/director/loop.py`); a plain
  `chi run` promotes the best recorded score. The
  [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md)
  also lists the missing piece: a standing noise estimate that tells you
  before the run whether the problem is measurable at all.

The simulation is synthetic, on a pack built to be noisy. It shows the
selection effect and the mechanism, not the noise of your kernel. Measure
yours: run the benchmark 20 times on unchanged code and look at the range
before you trust any single-run win.

## What should you do about it?

Measure your noise floor first, then set the margin above it, then re-check
winners. The problem-pack docs cover how the two eval tiers and the margin fit
together ([Problem packs](/docs/problems/)), and
[how to write an evaluator](/blog/write-an-evaluator-for-autoresearch/) covers
the benchmark side. For a related failure, where a score is real but does not
generalize, see
[how to catch a coding agent that overfits its benchmark](/blog/coding-agent-overfits-benchmark/).

To see the loop and its scoring end to end at `$0`, start with the
[offline demo](/blog/first-autoresearch-loop-no-api-key/).
