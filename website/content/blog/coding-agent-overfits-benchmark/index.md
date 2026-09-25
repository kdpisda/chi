---
title: "How to catch a coding agent that overfits its benchmark"
description: "See a coding agent's candidate score 99.8% faster on the benchmark and gain nothing on held-out data, plus the gate that catches the overfit before it ships."
date: 2026-09-25T02:28:57Z
draft: false
category: "Case study"
tags: ["autoresearch", "evaluation integrity", "overfitting", "benchmarking", "LLM agents"]
toc: true
newsletter:
  subject: "99.8% faster on the benchmark. 0% faster for real."
  preheader: "The bug hides in plain sight: one benchmark, one input, one number to game."
  body: |
    A candidate in chi's demo pack scores 99.8% faster on its target benchmark. I ran it today, then re-scored it on three input sizes the benchmark never uses: 0.0% faster.

    That's the failure mode behind autoresearch's most-cited public result: Shopify's CEO pointed a similar loop at Liquid, the template engine behind every Shopify store, and opened a PR claiming a 53% speedup with his own caveat that it was "probably somewhat overfit." It's still open six months later.

    chi's holdout gate catches this automatically: it re-scores every champion on a workload the agent never touched and compares what it claimed to what it delivered. I ran both the cheating candidate and a genuine rewrite through it today, one came back `overfit`, the other `generalizes`.

    The post walks through both runs, the exact code the gate checks, and what a verdict does to an unattended loop that hits one.
---

An autonomous coding agent reports a win: the benchmark score dropped 99.8%.
Ship it, and the win can evaporate, because the benchmark it moved and the
thing you actually wanted faster are not always the same thing. A loop that
runs unattended for hours has no reason to notice the difference — it only
sees the number it was told to lower.

