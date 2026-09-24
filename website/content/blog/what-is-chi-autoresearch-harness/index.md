---
title: "What is chi? An autoresearch harness for fleets of coding agents"
description: "chi points a fleet of LLM coding agents at any problem with a programmatic evaluator and lets it iterate unattended. Here's what it does, and why each part exists."
date: 2026-09-24T01:00:00Z
category: "Deep dive"
tags: ["autoresearch", "coding agents", "multi-agent", "LLM evaluation"]
toc: true
---

Autoresearch is a simple loop: an agent edits code, an evaluator scores it, the
best version is kept, repeat. Anyone can build that loop in an afternoon. Running
it for days, across several models, without it wasting money or fooling you, is
the hard part. That's what chi is for.

chi (χ, pronounced "kai") is an open-source, Apache-2.0 **autoresearch harness**.
You give it a problem with a programmatic evaluator (a build, a correctness
check, and a score) and it runs a fleet of coding agents that keeps improving
the answer, round after round, until you stop it.

## What chi needs from you: an evaluator

chi doesn't care what the problem is about. It needs three things it can run:

- a **candidate** file the agents are allowed to edit,
- a **correctness** command, a hard gate that runs against held-out seeds whose
  reference outputs the agents never see,
- a **benchmark** command that prints a score, plus which direction is better.

That lives in a `problem.yaml`. Here's the one for the bundled
`optimize_function` problem, which asks the fleet to speed up a prefix-sum function:

```yaml
name: optimize_function
candidate: candidate.py
entrypoints:
  correctness: "{python} check.py {candidate} --seed {seed}"
  benchmark: "{python} bench.py {candidate}"
score:
  metric: runtime_ms
  direction: minimize
  repeats: 5
correctness:
  seeds: [11, 27, 43]
  tolerance: 1.0e-6
```

If you can write a script that says "correct or not" and another that prints a
number, chi can run a fleet against it. See
[Problems & evaluators](/docs/problems/) for the full format.

## Why a harness, not just a loop

chi's design comes from a postmortem of running a multi-model agent fleet by
hand. That fleet failed in four ways, again and again:

1. It **re-explored dead ends**: the same ruled-out trick, tried for the fifth time.
2. It **wasted scarce authoritative evals** on changes that were only noise.
3. It **stalled silently**: agents that looped for hours while reporting progress.
4. It had **no clean way to be steered** once it was running.

Every mechanism in chi exists to remove one of those.

## The mechanisms

### A blackboard store instead of agent chat

Agents never talk to each other directly. Every iteration writes to an enforced
store, SQLite plus append-only JSONL, keyed by the hash of the candidate's code.
Re-proposing a candidate someone already tried is a lookup, not a re-run. Any
agent can be respawned with a fresh context built from the store alone, which is
chi's answer to context rot.

### A negative-results ledger

A ruled-out approach class is recorded with its evidence: the failure mode, the
seed, the size of the error. The ledger feeds a hard "do not retry" block in the
fleet's steering, so dead ends stay dead. `chi ledger --negative` shows you
which paths are closed and why.

### A deterministic watchdog

No model is asked whether it's stuck, because models are bad at answering that.
A zero-LLM monitor watches **eval recency** (an agent that has gone 10
iterations without producing a new evaluation is stalled, whatever its
transcript says) and **repeated diff hashes** (an agent emitting the same
candidate is looping). Either one gets the agent killed and respawned from the
store.

### A noise guard and a holdout gate

Most "improvements" in an agent loop are measurement noise. The NoiseGuard
re-benchmarks an apparent win and only counts it if the median clears the
promote margin. The optional [holdout gate](https://github.com/kdpisda/chi/blob/main/docs/holdout.md)
then asks a second question: does the win transfer to a workload the agents
never saw? A champion that only got faster on the benchmark's exact input is
flagged `overfit`, and it can't satisfy a target score.

### An autonomous director

You give one goal, for example *"get it under 0.001 ms, then stop"*, and the
director runs the fleet in rounds:

1. **Run** a bounded slice of iterations.
2. **Review** a deterministic digest built from the store.
3. **Classify** the round as `improving`, `plateaued` or `stuck` using explicit
   rules. The LLM's opinion is advisory; the rule decides, so the loop can't
   talk itself in circles.
4. **Research** the web, but only when stuck, for techniques that are genuinely different.
5. **Steer** by rewriting `steering.md`, which the coders hot-reload between iterations.

You can type at any time to add a directive to the next round, detach and
`/resume` later, or `/stop`. One rule survives full autonomy: **ranked
leaderboard submissions stay manual.** chi surfaces the verified result, and you
decide whether to submit it.

## A heterogeneous fleet

Different models do well on different parts of a problem, so a chi fleet mixes
them on purpose. Coders run behind small adapters: vendor CLIs such as
`claude`, `codex` and `grok` in headless or structured-stream mode, or any
LiteLLM-routable model (Anthropic, OpenAI, DeepSeek, GLM, MiniMax, …) in a
tool loop. Budgets are hard caps, per run and per role.

```yaml
run_name: toy
problem: problems/optimize_function
budgets:
  total_usd: 2.0
  per_role_usd: { coder: 1.5 }
coders:
  - { id: c1, model: anthropic/claude-sonnet-5, adapter: litellm_loop }
policies:
  max_iterations: 10
```

## What chi doesn't do (yet)

Here's where chi falls short today:

- **A problem pack is a single editable file.** chi can't yet optimize a whole
  repo, such as test-suite time or bundle size, without you wrapping it in a pack.
  A repo mode is the next big design pass.
- **Output is one exported champion file**, not a series of reviewable git commits.
- **There's no browser dashboard.** You get the terminal session plus
  `chi status` and `chi ledger` JSON.

We compare chi feature by feature with the rest of the ecosystem in
[chi vs pi-autoresearch vs karpathy/autoresearch](/blog/chi-vs-pi-autoresearch-vs-karpathy-autoresearch/).

## Try it

The fastest way to see all of this working is the offline demo. It needs no API
key and no network, and takes about ten seconds:
[Run your first autoresearch loop](/blog/first-autoresearch-loop-no-api-key/).
