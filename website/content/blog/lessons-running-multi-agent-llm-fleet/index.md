---
title: "Lessons From Running a Multi-Agent LLM Fleet by Hand"
description: "Four ways my hand-run multi-agent LLM fleet failed, the mechanism each one became in chi, and an audit of how much of each design actually shipped."
date: 2026-11-15T02:33:42Z
draft: false
category: "Case study"
tags: ["multi-agent", "fleets", "autoresearch", "coding agents", "loop reliability"]
toc: true
newsletter:
  subject: "I wrote four fixes for my agent fleet. Here's what shipped."
  preheader: "The design doc next to the code, one failure at a time, with the parts still missing."
  body: |
    Before chi existed, I ran a fleet of coding agents by hand: Claude and Codex as coders, another model as orchestrator, and a few open models on the side, all pointed at one GPU kernel. It failed in four specific ways, and I wrote each one down.

    Each core part of chi traces back to one of those four failures. But a design doc and a codebase are different things, so I put them side by side and checked how much of each fix actually shipped.

    Short version: the cheap, deterministic parts shipped whole. The parts that needed judgment shipped as advice. One query against my own ledger shows the gap in a single line. [Read the audit](https://getchi.dev/blog/lessons-running-multi-agent-llm-fleet/).
---

Running several LLM coding agents against one problem sounds like parallelism.
In practice it felt like babysitting. These are the lessons from running a
multi-agent LLM fleet by hand on a GPU kernel problem: the four failures I wrote
down at the time, the mechanism each one turned into, and an honest audit of how
much of each design is actually in the code today.

## What failed when I ran a multi-agent LLM fleet by hand?

Claude and Codex were the coders, another model instance acted as orchestrator,
and DeepSeek, GLM and MiniMax ran on the side. The target was a Cholesky kernel
on a B200, scored by a leaderboard. The four pain points are recorded in
[`docs/product-spec-v1.md`](https://github.com/kdpisda/chi/blob/main/docs/product-spec-v1.md)
(lines 12-16), and every core mechanism in chi exists because of one of them:

1. **Re-exploration.** The fleet kept retrying approaches it had already ruled
   out (precision tricks, streams, micro-tuning), because nothing shared
   remembered the failure.
2. **Scarce authoritative evals.** The leaderboard allowed about one submission
   per 30 minutes, and nothing separated cheap local benchmarks from the real eval.
3. **Stalls and give-ups.** Agents hung, looped, or declared themselves done
   too early.
4. **Ad hoc steering.** There was no clean way to redirect the fleet mid-run.

The spec then designed a fix for each. Below, each fix sits next to the code
on `main` today (version 0.2.0), along with what I ran today (November 15, 2026)
to check it. Everything ran offline at $0, on top of `chi run examples/offline.yaml`
(run `offline-demo-19d3cf`, about 3 seconds). Below, `$R` is that run's directory.

## Lesson 1: a dead end needs evidence, and a fuzzy lookup

**Designed.** A negative-results ledger: each ruled-out approach gets an entry
with evidence and a scope. Before working, an agent queries it by embedding
similarity, and a near match blocks the approach unless the agent files a
"distinguishing hypothesis" (spec lines 237-260).

**Shipped.** The ledger, the evidence requirement and the challenge flow are all
there. A prose-only dead end is refused:

```text
$ chi deadend --run-dir $R --agent replay --approach-class reduced_precision \
    --summary "float32 accumulation fails tolerance"
REJECTED: dead-end entries require non-empty --evidence-json      (exit 1)

$ chi deadend ... --evidence-json '{"seed":7,"failure_mode":"correctness"}' --scope "all inputs"
n_4d486bde
```

The lookup is not embeddings. `query_knowledge` is a SQL `LIKE` over the
approach class and summary
([`chi/store/ledger.py`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py),
lines 156-167). Here is what that means in practice:

| Query | Matches `n_4d486bde`? |
|---|---|
| `chi query precision` | yes |
| `chi query float32` | yes |
| `chi query fp16` | no |
| `chi query "lower precision"` | no |

An agent that thinks "fp16" gets back an empty list for an approach the ledger
already ruled out. And nothing blocks it. Active dead ends are listed in each
coder's prompt
([`chi/agents/context.py`](https://github.com/kdpisda/chi/blob/main/chi/agents/context.py),
line 24), and the steering digest adds "do not retry without a distinguishing
hypothesis"
([`chi/orchestrator/steering.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/steering.py),
lines 65-66). Under the director, a class ruled out more than once is labeled
"hard block" in the prompt
([`chi/director/strategy.py`](https://github.com/kdpisda/chi/blob/main/chi/director/strategy.py),
line 39). That is still a prompt, not a gate. I wrote about why scoped,
challengeable dead ends matter in
[why multi-agent systems repeat failed approaches](/blog/multi-agent-system-repeats-failed-approaches/).

## Lesson 2: never pay twice for the same code, and ration the real eval

**Designed.** Two eval tiers. The cheap local proxy runs freely. The
authoritative tier is rationed by a token bucket and spent only on a candidate
that passes correctness, beats the champion by more than `noise_std × margin_k`,
and clears an optional human approval (spec line 300).

**Shipped.** The rationing is close to the design.
[`SubmissionGate`](https://github.com/kdpisda/chi/blob/main/chi/eval/submission.py)
serializes ranked submissions behind a mutex, and defaults to one per 1,800
seconds (lines 36-37), the limit I was working under. Agents run with a
shimmed `popcorn-cli` first on their PATH, so a ranked submit that bypasses the
gate is refused
([`chi/agents/cli_subprocess.py`](https://github.com/kdpisda/chi/blob/main/chi/agents/cli_subprocess.py),
line 76). Auto-submit is off by default (`auto_submit: False`,
[`chi/config.py`](https://github.com/kdpisda/chi/blob/main/chi/config.py) line 117).

The margin is a fixed percentage over the last submission, `promote_margin_pct`
(default 0.5%, clamped to at least 0.1%,
[`chi/eval/autosubmit.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/autosubmit.py)
line 48). It is not scaled by measured noise.

The cheap win is the dedup cache. Every candidate is hashed after normalizing
line endings and trailing whitespace, and a known hash returns its stored result
without re-running anything
([`chi/eval/runner.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py),
line 71). Re-evaluating the champion's exact source today:

```text
$ chi eval --run-dir $R --agent replay
{"code_hash": "sha256:3b0056…", "correct": true, "score_value": 0.0725…, "detail": "cached", "cached": true}
```

Then I appended one comment line to the same code:

```text
{"code_hash": "sha256:b19b9f…", "correct": true, "score_value": 0.0646…, "detail": "", "cached": false}
```

The hash is exact, so a comment counts as a new candidate and gets a full eval.
It also scored about 11% "better" than the identical code it copies, which is
benchmark noise, and `chi champion $R` now reports the commented copy as the
run's champion. That is the failure from
[is your agent's benchmark improvement just noise?](/blog/agent-benchmark-improvement-is-noise/),
showing up again in a check I ran for a different reason.

## Lesson 3: stalls are caught by counting, not by asking

**Designed.** Heartbeats with a watchdog that probes and kills a silent agent,
no-progress detection, and repeated-diff detection (spec lines 314-317).

**Shipped.** The counting parts. The
[watchdog](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/watchdog.py)
makes no model call. It mutates the prompt after 5 iterations with no new eval
and kills at 10, and it mutates after the same candidate hash comes back 3 times
and kills at 6 (lines 50-94, defaults `repeat_k=3` and `eval_recency_iters=10`
in `chi/config.py` lines 30-31). A hung agent process is killed by the
per-iteration timeout, 600 seconds by default (line 33). The details are in
[how to stop an AI coding agent from looping](/blog/stop-coding-agent-looping/).

Heartbeats are written every 30 seconds to the `agents` table
([`chi/agents/protocol.py`](https://github.com/kdpisda/chi/blob/main/chi/agents/protocol.py),
lines 75-81), but nothing in `chi/` reads `last_heartbeat_at` to decide anything.
The heartbeat thread runs in the harness process, so it would keep beating while
the agent subprocess hangs anyway. The timeout does that job, not the heartbeat.

## Lesson 4: steering is a file, read every iteration

**Designed.** A human edits `steering.md`, and the orchestrator re-reads it
between iterations, reprioritizes the task queue, and injects the hint into
coder prompts (spec line 63).

**Shipped.** The re-read and the injection.
`steering.refresh()` runs at the top of every coder iteration
([`chi/orchestrator/loop.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py),
line 212) and logs a `STEER_UPDATE` event when the file's hash changes. From the
CLI:

```text
$ chi steer $R "stop micro-tuning; try a numpy path"
ok
$ tail -2 $R/steering.md
## §op 2026-11-15T02:38:36.335092Z
stop micro-tuning; try a numpy path
```

What it does not do is reprioritize work. The directive is text in each coder's
prompt, and the model decides what to do with it.

## What's the pattern across all four?

| Failure | Shipped whole | Shipped as advice, or missing |
|---|---|---|
| Re-exploration | Evidence-required ledger, challenges | Fuzzy lookup, hard block |
| Wasted evals | Exact-hash cache, rationed gate | Noise-scaled margin |
| Stalls | Counting watchdog, iteration timeout | Heartbeat-based kill |
| Steering | Hot-reloaded file in every prompt | Queue reprioritization |

The deterministic parts (hashes, counters, mutexes, timeouts) shipped intact.
Anything that needed a similarity judgment or the power to overrule an agent
shipped as text in a prompt. The text still helps, but it is the agent's
judgment you are trusting, not a mechanism.

## Where this falls short

This audit compares a design doc to code. It does not measure how often a fleet
actually retries a ruled-out approach under the LIKE lookup, because that needs
real model runs that I haven't done for this post. The four pain points are my
own record of one fleet on one problem. Other people's fleets fail in other
ways, and chi has a public list of the gaps I know about in
[`docs/autoresearch-gap-analysis.md`](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).
None of the missing pieces above are promised for a release.

If you run your own fleet, the cheapest lesson is the first row of that table.
Write down every dead end with evidence somewhere every agent reads it, before
you write anything clever. To see the shared store, cache and watchdog working
on your own machine without an API key, start with
[your first autoresearch loop, no API key needed](/blog/first-autoresearch-loop-no-api-key/).
