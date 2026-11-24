---
title: "Verify a Coding Agent's Speedup Is Real Before You Ship It"
description: "Your coding agent's loop says it found a 17% speedup. I ran 30 loops where every candidate was the same or slower. Most crowned a fake win. Here's the check."
date: 2026-11-24T02:43:31Z
draft: false
category: "Deep dive"
tags: ["benchmark noise", "evaluation integrity", "autoresearch", "coding agents", "benchmarking"]
toc: true
newsletter:
  subject: "My agent loop shipped a slower function as a 17% win"
  preheader: "30 loops where nothing got faster, and what the champion looked like anyway."
  body: |
    An agent loop keeps the best score it has seen. When the benchmark is noisy, that best score is partly luck, and a plain loop has no step that asks whether the win is real.

    I tested this on chi's own pack with real wall-clock timings. Every candidate the loop tried was either identical to the baseline or about 4% slower. Across 30 loops, most still exported a "champion" that claimed to be faster. One slower function claimed to be 17% faster.

    Raising the repeat count shrank the fake wins but did not remove them. The post has the numbers, the short re-check script I now run before I trust any champion, and when chi's director does this for you. [Read the post](https://getchi.dev/blog/verify-coding-agent-speedup-is-real/).
---

Your agent loop finishes overnight and reports a new champion: 17% faster than
the baseline. Before you merge it, there is a question the loop never asked.
Would that candidate still win if you measured it again? I ran chi, my
autoresearch harness, 30 times on a pack where nothing could get faster. Most
runs crowned a winner anyway, and in one batch the "winner" was really slower.
This post shows how to verify a coding agent's speedup is real, with the
numbers and a short script.

## How do you verify a coding agent's speedup is real?

Re-measure the champion and the baseline together, many times, alternating
between them, and compare the medians. Don't reuse the score from the loop.
That score was selected *because* it was the lowest, so it's biased toward a
lucky draw. A fresh, interleaved measurement isn't.

That's the whole answer. The rest of this post is why it matters more than it
sounds, and what chi does and doesn't do for you.

## Why is a loop's best score biased?

An autoresearch loop is an agent that edits code, scores each version with a
fixed evaluator, keeps the best one and repeats. "Keeps the best one" is the
problem. If you score 20 candidates on a noisy benchmark and pick the minimum,
you've picked the candidate whose measurement happened to land low, not
necessarily the fastest candidate.

