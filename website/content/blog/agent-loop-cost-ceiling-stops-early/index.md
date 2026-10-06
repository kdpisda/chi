---
title: "Why Your Agent Loop's Cost Ceiling Stops Early (a Real Bug)"
description: "A cost ceiling on an agent loop can stop far below its number. I measured chi's director halting at $1.20 of real spend on a $2.00 ceiling. Here is why."
date: 2026-10-06T02:32:18Z
draft: false
category: "Deep dive"
tags: ["budgets", "cost control", "autoresearch", "director", "loop reliability"]
toc: true
newsletter:
  subject: "My $25 ceiling fired at $2.40 of real spend"
  preheader: "A running total that sums a log which already contains the running total."
  body: |
    I set a cost ceiling on chi's autonomous director and ran it against a scripted coder that costs $0.40 an iteration. A $2.00 ceiling stopped the run after $1.20 of real spend. A $25.00 ceiling stopped it after $2.40.

    The cause is small and easy to miss: the director sums the run's event log each round, but its own round events are written into that same log with a cost on them. So it counts its own totals again.

    If you hand a loop a dollar ceiling and walk away, this is the failure that costs you research instead of money. The post has the repro, the numbers, and what to do until it is fixed. [Read the post](https://getchi.dev/blog/agent-loop-cost-ceiling-stops-early/).
---

You give an overnight agent loop a spend ceiling of $25 and go to bed. In the
morning it has stopped, the log says the ceiling was hit, and the provider
dashboard says you spent $2.40. The ceiling did its job and failed you at the
same time, because the run you wanted never happened.

This post is about why an agent loop's cost ceiling can stop early, using a bug
I found in chi, the harness I build. chi's autonomous director (a control loop
that runs the coder fleet in rounds until a goal or a ceiling is hit) counts
spend that was already counted. I confirmed it with a `$0` run on 2026-10-06.

## What happens when a cost ceiling double-counts?

The loop halts at a fraction of the ceiling you set. The money is safe. The
research is not. I ran chi's real director (real `RoundRunner`, real run
store, real evaluator) with one scripted coder, one iteration per round, no noise guard. I patched
the scripted adapter to report $0.40 per iteration, since the stock one spends
nothing. The director printed its own running
total each round:

```text
round 0: plateaued · best 36.76 · Σ 1 benches $0.40
round 1: stuck · best 36.76 · Σ 1 benches $1.60
round 2: stuck · best 36.76 · Σ 1 benches $4.40
✓ director stopped: cost ceiling reached: $4.40 >= $2.00 (budget)
```

Three iterations ran. Real spend, summed from the `ITERATION_COMPLETE` events,
was $1.20. The director reported $4.40 and stopped. I repeated it with five
ceilings:

| Ceiling | Rounds before stop | Real spend at stop | Director's reported total |
|---|---|---|---|
| $1 | 2 | $0.80 | $1.60 |
| $2 | 3 | $1.20 | $4.40 |
| $5 | 4 | $1.60 | $10.40 |
| $10 | 4 | $1.60 | $10.40 |
| $25 | 6 | $2.40 | $48.00 |

Look at the $5 and $10 rows. The same run stopped at the same point for both,
because the reported total jumped from $4.40 straight to $10.40. Past a point,
changing your ceiling does nothing.

## Why does the director count its own total again?