This isn't a hypothetical. In March 2026, Shopify's CEO pointed an autoresearch
loop — a coding agent that edits, measures, and keeps the best version, on
repeat — at Liquid, the Ruby template engine that renders every Shopify
storefront. About 120 automated experiments later, he opened a PR claiming a
53% parse+render speedup and 61% fewer allocations, with his own caveat
attached: *"This is probably somewhat overfit."* I checked its status while
writing this: [the PR](https://github.com/Shopify/liquid/pull/2056) is still
open, six and a half months after it was filed.

That's the clearest public example of **benchmark overfitting in an agent
loop**: a real, measured, reproducible win on the metric, that didn't survive
contact with the thing the metric was supposed to stand in for.

## Why a benchmark becomes a target

Every autoresearch loop optimizes against one frozen benchmark. That benchmark
is also the loop's selection pressure — the thing that decides which candidate
survives to the next round. Run enough iterations against a single fixed input,
a single seed, a single shape of workload, and the loop will find whatever is
cheapest to exploit in that specific measurement, not necessarily whatever
makes the underlying code faster. The benchmark improves. The program
sometimes doesn't.

I build [chi](https://github.com/kdpisda/chi), an open-source autoresearch
harness, and I hit this directly: chi already held out *correctness* (a
candidate never sees the reference outputs it's checked against), but its
*score* was fair game. A candidate could special-case the benchmark's exact
input and win cleanly.

## A worked example: 99.8% faster, 0.0% faster

chi ships a demo pack, `overfit_demo`, built to show this exact failure with
no API key and no live model. Its benchmark
([bench.py](https://github.com/kdpisda/chi/blob/main/problems/overfit_demo/bench.py))
times a prefix-sum function on one fixed 4000-element input. One scripted
candidate wins it by special-casing that input:

```python
def solve(xs):
    if len(xs) == 4000:          # the benchmark's exact input size
        return list(itertools.accumulate(xs))
    return [sum(xs[: i + 1]) for i in range(len(xs))]
```

I ran it today:

```console
$ chi run examples/holdout.yaml
{
  "baseline_score": 43.75,
  "champion_score": 0.084,
  "holdout": {
    "verdict": "overfit",
    "claimed_gain_pct": 99.81,
    "holdout_gain_pct": 0.02,
    "generalization": 0.0002,
    "detail": "claims +99.8% on the benchmark but realises only +0.0% held out (0% of the claim, floor 50%)"
  }
}
```

The `claimed_gain_pct` (99.8%) is what the candidate scored on `bench.py`. The
`holdout_gain_pct` (about 0.0%) is what the same candidate scored when chi
re-ran it against three *other* input sizes, seeded differently, under
`holdout_bench.py`, a file that never enters an agent's working directory. Same
code, same hash, two numbers, because the second one measures something the
candidate never got a chance to special-case.

Swap in a real rewrite — `itertools.accumulate` used everywhere, not just at
length 4000 — and the two numbers agree:

```console
$ chi run <same pack, examples/holdout_honest.json>
{
  "baseline_score": 43.51,
  "champion_score": 0.082,
  "holdout": {
    "verdict": "generalizes",
    "claimed_gain_pct": 99.81,
    "holdout_gain_pct": 99.89,
    "generalization": 1.0,
    "detail": "claims +99.8%, realises +99.9% held out (100% of the claim)"
  }
}
```

Both candidates report an almost identical win on the benchmark. Only one of
them is real. Exact numbers drift a little run to run, which is part of why
the gate compares a *ratio* of claimed to realised gain, not a fixed threshold.

## How the gate works

The mechanism is a second scoring command, declared once per problem
([`chi/eval/holdout.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/holdout.py)):

```yaml
# problems/overfit_demo/problem.yaml
holdout:
  benchmark: "{python} holdout_bench.py {candidate}"
  files: [holdout_bench.py]     # excluded from every agent workdir
  repeats: 3
  min_generalization: 0.5       # realize at least half the claimed gain
  max_regression_pct: 5.0       # ...and don't get materially slower
```

Three things make it hard to game:

- **The holdout files are never in the agent's workdir.** chi excludes any
  path listed under `holdout.files` from every candidate directory it builds
  (`chi/orchestrator/loop.py:52`, `holdout_ignore`). An agent with a full
  shell can read everything it's given; it just isn't given this.
- **Generalization is a ratio, not a fixed bar.** `generalization = holdout
  gain % / claimed gain %` (`chi/eval/holdout.py:21`). A win that claims 50%
  and realizes 45% generalizes. A win that claims 50% and realizes 3% scores
  0.06 — comfortably under the default floor of 0.5 — and is classed
  `overfit`, one of four verdicts the gate can return: `generalizes`,
  `overfit`, `regressed`, or `unavailable` if the holdout run itself breaks,
  which never blocks anything (`chi/eval/holdout.py:35-38`, `:59-66`).
- **It only runs when it's worth the cost.** Under chi's director (the mode
  that runs a fleet unattended toward a goal), the holdout only re-scores a
  win that already passed the noise guard — chi's median-of-N re-benchmark for
  an apparent improvement. A noise-verified win is real *on the benchmark*;
  whether it's a real speedup at all is the second, more expensive question,
  and it's only worth asking about a win that already cleared the cheap one
  (`chi/director/loop.py:111-122`).

On the two demo runs above, each paid for 3 extra held-out benchmarks — not
every candidate the fleet ever produces, only the one that looked like a
winner.

## What a verdict does to an unattended run

Tell chi's director to run until a problem hits a target score and walk away,
and an `overfit` or `regressed` champion cannot satisfy that target
(`chi/eval/holdout.py`'s `shippable` property; `chi/director/loop.py:79`). The
loop keeps researching instead of handing you a result that only works on the
one input it was tuned against. `chi status` and `chi champion` print the
verdict either way, and `chi champion --export` warns loudly on a bad one —
and still exports, because the verdict is evidence for your decision, not a
veto over it.

## Where this falls short

The gate is opt-in. It only exists for a problem that declares a `holdout:`
block, and chi's own bundled `optimize_function` pack deliberately ships
without one, since a holdout costs a baseline measurement on every run and a
pack most people use for a quick loop shouldn't pay that tax by default. Skip
the block and chi behaves exactly as before: `holdout = null`, no check.

It's also only as good as the workload you pick. A holdout that reruns the
same input under the same conditions just remeasures noise — that's the
NoiseGuard's job, not this one. It earns its keep when it varies the exact
dimension a candidate could exploit: a different input size, seed, or shape of
the same problem. For what else is still open, see
[chi's gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Try it yourself

Both runs above come from a config that ships with chi and needs no API key —
the same demo covered in
[Run your first autoresearch loop in 10 seconds](/blog/first-autoresearch-loop-no-api-key/).
Install it and reproduce the overfit verdict on your own machine:

```sh
uv tool install getchi
git clone https://github.com/kdpisda/chi && cd chi
chi run examples/holdout.yaml
```

Full mechanism, config reference, and the `unavailable`/`regressed` cases this
post didn't cover are in [the holdout docs](https://github.com/kdpisda/chi/blob/main/docs/holdout.md).