A plain `chi run` does exactly this. Its champion is one SQL query: the correct
experiment with the lowest stored score
([`chi/store/ledger.py:58-66`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py#L58-L66)):

```python
order = "ASC" if direction == "minimize" else "DESC"
rows = store.query(
    f"SELECT * FROM experiments WHERE run_id=? AND correct=1 AND score_value IS NOT NULL"
    f" ORDER BY score_value {order} LIMIT 1",
    (run_id,),
)
```

Each stored score is already a median. The evaluator runs the benchmark
`score.repeats` times and records the median and the population standard
deviation as `noise_std`
([`chi/eval/runner.py:112-144`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L112-L144)).
So the noise is recorded. Nothing in the champion query uses it.

I wrote about the selection math before, on a synthetic noisy benchmark, in
[is your LLM agent's benchmark improvement just noise?](/blog/agent-benchmark-improvement-is-noise/)
That post is about the director's re-check. This one is about the plain run, on
real timings, and what the exported file is worth.

## What happens when nothing can get faster?

I used `problems/optimize_function`, the pack behind chi's offline demo. It
times one call of an O(n²) prefix sum on 4,000 floats with `time.perf_counter`,
and its `problem.yaml` sets `repeats: 5`. All runs used the scripted adapter,
which replays canned candidate files and evaluates each one for real, so no
model and no API key are involved. Machine: a 4 vCPU cloud container, November
24, 2026.

**Batch 1, identical code.** 20 candidates, each the baseline with a different
comment, so each has its own code hash. 10 runs, 21 evaluations each (the
baseline plus 20).

**Batch 2, slower code.** 20 candidates that add a redundant pass,
`_ = [sum(xs) for _ in range(100)]`, before the same O(n²) loop. In 40
interleaved pairs measured outside chi, that made them about 3.9% slower than
the baseline (34.08 ms vs 35.40 ms median). 10 runs.

| Batch | Runs that exported a non-baseline champion | Claimed gain over baseline |
|---|---|---|
| Identical code, `repeats: 5` | 8 of 10 | 2.8% to 23.2% |
| ~3.9% slower code, `repeats: 5` | 9 of 10 | 4.3% to 17.2% |
| ~3.9% slower code, `repeats: 15` | 7 of 10 | 0.2% to 5.3% |

In batch 2, nine runs exported a slower function and reported it as faster.

Here is the champion row from the first identical-code run I did, from `chi ledger`
(trimmed):

```json
{"author": "replay", "score_metric": "runtime_ms",
 "score_value": 27.61015200000827, "noise_std": 4.191205502493554}
```

The baseline in that run scored 35.45 ms with a `noise_std` of 2.94, so the
claimed gain was 22%. The champion's own spread was more than half the size of
its claimed lead, and the run still printed `"champion_score": 27.61` without
comment.

The baseline is a noisy draw too. Two of the identical-code runs crowned the
baseline because it happened to land low (29.17 ms, against a run median of
35.12). A lucky baseline doesn't ship anything bad, but it makes a real
improvement look smaller than it is.

## What does a re-measurement show?

This is the script I now run before I believe a champion. It alternates
baseline and champion so that machine drift hits both sides:

```python
"""Re-measure a champion against the baseline, interleaved, before you trust it."""
import json, statistics, subprocess, sys

bench, base, champ, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])

def score(path):
    out = subprocess.run([sys.executable, bench, path], capture_output=True, text=True)
    return float(json.loads(out.stdout.strip().splitlines()[-1])["score"])

b, c = [], []
for _ in range(n):  # alternate so drift hits both sides equally
    b.append(score(base))
    c.append(score(champ))

mb, mc = statistics.median(b), statistics.median(c)
print(f"baseline median {mb:.2f}  champion median {mc:.2f}  "
      f"gain {100 * (mb - mc) / mb:+.1f}%")
```

It assumes your benchmark prints `{"score": ...}` as its last line, which is
the contract every chi pack follows. Export the champion with
`chi champion <run_dir> --export champ.py`, copy the pack's original candidate
next to it as `base.py`, and run it:

| Champion | Loop claimed | Re-check, n=20 | Re-check, n=60 |
|---|---|---|---|
| Identical code (22% run) | +22.1% | −0.5% | |
| ~3.9% slower code (17% run) | +17.2% | −2.0% | −2.4% |
| O(n) rewrite from `chi run examples/offline.yaml` | 99.8% (33.73 → 0.069 ms) | +99.8% | |

Every fake win disappeared, and the slower champion came back slower. Even 60
pairs didn't pin the true 3.9% exactly. That's the honest limit of re-checking
on a box with this much jitter. A single benchmark sample in my first re-check
ranged from 27.3 to 43.5 ms for the baseline, and one champion sample hit 67.6
ms. But the sign was right, and that's the question that matters before a merge.

The real win, for contrast, is not subtle. The offline demo's
`itertools.accumulate` rewrite is about 500 times faster, and no amount of
noise hides that.

## Why didn't more repeats fix it?

`repeats: 15` made each stored score steadier, and the claimed gains dropped
from the teens to single digits. It didn't stop the slower code from winning in
7 of 10 runs, because the loop still takes the minimum over 20 candidates.
More candidates means more chances for one median to land low. A real loop
logs far more than 20 evaluations, so this effect gets worse with run length,
not better. Repeats also cost wall-clock on every single evaluation, while a
re-check costs you once, on the one candidate you're about to ship.

## When does chi re-check the champion for you?

In the director, not in a plain `chi run`. The director is the session mode
that runs the fleet in rounds and reviews each round
([concepts: the director](/docs/concepts/#the-director)). When a round looks
like an improvement, it re-benchmarks the exported champion with NoiseGuard: 3
fresh single samples, and the median has to beat the previous best by
`promote_margin_pct`, 0.5% by default
([`chi/eval/noise.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/noise.py),
[`chi/director/loop.py:102-110`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L102-L110),
[`chi/session/director_runner.py:41-58`](https://github.com/kdpisda/chi/blob/main/chi/session/director_runner.py#L41-L58)).
A refuted win demotes the round to plateaued.

That guard decides whether a round counts as progress. It doesn't change what
`chi champion` returns: the champion is still the lowest stored score. And
three samples is a light check against noise like this. My
[earlier noise post](/blog/agent-benchmark-improvement-is-noise/) measured that
a median of 3 samples from an unchanged program still lands below the champion
about half the time, before the margin is applied, on a pack built to be noisy.

So for a plain run the rule is simple: the exported champion is a candidate,
not a result. For a director run, NoiseGuard filters the progress signal, and
the re-check above is still what I'd run before a merge.

## Where this falls short

- **One pack, one machine.** The numbers come from a pure-Python function timed
  with `perf_counter` on a shared 4 vCPU container. A quiet bare-metal box, or a
  benchmark that times many calls, will have less noise and fewer fake wins.
  Measure your own spread before you pick an `n`.
- **The candidates were hand-written.** The scripted adapter replayed files I
  wrote to be identical or slower. A real model proposes a mix, including real
  wins. The bias is the same; how often it bites depends on how many of your
  candidates are near-ties.
- **chi doesn't do this re-check in a plain run yet.** The champion query
  ignores `noise_std`, and there is no `chi champion --verify`. Gap #5 on
  [chi's public gap list](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md#5-confidence-is-binary-and-only-computed-on-apparent-winners)
  says it plainly: chi computes `noise_std` per eval and throws it away at the
  reporting layer.
- **The script compares medians, not confidence intervals.** For a gain of a few
  percent on a noisy box, use a proper test (a bootstrap of the median
  difference, or a Mann-Whitney U), or just raise `n` until the answer stops
  moving.

## Try it on your own pack

The fastest way to see this is to run the offline demo, export its champion,
and re-check it against the baseline with the script above. Then point the
same script at a champion from your own loop. The [problem pack docs](/docs/problems/)
cover the `score.repeats` and benchmark contract the script relies on.
