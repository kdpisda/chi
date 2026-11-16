---
title: "Coding Agent Loop Without an LLM Planner: What You Lose"
description: "Should your autonomous coding agent loop have an LLM planner? I ran chi's director with no brain, a stub brain and a failing one. Here's what each changed."
date: 2026-11-16T02:33:57Z
draft: false
category: "Guide"
tags: ["director", "loop reliability", "autoresearch", "coding agents", "steering"]
toc: true
newsletter:
  subject: "A failed CLI call became my agent's new strategy"
  preheader: "A second kind of failure stopped the loop with nothing on screen."
  body: |
    Most agent loops put an LLM planner on top of the coders: something that reads the results and says what to try next. I wanted to know how much of chi's director actually depends on that model call, so I ran it at $0 three ways: with no planner, with a stub planner, and with a planner whose CLI fails.

    The no-planner director still classifies rounds, lists dead ends and steers the fleet. The planner adds two calls, and in my flat scripted run it fired exactly once.

    The failing cases were the interesting part. One error message became a coder's "new direction" in the steering file. The other stopped the director with nothing on screen. [Read the guide](https://getchi.dev/blog/coding-agent-loop-without-llm-planner/).
---

You put an LLM planner on top of your coding agents because the loop got stuck
and someone has to decide what to try next. Then you pay for a model call every
round, and you can't easily tell whether it helped. If the planner were gone,
which parts of the loop would still work?

I asked that about chi's director, the loop that runs a coder fleet in rounds on
its own. Short answer: an autonomous coding agent loop without an LLM planner
still runs, classifies every round, lists dead ends and tells the coders what
to beat. The planner (chi calls it the brain) adds two things: a research call
when the run is stuck, and a new strategy label for one coder. I ran the
director at `$0` on 2026-11-16 with no brain, a stub brain and a failing brain to
see what each one changes. The failing brain was the most useful run.

## What does the director do without an LLM planner?