Two lines that are each reasonable, in two files. Each round, `RoundRunner`
returns the run's spend by summing every event for the run
([`chi/director/round.py:62-64`](https://github.com/kdpisda/chi/blob/main/chi/director/round.py#L62-L64)):

```python
cost = store.query(
    "SELECT COALESCE(SUM(cost_usd),0) c FROM events WHERE run_id=?",
    (self.run_id,))[0]["c"]
```

That query returns the cumulative total for the run so far, not this round's
spend. Then the `Director` treats it as this round's spend and adds it to its
own counter
([`chi/director/loop.py:92`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L92)):

```python
self.cumulative_cost += result.cost_usd
```

That alone would count round 0 once and every later round's earlier spend
again. It gets worse, because at the end of each round the director records a
`DIRECTOR_ROUND` event with `cost_usd=result.cost_usd`
([`loop.py:148`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L148)).
That event goes into the same `events` table the next round sums. So the next
query picks up the real iterations and the director's own previous totals.

Walk it through with $0.40 per iteration. Round 0 sums $0.40. Round 1 sums two
iterations ($0.80) plus round 0's event ($0.40), so $1.20, and the counter goes
to $1.60. Round 2 sums three iterations ($1.20) plus the events from rounds 0
and 1 ($0.40 + $1.20), so $2.80, and the counter goes to $4.40. That matches
the output above. After the first few rounds each reported total is a bit over
twice the last (4.4, 10.4, 22.8, 48.0 in my runs), so the gap widens
every round.

## Is the unit test wrong?

No, the test checks the loop, not the runner. `test_director_self_stops_at_cost_ceiling`
in [`tests/test_director_loop.py`](https://github.com/kdpisda/chi/blob/main/tests/test_director_loop.py)
uses a fake runner that returns a flat `$2.00` per round, and the director adds
it up correctly. It treats `result.cost_usd` as one round's spend. The real
`RoundRunner` returns a cumulative number. Each side passes its own tests, and
the bug lives in the seam between them, which only an end-to-end run shows.

## What does it mean for a real run?

Treat the ceiling as a fuse that blows early, not a budget. For a run where
each round costs $c in real spend, the director's reported total roughly
doubles per round after the first few, so it reaches your ceiling long before
you have spent it. How early depends on your rounds. My repro is the smallest
case (one coder, one iteration a round, $0.40 flat). With more coders and
iterations per round, each round costs more, and the ceiling arrives in fewer
rounds, but the gap between real and reported spend keeps the same shape.

I have not run this against live coders, so I can't tell you what your
overnight run will show. The mechanism does not depend on what the coder is: the
query sums every event with a `cost_usd`, and both the vendor-CLI path and the
LiteLLM path write their cost onto `ITERATION_COMPLETE` events
([`chi/orchestrator/loop.py:253`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py#L253)).
The same code is in the released wheel: I downloaded `getchi==0.2.0` from PyPI
and the same query and the same `+=` are there.

## What should you do until it is fixed?

- **Do not use the ceiling as your spend control.** If your coders go through
  chi's LiteLLM path, the per-run caps in `budgets:` are the control that
  actually charges spend. How they leak is in
  [where cost caps leak](/blog/limit-llm-api-cost-coding-agents/).
- **Use the ceiling as a tripwire for "something is burning money."** It will
  trip early, which is the safe direction.
- **Read the director's round lines, not the ceiling.** The `Σ $` figure on
  each `round N:` line is inflated after round 0. Add up the `ITERATION_COMPLETE`
  costs in the run's store if you need the real number.
- **Expect the gap to grow with the ceiling.** In my runs, real spend at the
  stop was 80% of a $1 ceiling, 60% of $2, 32% of $5, 16% of $10 and 10% of
  $25. If you want a run to continue, test your ceiling against a scripted
  coder first, as I did.

## Where this falls short

This is a bug in chi, on `main` and in 0.2.0, and I haven't fixed it. The
fix is small (have `RoundRunner` return only the new spend since the last
round, or have the director assign the cumulative value instead of adding it),
but I haven't written or tested it, so don't read that as a promise. My repro
used a scripted coder with a flat injected cost, one iteration per round. I did
not run a live vendor CLI or a LiteLLM model through it. The ceiling also
checks only between rounds, so a single round can still overshoot it, which
is separate from this bug. Known gaps live in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Reproduce it yourself

The director's design is in the [concepts doc](/docs/concepts/) and
[`docs/director.md`](https://github.com/kdpisda/chi/blob/main/docs/director.md).
To try the harness without a key, install chi and run the scripted demo, which
costs nothing and shows the run store these queries read:

```bash
uv tool install getchi
chi run examples/offline.yaml
```

Star the repo if a loop that tells you the truth about its own spend is what
you want from a harness: [github.com/kdpisda/chi](https://github.com/kdpisda/chi).
