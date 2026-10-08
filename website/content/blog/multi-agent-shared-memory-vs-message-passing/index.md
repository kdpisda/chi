---
title: "Multi-Agent Coordination: Shared Memory vs Message Passing"
description: "Should coding agents share a store or message each other? What a shared SQLite store gave me in chi, plus a dedup race I found while testing it."
date: 2026-10-08T02:33:12Z
draft: false
category: "Deep dive"
tags: ["multi-agent", "fleets", "autoresearch", "coding agents", "blackboard"]
toc: true
newsletter:
  subject: "Two agents, one candidate, one crash"
  preheader: "Shared memory dedups for free. Until both agents miss the cache at once."
  body: |
    I ran two scripted coders against one chi run store. They requested six evaluations and the benchmark ran three times. The store's dedup did its job.

    Then I gave both coders the exact same script. In 2 of 10 runs, the second agent's insert hit a primary-key error and that agent stopped, while the run still reported `done`. The lookup and the insert are two separate steps, and I found the gap by testing the claim I was about to make.

    The post covers what a shared store replaces in multi-agent coordination, what it costs, and the unfixed race. [Read the post](https://getchi.dev/blog/multi-agent-shared-memory-vs-message-passing/).
---

If you run several coding agents on one problem, you have to decide how they
share what they learn. One option is message passing: agents talk to each other
or to a coordinator, and the transcript is the memory. The other is shared
memory: agents never talk, and everything lands in a store they all read.

chi, the harness I build, picked shared memory. This post is what that choice
bought, what it does not buy, and a race in its dedup that I found while
testing for this post. Every number below is from a `$0` run I did on
2026-10-08 with scripted coders, so there is no model in the loop.

## What does a shared store replace in multi-agent coordination?

It replaces three things that message passing makes you build: a way to avoid
repeated work, a way to restart an agent, and a way to audit what happened.

chi calls this a blackboard: [one SQLite file per run](https://github.com/kdpisda/chi/blob/main/chi/store/db.py)
holding tasks, an append-only event log, experiments keyed by code hash, and a
negative-results ledger. Each write is also appended to a JSONL mirror. Agents
never address each other. A coder reads the store, edits a file, and the
evaluator writes the result back. The
[concepts doc](/docs/concepts/) has the rest of the parts.

## How does the store dedup work across agents?

The experiments table has `code_hash` as its primary key
([`schema.sql:41`](https://github.com/kdpisda/chi/blob/main/chi/store/schema.sql)).
The hash is a sha256 of the candidate source after normalizing line endings,
trailing whitespace and trailing blank lines
([`hashing.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/hashing.py)).
Before benchmarking, the evaluator looks the hash up, and if a row exists it
returns that row marked `cached`
([`runner.py:71-77`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py)).

To see it work I ran two scripted coders in one fleet. `alpha` replays the three
candidates from `examples/demo_candidates.json`. `beta` replays the same three
with CRLF line endings and three trailing spaces on every line, so the bytes
differ and the normalized source does not.

```text
$ chi run blackboard-demo.yaml        # two coders, 3 iterations each
experiments table:
  baseline  5dcafb15  44.42 ms
  alpha     650927fe  45.02 ms
  alpha     410b8023   0.151 ms
  beta      3b005691   0.084 ms
ITERATION_COMPLETE events: 6   RESULT events: 4
```

Six iterations each asked for an evaluation. The table has four rows, and one
of them is the baseline, so the benchmark ran three times for six requests.
The winner was found by `beta`, and `alpha`'s own request for it was a lookup.
Across three runs the split between agents changed (`alpha` found two of the
three, then `beta` did, then `alpha` found all three), and the count stayed
the same. Timings are noisy:
the same candidate scored 0.151, 0.159 and 0.120 ms in three runs, so read the
table for who did the work, not for the speeds.

## Why does shared memory help with context rot?

A long-lived agent's context fills up and its output degrades. With a shared
store, the recovery plan is to retire the agent and start a fresh one.
[`build_seed_context`](https://github.com/kdpisda/chi/blob/main/chi/agents/context.py)
builds each iteration's input from the store (champion score, the ledger's dead
ends) plus the candidate file in the agent's workdir and the current steering
text. Nothing in it comes from another agent's conversation.

I want to be careful here. I read this in the code and I did not kill an agent
mid-run and respawn it. The design assumption is in the code and the spec, and
the practical test of it is still ahead of me.

## Where does it get awkward?

Shared memory moves the hard problems into the store. Two of them showed up.

**Cached evaluations do not create rows.** The watchdog decides whether an agent
is still exploring by looking up its latest experiment in the store
([`loop.py:240-246`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py)).
When an agent's request is a cache hit, nothing is written under its name. In my
first run, `alpha`'s third iteration was cached, and its recorded
`candidate_hash` for that iteration was its previous candidate, `410b8023`.
An agent that is mostly served from cache can look stalled to a check that reads
"my latest experiment." I did not run long enough to see the watchdog act on
that, so I am flagging it, not claiming it.

**The lookup and the insert are separate steps.** This is the one that broke.

## Does the dedup race when two agents submit identical code?

Yes. `evaluate` calls `get_experiment`, runs the benchmark, and later
`record_experiment` checks again and then inserts
([`ledger.py:29-31`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py)).
The store serializes each statement with a lock, but the check and the insert
are not one transaction. If two agents evaluate the same new code at the same
time, both miss, both benchmark, and the second insert violates the primary key.

I reproduced it by pointing both coders at the identical script and running the
fleet ten times:

```text
runs: 10   experiments rows: 4 in every run
runs where an agent hit an error: 2
error: UNIQUE constraint failed: experiments.code_hash
```

The coder loop catches any exception from an iteration, logs a `STATUS` event
with the error, and marks that agent `failed`
([`loop.py:229-233`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py)).
In the failing runs the other agent kept going and the run's final status was `done`, because a
run reports `failed` or `stalled` only when every agent did
([`loop.py:432-436`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py)).
The dead agent's task row stayed `claimed`.

How likely is this in a real run? Two models produce identical normalized
source far less often than two copies of one script do, so my 2 in 10 is an
upper bound for a synthetic worst case. I have not measured the rate with live
models. The plain fix is to let the insert be the arbiter (insert, and treat a
primary-key conflict as a cache hit). I have not written or tested it, so
treat that as a direction, not a promise.

## Where this falls short

- Dedup is per run. Each run gets its own database, so a second run does not
  see the first one's experiments.
- The hash is exact on normalized text. A comment-only edit is a new candidate
  and pays a full evaluation.
- A store is not a substitute for coordination you actually need, such as one
  agent handing a subtask to another. chi's task table has claims and leases
  for the fleet, not delegation between agents.
- The race above is unfixed on `main` and in 0.2.0. Known gaps live in the
  [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Try it yourself

The scripted demo needs no API key and costs nothing. After it runs, the
`chi.db` file in the run directory opens with any SQLite client, so you can
query the same experiments and events I did. The
[concepts doc](/docs/concepts/) and the post on
[the negative-results ledger](/blog/multi-agent-system-repeats-failed-approaches/)
cover the other tables.

```bash
uv tool install getchi
chi run examples/offline.yaml
```

If a harness that tells you where its own coordination breaks is what you want,
star the repo: [github.com/kdpisda/chi](https://github.com/kdpisda/chi).