Everything that decides. The director's rules decide the round state and the
model's opinion doesn't override them. The file header says so:
"The brain's qualitative read is advisory; these rules DECIDE the state"
([`chi/director/review.py`](https://github.com/kdpisda/chi/blob/main/chi/director/review.py)).

Each round, with or without a brain:

1. Runs a short fleet slice (2 iterations by default).
2. Builds a digest from the run store: champion score, dead approach classes
   from the negative ledger, and how many classes are new.
3. Classifies the round `improving`, `plateaued` or `stuck` with fixed rules. I
   walked through those rules in [detecting a plateau without an
   LLM](/blog/detect-agent-loop-plateau-rules/).
4. Verifies an apparent win with NoiseGuard, a median-of-N re-benchmark, and
   runs the holdout check when the problem has one.
5. Rewrites the director block of `steering.md`, the file every coder
   re-reads before each iteration.

Apart from the coders themselves in step 1, steps 1 to 4 never call a model.
Step 5 calls one only on plateaued or stuck
rounds, and only if a brain is configured
([`chi/director/strategy.py:54`](https://github.com/kdpisda/chi/blob/main/chi/director/strategy.py)).

Here's the director block after a brain-less run of the offline demo pack,
three scripted coders, 12 seconds:

```text
# Director directives (auto-generated each round)

Round 0: state = plateaued.
Champion to beat: 0.10118699998429292.
```

That is the whole block. It has no dead classes because the scripted coders
don't record any. In a real run, a class an agent ruled out with `chi deadend`
shows up under `## DEAD — do not retry`, marked as a hard block once it has
been ruled out twice (`strategy.py:36-43`). How that ledger works, and where
"do not retry" is only advice, is in [why multi-agent systems repeat failed
approaches](/blog/multi-agent-system-repeats-failed-approaches/).

## What does the brain add?

Two model calls, both through the vendor CLI you set as `operator_cli` in
`~/.config/chi/config.yaml`. The session resolves it with `shutil.which`; if the
CLI isn't on `PATH`, the brain is `None` and both calls are skipped
([`chi/session/engine.py:519-532`](https://github.com/kdpisda/chi/blob/main/chi/session/engine.py)).

| Call | When it fires | What it writes |
|---|---|---|
| Research | Stuck round, and the round produced new information | `## Research findings`: the prompt asks for 3 to 5 techniques; the reply is cut at 2,000 chars ([`research.py`](https://github.com/kdpisda/chi/blob/main/chi/director/research.py)) |
| New direction | Plateaued or stuck round, same condition | `## New direction`: one kebab-case label for one coder (`strategy.py:54-70`) |

"New information" matters more than it looks. The director only spends a brain
call when the slice benchmarked something new or the champion or the
dead-class list changed (`loop.py:127-137`). That guard stops a call storm when
every coder fails instantly. It also means a fleet that only produces
duplicates never gets research.

I ran the real `DirectorHandle` (the object the session starts) with three
scripted coders and a stub brain that logs every call, for 30 seconds:

```text
[   1.5s] round 0: plateaued · best 0.1261620000008179 · Σ 2 benches $0.00
[   1.6s] round 1: stuck · best 0.1261620000008179 · Σ 2 benches $0.00
...
[  26.9s] round 6: stuck · best 0.1261620000008179 · Σ 2 benches $0.00
brain calls: {'strategy': 1}
```

Seven rounds, six of them stuck, one brain call, zero research. The scripted
coders replay the same candidates every slice, so after round 0 nothing was
new. Real coders usually write different code each iteration, so the guard
would pass more often, but a fleet that keeps resubmitting the same file gets no
research at all.

To see the calls fire every round, I swapped the fleet slice for a stub that
reports one new benchmark per round, on the same store. Then the brain was
called 15 times in 8 rounds: 8 strategy calls and 7 research calls (round 0
was plateaued, so no research).

## Which coder gets the new direction?

Always the same one. The strategist picks `sorted(strategies)[0]`, the coder
whose id sorts first, and the code comment says why: "deterministic pick
without per-coder scores" (`strategy.py:68`). The [concepts
page](/docs/concepts/) calls it "the weakest coder"; the code has no per-coder
scores to find one.

Same 8-round replay, coders named `coder-c`, `coder-a`, `coder-b`:

```text
final strategies: {'coder-c': 'vectorize', 'coder-a': 'idea-8', 'coder-b': 'c-extension'}

## New direction
- coder-a: try `idea-8`
```

`coder-a` was reassigned 8 times. The other two never moved. Two more details
from the code:

- The label lives only in the director's own map. Each slice still builds the
  coder's task from the problem's strategy list or the coder config
  ([`chi/orchestrator/loop.py:412`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py)).
  The new direction reaches coders only as that line in `steering.md`.
- `steering.md` is one file for the whole fleet, so every coder reads
  "coder-a: try ..." and only `coder-a` is addressed.

If you want rotation, name your coders so the one you'd sacrifice sorts first,
or steer the others yourself. [Steering a running agent](/blog/steer-autonomous-coding-agent-mid-run/)
covers how a typed directive survives the director's rewrites.

## What happens when the planner call fails?

This is the part I'd want to know before leaving a loop running. The brain is a
subprocess: `claude -p <prompt>` or `codex exec <prompt>`, 240-second timeout,
returning stdout, or stderr when stdout is empty
([`chi/session/operator.py:475-485`](https://github.com/kdpisda/chi/blob/main/chi/session/operator.py)).
The two calls handle failure differently.

**A CLI that exits with an error becomes advice.** I put a fake `claude` on
`PATH` that prints `fake-cli: not logged in (exit 1)` to stderr and exits 1,
then started the director through the session engine. The director kept
running. Its steering block said:

```text
## New direction
- replay: try `fake-cli: not logged in (exit 1)`
```

The runner returns stderr as the answer, and the strategist takes the first
line as the label. Research would do the same with the whole error text,
since it only falls back to empty on an exception or an empty reply
(`research.py:42-46`).

**A call that raises stops the director, quietly.** Research wraps its call in
`try/except`. The strategist doesn't (`strategy.py:65`). A
`subprocess.TimeoutExpired` from a CLI that hangs past 240 seconds, or any other
exception, leaves `Director.run`. The handle stores the message in its `error`
field and the thread ends
([`chi/session/director_runner.py:150-152`](https://github.com/kdpisda/chi/blob/main/chi/session/director_runner.py)).
I made the brain raise and polled the session for 10 seconds:

```text
['director starting on optimize_function until you /stop it; ...']
status: ['director stopped run=optimize_function-ed85ca · Σ 2 benches $0.00']
handle.error: simulated vendor CLI timeout
```

No round line, no warning. The status says `stopped` with no reason. The
`halted_reason` that the dead-evaluator, target and ceiling stops set is empty
here, and I found no code in `chi/session/` or `chi/tui/` that reads the
handle's `error`. With a real CLI, the timeout path would also take the full
240 seconds before it fires.

## Should you run without a planner?

Here's what I'd do, based on these runs:

- **Run without one** when the problem has a fixed list of strategies, or when
  you mostly want the rules: plateau detection, NoiseGuard, dead-class blocks,
  the self-stops. Nothing in that list needs a model.
- **Add one** when the fleet runs out of ideas and you want outside techniques
  injected on stuck rounds. That's the one thing only the brain does.
- **Before you add one,** run `claude -p "say ok"` (or `codex exec`) in the
  same shell you start `chi` from, and confirm it answers. A logged-out CLI
  doesn't fail loudly; it turns into a strategy.
- **Check on it.** If `/director` says `stopped` and you didn't stop it and
  there's no `halted` line, suspect the brain call.

## Where this falls short

- **No real model.** Every brain here was a stub or a fake CLI. I didn't
  measure whether real research helps a real run, or what it costs.
- **Small runs.** One problem pack, scripted coders, runs of 10 to 30 seconds.
  The 8-round replay replaced the fleet slice with a stub, which I said
  above.
- **Known issues, not fixed here.** The fixed coder pick, the unwrapped
  strategist call and the unreported `error` are behaviors of chi 0.2.0 as of
  today. The "promising bases" block in the docs never appears in a real run:
  the only caller of `mark_near_miss` is a test. The director also has no
  saturation stop, which is gap 6 in the [public gap
  analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Try it

The director's round anatomy, the steering file and the supervision commands
(`/director`, `/stop`, typing to interject) are on the [concepts
page](/docs/concepts/). Read that before you decide whether to set
`operator_cli`.
