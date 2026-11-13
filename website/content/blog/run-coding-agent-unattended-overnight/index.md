---
title: "Run a Coding Agent Unattended Overnight: What Stops It"
description: "Before you run a coding agent unattended overnight with a budget, know what ends the loop. I ran chi's director at $0 and logged each way it stops, and one gap."
date: 2026-11-13T02:35:16Z
draft: false
category: "Guide"
tags: ["director", "loop reliability", "budgets", "autoresearch", "coding agents"]
toc: true
newsletter:
  subject: "My agent loop was stuck for 12 rounds. Nothing stopped it."
  preheader: "The watchdog fired 10 times. The loop above it kept going until I typed stop."
  body: |
    Before you leave a coding agent loop running overnight, the question that matters is what will end it. I ran chi's autonomous director at $0 with a scripted coder and logged every stop path the code has: your stop command, a target score, a cost ceiling, a dead evaluator, and closing the terminal.

    One case none of those cover: a loop that is stuck but still healthy. In my run the per-coder watchdog killed the coder 10 times, and the director kept starting new rounds until I stopped it by hand.

    The post has the commands, the output, and a short checklist for what to set before you walk away. [Read the guide](https://getchi.dev/blog/run-coding-agent-unattended-overnight/).
---

You want to start a coding agent loop at 11pm, go to bed, and find a better
candidate in the morning. To run a coding agent unattended overnight with a
budget, the setting that matters most is not the model or the prompt. It's the
answer to one question: what ends the loop when nobody is watching?

This guide answers that for chi's director, the part of chi that runs the coder
fleet in rounds on its own. I read the stop paths in the code and ran the
director at `$0` on 2026-11-13 to watch each one. One case has no stop at all,
and it's the one most likely to happen at 3am.

## How do you run a coding agent unattended overnight with a budget in chi?

You give the director one sentence. Bare `chi` opens the session, and you type
something like:

```text
› keep improving problems/optimize_function on its own. get it under 0.2 then stop, don't spend over $5
```

The session's operator turns that into a `start_director` call with a
`target_score` and a `cost_ceiling_usd`. The tool description tells it to map
"get it under 500" to `target_score: 500` and "don't spend over $5" to
`cost_ceiling_usd: 5`
([`chi/session/operator.py`](https://github.com/kdpisda/chi/blob/main/chi/session/operator.py),
lines 92–106). Then the director runs rounds until something stops it.

Each round runs a short slice of the fleet (2 iterations by default),
builds a digest from the run store, classifies the round as `improving`,
`plateaued` or `stuck`, and rewrites the steering file the coders read
([`chi/director/loop.py`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py)).
If you want the full round anatomy, the [concepts page](/docs/concepts/) has it.
This post is only about the exit.

The operator needs a model to parse your sentence, so my runs skipped it. I drove
the same `DirectorHandle` the session uses, with the offline demo's scripted
coder (it replays canned candidates and really evaluates them) and no brain:

```python
h = DirectorHandle(fleet, runs, brain_fn=None, emit=emit,
                   target_score=target, cost_ceiling_usd=ceiling)
h.start(); h.ready.wait()
```

## What stops the director?

Five things, and I checked each in the code. The three self-stops (target,
ceiling, dead evaluator) are checked after a round is recorded, so the round
that crosses the line still shows up in the log.

| Stop | Trigger | Where |
|---|---|---|
| `/stop` or a bare "stop" | You type it. Halts at the next round boundary | `chi/session/engine.py` `_cmd_stop`; `loop.py:89` |
| Target score | Best score meets your target, unless the holdout check says the champion is overfit | `loop.py:72-81`, `176-182` |
| Cost ceiling | The director's running cost total reaches your ceiling | `loop.py:183-190` |
| Dead evaluator | 3 rounds in a row with no scored candidate | `loop.py:59-62`, `164-173` |
| Process exit | The director is a daemon thread inside the session | `chi/session/director_runner.py:155` |

Here is the target stop on a real run. Target `0.2`, minimize:

```text
[   1.7s] round 0: plateaued · best 0.1260049999984858 · Σ 2 benches $0.00
[   1.7s] ✓ director stopped: target reached: 0.1260049999984858 meets 0.2
```

It stopped in round 0, 1.7 seconds in. That's correct behavior here, but look at
what didn't happen. The median-of-N re-check (chi's NoiseGuard, which
re-benchmarks an apparent win a few times before believing it) only runs on an
`improving` round that has a previous best (`loop.py:103-104`). Round 0 has no
previous best, so a target hit in round 0 is one unverified sample. I wrote up
that case in [an agent loop that stops on one lucky benchmark
run](/blog/agent-loop-stops-lucky-benchmark-score/). If your target is close to
the baseline, set it a little past where you'd be happy.

## What happens when the loop is stuck and nobody is watching?

Nothing stops it. This is the case to plan for.

I ran the director with no target and no ceiling, and let it go for 60 seconds.
The scripted coder found its best candidate in round 0. After that it had
nothing new to offer: it replays its script from the first candidate each slice,
so every later iteration produced a duplicate. That's a decent stand-in for a
fleet that has run out of ideas.

```text
[   1.6s] round 0: plateaued · best 0.1096189999998387 · Σ 2 benches $0.00
[   1.6s] round 1: stuck · best 0.1096189999998387 · Σ 2 benches $0.00
[   6.7s] round 2: stuck · best 0.1096189999998387 · Σ 2 benches $0.00
...
[  57.0s] round 12: stuck · best 0.1096189999998387 · Σ 2 benches $0.00
[  60.7s] -> request_stop()
```

Thirteen rounds, twelve of them `stuck`, no new scored candidate after round 0.
The run store shows what happened underneath. The per-coder watchdog (the check
that kills a coder whose candidate stops changing) fired 10 times, with reasons
like `candidate unchanged 12 consecutive iterations`, and 10 slices ended
`stalled`. Each time, the director classified the round, slept 5 seconds
(`loop.py:191-194`), and started another slice. It ended only when my script
called `request_stop()`.

That's by design. The director is documented as run-until-stopped. The watchdog
stops a coder. Nothing stops the director because the metric stopped moving.
The dead-evaluator rule doesn't apply either, because a champion exists, so
every round has a best score.

In my run that cost nothing: the scripted coder spends `$0`, and a duplicate
candidate gets the cached score from the store instead of a new benchmark
(`chi/eval/runner.py:70-78`). With real coders, every new
slice launches them again, and each iteration is a model call you pay for. I
didn't measure that cost, so I won't put a number on it. The lack of a
saturation-aware stop is gap #6 in chi's
[public gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md#6-no-saturation-aware-stop),
which cites a practitioner's loop that ran 11 days on an already-solved
benchmark.

## Does a cost ceiling make an overnight run safe?

It makes it bounded, with two caveats you should know before relying on it.

First, the ceiling only counts spend the store sees. A CLI coder that doesn't
report cost adds `$0`. I covered that in [how to limit LLM API cost for coding
agents](/blog/limit-llm-api-cost-coding-agents/).

Second, in chi 0.2.0 the director's running total counts some spend twice, so
the ceiling fires early. In an earlier `$0` repro, with a scripted coder patched
to report $0.40 per iteration, it halted at $1.20 of logged spend on a $2.00
ceiling. The details are in [why your agent loop's cost
ceiling stops early](/blog/agent-loop-cost-ceiling-stops-early/). Early is the
safe direction for your money, but it can cost you most of the night.

There's also a per-slice budget. A session-started director gives the fleet your
`default_budget_usd` (5.0 unless you changed it, `chi/userconfig.py:114`), and
each slice builds a fresh budget tracker (`chi/orchestrator/loop.py:488`). So
that cap applies per slice, not to the whole night. The director's cost ceiling
is the only whole-run cap.

## Does closing the terminal stop it?

Yes. The director is an in-process thread
([`docs/director.md`](https://github.com/kdpisda/chi/blob/main/docs/director.md)
says the same, and lists a detached director as planned). If you try to quit
while it runs, the UI refuses and prints `a director is running — /stop it first
(exiting kills it)`. The plain REPL lets a second Ctrl-D through. The concepts
page currently says "Ctrl-D detaches, the loop keeps running". In the 0.2.0
code it doesn't, so trust `docs/director.md` on this one.

For an overnight run, start `chi` inside `tmux` or `screen` on a machine that
won't sleep. A dropped SSH session then leaves the director running.

## What does a director do without a brain?

It still runs, classifies and steers, but it can't research or invent a new
strategy. The research step and the "new direction" for a coder each make one
model call, through the vendor CLI set as `operator_cli` in
`~/.config/chi/config.yaml` (`chi/session/engine.py:519-532`). With no
`operator_cli`, both degrade to nothing. My runs had no brain, so the only
steering the coders ever got was the deterministic part: a header with the round
state and the champion to beat. If you want the stuck rounds
to trigger research overnight, set `operator_cli` before you start.

## What to set before you walk away

1. A **cost ceiling**, knowing it fires early and only sees reported spend.
2. A **target**, set a little past what you'd accept, since a round-0 hit isn't
   re-checked.
3. A **check-in time**. Nothing ends a stuck-but-healthy loop, so look at the
   round line in the morning: a long run of `stuck` with a flat `best` means you
   paid for nothing after that point.
4. **`tmux` or `screen`**, on a machine that stays awake.
5. Optionally, **`operator_cli`**, so stuck rounds research instead of idling.

In the morning, `/director` or `/resume <run_id>` replays every round from the
store (my 60-second run left 13 `DIRECTOR_ROUND` events), and
`chi status <run_dir>` prints the run row and recent events. You can still steer it mid-run by typing while it works.

## Where this falls short

- **Small test.** One scripted coder, one problem pack, three short runs. I
  didn't run real models, so I have no cost or timing numbers for a real night.
- **No saturation stop.** A stuck director runs until you stop it, a ceiling
  hits, or the process dies.
- **Early ceiling.** The double-count means the ceiling isn't a precise budget
  yet.
- **Session-bound.** No detached mode. Your terminal multiplexer is the daemon.

## Read the director's moving parts

The [concepts page](/docs/concepts/) explains the director, the watchdog, the
store and steering in one place. Read its director section before you plan your
first overnight run.
