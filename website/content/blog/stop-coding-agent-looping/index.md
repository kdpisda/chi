---
title: "How to Stop an AI Coding Agent From Looping"
description: "An agent that says \"final submission\" seven times isn't done. Here's the code that kills a looping or stalled coding agent without asking it."
date: 2026-09-27T02:33:19Z
draft: false
category: "Deep dive"
tags: ["coding agents", "autoresearch", "loop reliability", "fleets", "watchdog"]
toc: true
newsletter:
  subject: "It said \"final submission\" seven times. It wasn't."
  preheader: "The fix isn't a smarter prompt. It's a rule that never asks the model."
  body: |
    One agent in an early hand-run fleet looped for about 30 hours. It cycled three identical "optimizations," posted "final submission" seven times, and never produced one real benchmark number. Nothing crashed. Nothing errored. It just never stopped.

    The instinct is to ask the model if it's stuck, or add a bigger prompt telling it not to loop. Neither works, because a model that's looping is also the one telling you, confidently, that it's finished. So chi's fix asks nothing: nine lines of state and two counters that watch what the agent produces, not what it says.

    The post has the exact rules, a live run of the code against the anecdote above, and the two failure modes it still can't catch.
---

An AI coding agent that announces "final submission" is not necessarily
finished. An early hand-run fleet behind chi had one agent loop for about 30
hours: it cycled three identical "optimizations" and posted seven near-identical
"final submission" messages, and never produced a single benchmark datapoint
([`docs/superpowers/specs/2026-07-25-chi-v1-design.md:26`](https://github.com/kdpisda/chi/blob/main/docs/superpowers/specs/2026-07-25-chi-v1-design.md#L26)).
Nothing threw an exception. The transcript, read on its own, looked like
progress.

That's the actual problem behind "how do I stop an AI coding agent from
looping": the agent's own account of its status is not a signal you can trust,
because a model that's stuck is also the one narrating confidence. The fix
that shipped in chi doesn't try to make the model more self-aware. It's a
small, deterministic watchdog that never calls an LLM and only looks at two
things: whether an iteration produced a real evaluation, and whether the code
it evaluated is the same as last time.

## Two signals, no model call

[`chi/orchestrator/watchdog.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/watchdog.py)
is a plain Python class with four instance attributes: three counters and the
last candidate hash it saw. Its docstring says what it's for:
"Deterministic, zero-LLM watchdog." Every finished iteration feeds it two
facts — how many new evals that iteration produced, and the hash of the
candidate code it evaluated — and it returns one of three verdicts: `ok`,
`mutate` (steer the agent toward a smaller, different edit), or `kill` (stop
this agent and free its task).

Two independent rules drive that verdict:

- **Eval recency.** If an agent goes `eval_recency_iters` iterations (default
  `10`, [`chi/config.py:31`](https://github.com/kdpisda/chi/blob/main/chi/config.py#L31))
  without a single new eval datapoint, it's killed. Halfway there, at 5
  iterations, it gets a `mutate` nudge first: "no eval datapoints recently —
  produce measured results now." A CLI that errors on every call, or an agent
  that edits and re-edits without ever running the benchmark, hits this even
  if its commentary reads as busy.
- **Repeated diff hash.** If the evaluated candidate's hash repeats
  `repeat_k` times in a row (default `3`), the agent gets a `mutate`: "try a
  different approach." If it repeats `2 * repeat_k` times, it's killed:
  "candidate unchanged N consecutive iterations." This is the rule that
  catches the three-identical-optimizations loop directly — an agent can keep
  producing real evals and still be going nowhere if it's re-submitting the
  same code.

A third, smaller rule fires immediately rather than waiting for a streak: if
an iteration times out with zero evals, the watchdog mutates on the very
first occurrence, before either counter reaches its threshold
([`watchdog.py:72-80`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/watchdog.py#L72-L80)).
A timeout with nothing measured is a strong enough signal that the edit was
too large; there's no reason to wait for two more of them.

## Running it against the actual anecdote

The rules are plain enough to run outside a fleet. I fed the watchdog the
shape of the 30-hour loop — a coder that keeps "submitting" without ever
producing an eval — using chi's own `Watchdog` class and its default policy
(`repeat_k=3`, `eval_recency_iters=10`):

```python
from chi.config import PoliciesCfg
from chi.orchestrator.watchdog import Watchdog

w = Watchdog(PoliciesCfg())
same_hash = "candidate_abc123"

for i in range(1, 11):
    v = w.observe_iteration(new_evals=0, candidate_hash=same_hash, note="final submission")
    print(f"iteration {i:2d}: action={v.action:6s} reason={v.reason}")
    if v.action == "kill":
        break
```

Run today, that prints:

```
iteration  1: action=ok     reason=
iteration  2: action=ok     reason=
iteration  3: action=mutate reason=candidate unchanged 3 times — try a different approach
iteration  4: action=ok     reason=
iteration  5: action=mutate reason=no eval datapoints recently — produce measured results now
iteration  6: action=kill   reason=candidate unchanged 6 consecutive iterations
```

Two things are worth noticing. First, the repeat-hash rule fires the kill at
iteration 6, well before the eval-recency rule would fire at iteration 10 —
whichever rule trips first wins, so a looping-but-otherwise-idle agent is
caught faster than the recency cap alone would catch it. Second, iteration 4
comes back `ok`: a `mutate` verdict doesn't reset the counters, it's advisory
steering, so the streak that started at iteration 1 just kept counting toward
the kill. The full test suite in
[`tests/test_watchdog.py`](https://github.com/kdpisda/chi/blob/main/tests/test_watchdog.py)
pins eight of these scenarios, including a case where a genuine eval resets
both streaks; all eight passed when I ran them today.

## How it's wired into a real run

The class above is deliberately dumb — it has no idea what a "run" or a
"coder" is. The wiring that makes it matter lives in
[`chi/orchestrator/loop.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py):

- After every iteration, `watchdog.observe_iteration(...)` gets the real
  evals-run count and the hash of the candidate the coder actually
  *evaluated* — not just what's on disk. That distinction matters: coders
  revert `candidate.py` to the current champion after a losing benchmark, so
  the file hash alone would look unchanged every round and would wrongly flag
  an agent that's exploring a new candidate each time it loses
  ([`loop.py:235-240`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py#L235-L240)).
  The watchdog tracks the store's record of what was scored, not the
  filesystem.
- A `kill` verdict emits a `WATCHDOG_KILL` event, releases the agent's task
  back to `pending`, and marks it `stalled`
  ([`loop.py:276-282`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py#L276-L282)).
  Nothing crashes the fleet; one dead coder doesn't take down the run.
- Under chi's [director](/docs/concepts/#the-director), the fleet runs in
  short slices, and a fresh watchdog built per slice would never accumulate
  enough iterations to hit its own thresholds. `_seed_watchdog` re-derives
  the counters from the run's own event history before each slice starts,
  and `watchdog.preflight()` can kill a coder before it runs a single new
  iteration if its trailing history already crossed the eval-recency cap
  ([`loop.py:148-201`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py#L148-L201)).
  Without that seeding step, slicing the run into rounds would quietly
  disable the watchdog.

## Where this doesn't help

- **It only sees eval count and code hash.** An agent that changes a comment
  or a variable name every iteration produces a new hash each time and never
  trips the repeat-hash rule, even if the actual logic hasn't moved. The
  watchdog isn't reading diffs semantically.
- **A real number is still "progress" to it**, even a tiny or noisy one. The
  watchdog doesn't judge whether an eval was any good — that's a separate
  concern, the [noise gate](/docs/concepts/) that re-runs apparent wins
  before they count. An agent that keeps producing real-but-flat evals will
  run to `max_iterations`, not get killed.
- **The defaults are global, not per-problem.** `repeat_k=3` and
  `eval_recency_iters=10` live in one `policies:` block in the fleet config;
  a problem where a legitimate iteration takes far longer than usual needs
  those raised by hand, or it'll get killed for being slow rather than
  stuck.

None of that makes the two rules useless — they turned an unbounded, unnoticed
30-hour loop into a kill at iteration 6 of a bounded config, using two
numbers and a hash comparison, for zero additional LLM calls. That's a
narrow, cheap backstop, not a general stuck-detector, and it's meant to be
one.

## Try it on a real run

The watchdog runs on every `chi run`, with no flag to turn it on:

```sh
uv tool install getchi
chi run examples/offline.yaml   # $0, no API key, to see the full loop first
```

[Concepts](/docs/concepts/#the-watchdog) covers where the watchdog sits next
to the director, the blackboard store and steering, and
[Getting started](/docs/getting-started/) has the `policies:` block if you
need to change `repeat_k` or `eval_recency_iters` for a slower problem.
