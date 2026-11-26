---
title: "A Noisy Benchmark Hides Real Improvements From Your Agent Loop"
description: "My agent loop made a real 5% gain every round. With ±10% benchmark noise, chi's director called 42% of those rounds flat. What I tested and what helped."
date: 2026-11-26T02:42:56Z
draft: false
category: "Deep dive"
tags: ["benchmark noise", "director", "NoiseGuard", "loop reliability", "autoresearch"]
toc: true
newsletter:
  subject: "My loop improved every round and still got called stuck"
  preheader: "220 rounds of real progress, and the fix I was sure about barely moved the count."
  body: |
    Most writing on benchmark noise is about fake wins: a lucky sample crowns a candidate that isn't faster. This post is about the opposite failure. Under the same noise, real progress can look flat.

    I fed chi's director a scripted coder that really got about 5% cheaper every round, on a pack with ±10% noise. Out of 220 rounds, every one a genuine improvement, 93 were labelled plateaued or stuck. If your loop has an LLM planner, those labels are when it gets asked for "a genuinely different approach".

    I went in sure the fix was one line in the director. I measured it, and it moved the count from 127 to 131. Two things that did help are in the post, plus a stale .pyc trap that ruined my first dataset. [Read the post](https://getchi.dev/blog/noisy-benchmark-hides-real-improvements/).
---

Your agent loop is getting better. Every candidate really is a few percent
faster than the last. But the benchmark wobbles by more than that, and a noisy
benchmark hides real improvements: the loop above your agent decides, round
after round, that nothing is happening. If it
has a planner, it then tells the agent to drop what's working and try something
else. I measured how often that happens in chi, my autoresearch harness, and
tested the fix I was sure would work. It barely moved the number.

## Can a noisy benchmark hide real improvements?

Yes, and fairly often. On a pack with ±10% benchmark noise, I gave chi's
director a scripted coder whose candidates each did about 5% less work than the
one before. Across 20 runs and 220 rounds, every one of them a real
improvement, the director labelled 127 rounds `improving` and 93 (42%)
`plateaued` or `stuck`. When each step was 10% instead of 5%, 43 of 220 rounds
were still mislabelled.

The short version: a loop can only see an improvement that is large next to its
measurement noise. Below that, how it picks its baseline matters less than I
expected, and more samples per candidate matter more.

## What the director sees each round

Some definitions first. An **autoresearch** loop is an agent that edits code,
scores each version with an evaluator, keeps the best, and repeats. chi's
**director** runs that loop in rounds, and after each round it labels the run
`improving`, `plateaued` or `stuck` from the store, without asking a model
([how those rules work](/blog/detect-agent-loop-plateau-rules/)).
**NoiseGuard** is the check that re-benchmarks an apparent win three times and
believes it only if the median beats the baseline by the 0.5% promote margin
([`chi/eval/noise.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/noise.py)).

Here is the chain, from the code:

1. The round's best score is the ledger champion: the lowest stored score of
   any correct candidate
   ([`chi/store/ledger.py:58-66`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py#L58-L66)).
   With `score.repeats: 1`, a stored score is one sample.
2. If that best beats the director's baseline by the margin, the round looks
   `improving`
   ([`chi/director/review.py:31-41`](https://github.com/kdpisda/chi/blob/main/chi/director/review.py#L31-L41)).
3. NoiseGuard then re-benchmarks the exported champion, the file in the run's
   base workdir. A refuted win demotes the round to `plateaued`
   ([`chi/director/loop.py:103-110`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L103-L110)).
4. On a confirmed win the baseline moves to the ledger's best score, which is
   the raw stored number, not NoiseGuard's median
   ([`chi/director/loop.py:155-159`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L155-L159)).
5. At the default `stuck_k` of 2, two non-improving rounds in a row are `stuck`
   ([`chi/director/review.py:64-67`](https://github.com/kdpisda/chi/blob/main/chi/director/review.py#L64-L67)).

Step 4 was my suspect. A candidate that wins partly through a lucky sample sets
the bar at that lucky number, and the next real improvement has to beat luck.

## How I tested it

Everything ran offline at $0 on 2026-11-26, on a 4-vCPU box:

- **Pack.** A copy of chi's `problems/noisy_bench`. Its score is the number of
  Python line events `solve()` executes on a fixed input, times a noise factor
  in [0.9, 1.1] hashed from the candidate source and a per-workdir run counter
  ([`bench.py`](https://github.com/kdpisda/chi/blob/main/problems/noisy_bench/bench.py)).
  So the true cost of each candidate is known exactly, and the noise is
  synthetic and uniform. Real hardware noise has a different shape.
- **Candidates.** Twelve hand-written prefix-sum candidates whose true cost
  drops by about 5% per step, 295 → 170 line events (the baseline is 302). A
  scripted coder submits one per round. The candidates are mine, not an LLM's.
- **Director.** chi's real `Director`, `RoundRunner` and local NoiseGuard
  (n = 3, margin 0.5%), as `DirectorHandle` builds them
  ([`chi/session/director_runner.py`](https://github.com/kdpisda/chi/blob/main/chi/session/director_runner.py)),
  one iteration per round, a stub brain that counts its calls, and no
  researcher.
- **Runs.** 20 runs per configuration. Each run changes only a docstring, so it
  gets different noise draws.

## What one run looks like

Run `rb-416f79`, unpatched, repeats 1. "True" is the exact line-event count,
"stored" is the one noisy sample in the ledger.

| Round | True cost | Stored | Label | NoiseGuard |
|---|---|---|---|---|
| 4 | 242 | 236.7 | improving | confirmed |
| 5 | 230 | 208.4 | improving | confirmed |
| 6 | 219 | 202.5 | plateaued | refuted |
| 7 | 208 | 222.8 | plateaued | refuted |
| 8 | 198 | 193.9 | plateaued | refuted |
| 9 | 188 | 189.5 | improving | confirmed |

Round 5's candidate drew a sample 9.4% under its true cost. NoiseGuard rightly
confirmed it, since it really was better than round 4's. The baseline then
became 208.4, the lucky number. The candidates in rounds 6, 7 and 8 were all
genuinely cheaper than round 5's, and round 8's by 14%. None of them moved the
baseline. NoiseGuard re-measured round 6's candidate twice, in rounds 6 and 7,
because it stayed the ledger champion, and refuted it both times. Round 7's
own candidate drew an unlucky 222.8 and was never re-measured at all. Round 8's
was refuted with a median of 211.8, because it had to beat 208.4 less the
margin, 207.4, not round 5's real cost of 230.

That looked like my bug, caught in the act.

## Does fixing the baseline help?

I patched a copy of `Director.run` at runtime so the baseline moves to
NoiseGuard's confirmed median instead of the raw ledger score. It changes one
line of `loop.py`, and main is untouched. Then I reran everything:

| Configuration | Rounds labelled improving (of 220) | Plateaued | Stuck | Brain calls per run (median, of 12 rounds) |
|---|---|---|---|---|
| main, repeats 1, 5% steps | 127 | 35 | 58 | 6 |
| median baseline, repeats 1, 5% steps | 131 | 46 | 43 | 6 |
| main, repeats 5, 5% steps | 148 | 27 | 45 | 5 |
| median baseline, repeats 5, 5% steps | 152 | 37 | 31 | 4.5 |
| main, repeats 1, 10% steps | 177 | 14 | 29 | 3 |
| median baseline, repeats 1, 10% steps | 177 | 26 | 17 | 3 |

The patch moved 127 to 131. The reason is back in run `rb-416f79`: a median of three
noisy samples is also a draw. Round 5's NoiseGuard samples were 211.5, 233.3
and 220.6, so the "confirmed" median was 220.6, still about 4% under the true
230. Using it as the baseline trades a lucky single sample for a slightly less
lucky median. Mostly it moved rounds from `stuck` to `plateaued`.

What did help:

- **More samples per candidate.** `score.repeats: 5` makes each stored score a
  median of five
  ([`chi/eval/runner.py:122-143`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L122-L143)).
  That took 127 to 148, and the longest run of mislabelled rounds dropped from
  a median of 2 to 1. It isn't free. Counting NoiseGuard's calls, a run used
  about 91 benchmark calls instead of about 37.
- **Bigger steps.** With 10% steps, 177 of 220 rounds read as `improving`
  under either baseline. No setting changes the step your agent finds, but it
  tells you which gains your loop can see at all.

The loop didn't lose the candidates themselves. At 5% steps, the final ledger
champion was the true best candidate in 13 of 20 runs and the second best in 6.
The cost is the labels.

## Why the labels matter

A label is more than a line in the transcript. The director writes
`state = plateaued` or `stuck` into the steering file the coders reload. With a
brain configured, each `plateaued` or `stuck` round asks it for "a genuinely
different approach" and reassigns a coder to that label
([`chi/director/strategy.py:53-69`](https://github.com/kdpisda/chi/blob/main/chi/director/strategy.py#L53-L69)).
A `stuck` round also fires the research call
([`chi/director/loop.py:131-134`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L131-L134)).
In the main 5% runs, the stub brain was asked for a new direction in a median of
6 of 12 rounds, while the candidates improved every round. My scripted coder
ignores that advice. An LLM coder would get pulled off a direction that was
working.

## A trap that ruined my first dataset

My first batch said 126 of 220 rounds were `stuck`. It was wrong. My twelve
candidates were the same size in bytes, and the scripted coder rewrote
`candidate.py` several times within a second. noisy_bench imports the candidate
as a module. Python checks a cached `.pyc` against the source's mtime, in whole
seconds, and its size, so the benchmark kept scoring an older candidate from
`__pycache__`. I caught it when a NoiseGuard sample came back above 110% of the
true cost, which the pack can't produce. Rerunning with
`PYTHONDONTWRITEBYTECODE=1`, plus an assertion that every stored sample falls
within ±10% of the true cost, fixed it. All the numbers above come from that
rerun.

An LLM agent that takes seconds per edit is unlikely to hit this. A fast
scripted replay of same-length candidates will. If your benchmark imports the
candidate, set that variable for the run, or load the candidate by compiling
its source text instead of importing the file.

## Where this falls short

- **Synthetic noise and candidates.** Uniform ±10% noise and a hand-built
  ladder of 5% steps. Real timings have tails, drift and warm-up. I'd expect
  the direction of these results to hold, not the exact counts.
- **One coder, one iteration per round.** The default is two iterations per
  round, and fleets submit more candidates. More candidates per round means
  more chances for a lucky minimum.
- **No fix on main.** The director still sets its baseline from the raw ledger
  score, and NoiseGuard is still a pass/fail verdict on apparent winners only.
  That's gap #5 on
  [chi's public gap list](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md#5-confidence-is-binary-and-only-computed-on-apparent-winners).
  What I'd try next is a baseline from a fresh, interleaved re-measure of the
  champion, the check from my
  [post on verifying a speedup](/blog/verify-coding-agent-speedup-is-real/).
  I haven't measured that inside the director yet.

## Set repeats for the gains you expect

If your agent's typical gain per round is smaller than your benchmark's spread,
raise `score.repeats` in your pack before you trust the director's labels. A
quick way to know the spread: benchmark your baseline ten times and look at the
range. The [problem pack docs](/docs/problems/) cover `score.repeats` and the
benchmark contract.
