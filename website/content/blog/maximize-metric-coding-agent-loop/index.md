---
title: "Maximize a Metric With a Coding Agent Loop: Keep It Positive"
description: "Maximize a metric with a coding agent loop and chi drops every score at or below zero. A signed metric left my run with no champion. The fix: a ratio."
date: 2026-11-28T02:35:35Z
draft: false
category: "Deep dive"
tags: ["evaluators", "problem packs", "benchmarks", "autoresearch", "scoring"]
toc: true
newsletter:
  subject: "I asked chi to maximize. It crowned nothing, 11 times."
  preheader: "Three ways to say faster, one of them silently empties the run."
  body: |
    chi's problem.yaml lets you set `direction: maximize`, so you can hand an agent loop throughput, accuracy or a speedup instead of a runtime. I tried three versions of the same "faster is better" score on chi's example problem, all offline at $0.

    Two of them worked: the right candidate won every time. The third, percent faster than a reference, ended with status done and exit code 0, and in 11 runs it never scored a baseline or crowned a champion. Every candidate passed correctness. Not one was ever scored.

    The cause is a one-line guard that was written for runtimes and applies to every metric. The post walks through it, the runs, and the metric shape that avoids it.

    [Read how to maximize a metric safely](https://getchi.dev/blog/maximize-metric-coding-agent-loop/)
---

Not everything you'd want an agent to improve is a runtime. Throughput,
accuracy, a reward, "percent faster than the reference": those are numbers you
push up, not down. To maximize a metric with a coding agent loop, chi's
problem file has a switch, and it works, as long as your number never touches
zero.

## Can a coding agent loop maximize a metric instead of minimizing one?

Yes. In chi you set `score.direction: maximize` in `problem.yaml`
([`chi/config.py:64-67`](https://github.com/kdpisda/chi/blob/main/chi/config.py#L64-L67)),
and your benchmark prints a bigger-is-better number as `{"score": ...}`. The
champion query flips its sort order
([`chi/store/ledger.py:56-64`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py#L56-L64)),
and so does every comparison I could find downstream.

The catch: every benchmark sample must be finite and strictly greater than
zero, whatever the direction. A sample at or below zero throws away that
candidate's whole measurement. If your metric can be zero or negative, chi will
record your candidates as correct and never score them. Use a metric that is
positive by construction, like a speedup ratio, and both directions work.

The rest of this post is the evidence: the code, and three offline runs of the
same problem with three ways of saying "faster".

## Which parts of chi respect `direction: maximize`?

I read every place I could find that compares two scores. They all branch on the
direction:

| Where | What it compares | Maximize branch |
|---|---|---|
| [`ledger.champion`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py#L56-L64) | best correct, scored experiment | `ORDER BY score_value DESC` |
| [`NoiseGuard.verify`](https://github.com/kdpisda/chi/blob/main/chi/eval/noise.py) | median of re-runs vs champion | `median > champion * (1 + margin)` |
| [`review._is_improving`](https://github.com/kdpisda/chi/blob/main/chi/director/review.py#L31-L41) | round best vs previous best | `best > prev * (1 + margin)` |
| [`AutoSubmitter._improves`](https://github.com/kdpisda/chi/blob/main/chi/eval/autosubmit.py#L58-L63) | candidate vs last submission | `score > last * (1 + margin)` |
| [`holdout.gain_pct`](https://github.com/kdpisda/chi/blob/main/chi/eval/holdout.py#L81-L87) | claimed vs held-out gain | `(score - baseline) / abs(baseline)` |

NoiseGuard is the director's median-of-n re-benchmark, which asks whether an
apparent win is bigger than the noise. The holdout gate re-scores the final
champion on a workload the agents never saw.

Notice the margins are multiplicative. "Beat the champion by 0.5%" means
`score * 1.005`. That only means what you want when scores are positive: with a
negative champion, `* 1.005` moves the bar the wrong way. So the positivity
rule below isn't just an accident of history. The rest of the code quietly
assumes it.

## Why does chi drop scores at or below zero?

Because a candidate once cheated. The evaluator loop reads each repeat's score
and rejects it if it isn't a finite positive number
([`chi/eval/runner.py:133-140`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L133-L140)):

```python
sample = float(json.loads(proc.stdout.strip().splitlines()[-1])["score"])
# a runtime must be finite and strictly positive: a candidate that
# freezes the benchmark's clock (0.0) or reports a negative/inf time is
# gaming or broken, not fast. Reject the measurement — don't crown it.
if not math.isfinite(sample) or sample <= 0:
    bench_failed = True
    detail = f"invalid score {sample!r}: runtime must be finite and > 0"
    break
```

The shared sampler that NoiseGuard and the holdout gate use has the same check
([`chi/eval/sample.py:39`](https://github.com/kdpisda/chi/blob/main/chi/eval/sample.py#L39)).
For a runtime this is exactly right. A candidate that freezes `perf_counter`
reports 0 ms, and 0 ms must not win. I covered that guard, and what it doesn't
catch, in the post on
[Goodhart's law and agent benchmarks](/blog/goodhart-law-ai-agents-benchmark/).

Two details make it harsh for other metrics. The check doesn't look at
`direction`. And it `break`s out of the repeat loop on the first bad sample, so
one non-positive repeat out of five discards the four good ones, and the
candidate is stored as correct with `score_value` null.

## What happens with a signed metric?

I copied `problems/optimize_function` three times. It's chi's example problem:
make an O(n²) prefix sum fast. In each copy I set `direction: maximize` and
changed only the last lines of `bench.py`. Then I replayed the offline demo's
three candidates (the same O(n²) code with a new comment, a plain O(n) loop,
`itertools.accumulate`) with the `scripted` adapter, which writes canned
candidates and evaluates each one for real. No model, no key, $0. chi 0.2.0,
4 vCPU, 2026-11-28.

The three scores:

- **Throughput:** `4000 / elapsed_ms`, elements per millisecond.
- **Percent faster than reference:** `(ref_ms - cand_ms) / ref_ms * 100`, where
  the bench also times `itertools.accumulate` on the same input.
- **Speedup ratio:** `ref_ms / cand_ms`, same reference.

The percent version is the one that bit:

```text
$ chi run fleet-pct.yaml
  "baseline_score": null,
  "champion_score": null,
  "champion_hash": null,
  "status": "done",
```

Exit code 0. `chi ledger` shows all four candidates, baseline included, with
`correct: 1` and `score_value: null`. `chi champion` prints `no champion yet`
and exits 1. I ran it 11 times: no baseline score and no champion in all 11.

Running `bench.py` by hand shows why. The O(n²) baseline lands around −48,000
to −57,000 (it's hundreds of times slower than the reference). The O(n) loop
lands around −30 to −60. Those can never pass. The interesting one is
`itertools.accumulate`, which is the reference, so its true score is zero. Over
40 hand runs its single samples ranged from −74.6 to +32.4, and 7 of the 40
were positive. To get scored it needs five positive repeats in a row. If the
samples are independent, that's roughly 1 in 6,000, and the code-hash cache
means the first evaluation of a hash is the only one it gets.

The reason is only in the evaluator's return value. When I edited the workdir
candidate and ran `chi eval` directly, it said so:

```text
$ chi eval --run-dir runs/max-pct-cf825f --agent me
{"correct": true, "seeds_passed": [11, 27, 43], "score_value": null,
 "detail": "invalid score -31.091628792841984: runtime must be finite and > 0", ...}
```

The `experiments` table has no column for that detail
([`chi/store/schema.sql:40-57`](https://github.com/kdpisda/chi/blob/main/chi/store/schema.sql#L40-L57)),
so after a run the ledger can't tell you why nothing scored. `chi validate`
prints `OK` on this problem too. Its job is the schema, as I found in
[the dry-run post](/blog/dry-run-agent-benchmark-before-spending-tokens/).

## Which metric shapes work?

The other two copies did what you'd expect. Same three candidates, same
machine:

| Score | Baseline | Plain loop | `accumulate` | Champion |
|---|---|---|---|---|
| Throughput (elements/ms) | 117.4 | 24,191 | 57,705 | `accumulate` |
| Speedup vs reference | 0.0018 | 0.687 | 0.972 | `accumulate` |
| % faster than reference | null | null | null | none |

I ran the ratio copy three more times. `accumulate` won all four ratio runs,
at 0.93 to 0.99 (it is the reference, so about 1.0 minus timing noise).

The rule of thumb: pick a score that is positive by construction.

- **Ratios beat differences.** `ref_ms / cand_ms` is above zero for any finite
  timing, and 1.0 means "same as reference". A 0.5% margin then means
  a 0.5% speedup, which is what you'd want it to mean.
- **Don't shift a signed metric to make it positive.** Adding a constant gets
  past the guard but breaks the margins. On `score + 100000`, a 0.5% margin is
  500 units, no matter what your metric's scale is. And `100 + percent` still
  goes negative once a candidate is more than twice as slow as the reference.
- **Watch for honest zeros.** An accuracy or pass count that can legitimately
  be 0 hits the same wall. A baseline at 0 is stored unscored, so the run
  reports no baseline, and any candidate that also scores 0 is never ranked.
  Add one to a count only if you're fine with the margin math that follows, or
  measure something that can't be zero.
- **Reciprocals are noisy but rank fine.** My throughput numbers had a large
  spread (`accumulate` at 57,705 with a standard deviation near 12,800), because
  `1 / time` stretches small timings. chi takes the median, and the median of
  `1 / t` is `1 / median(t)`, so the ranking matches the runtime version.

Then dry-run it. A scripted replay of a baseline copy and one known-good
candidate shows in a few seconds whether `baseline_score` comes back null. A
null baseline on a maximize problem is the tell.

## Where this falls short

This is chi 0.2.0 behavior, read from the code and checked with scripted runs.
I didn't run a director session or a real model on these copies, and I didn't
run the holdout gate on them. The table of maximize branches comes from reading
the code, not from exercising each one.

The fix on chi's side is small. The positivity check could apply only to
minimized runtimes, or become a per-problem setting. But the multiplicative
margins would need a look too before negative scores are safe, so for now this
is a known limit, not a promise. The
[problems docs](/docs/problems/) describe `direction` without mentioning it,
and the failure reason isn't stored with the experiment. Other known gaps are
listed in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

The trade is deliberate, even if the scope isn't. chi gives up some metric
freedom for a guard against a frozen clock, one way an agent has already
faked a speedup on it.

## Writing your own maximize problem

If you're wrapping your own benchmark, the
[problem pack reference](/docs/problems/) covers the rest of `problem.yaml`:
the correctness gate, repeats, and timeouts. Make the score a ratio, run a
scripted dry run, and check that the baseline comes back with a number before
any agent starts.
