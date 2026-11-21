---
title: "Multi-Agent Fleet Crash Recovery: I Killed chi 28 Times"
description: "Multi-agent fleet crash recovery, tested: I killed a two-coder chi fleet 28 times and checked its shared SQLite store, champion archive and task leases."
date: 2026-11-21T02:34:55Z
draft: false
category: "Case study"
tags: ["fleets", "multi-agent", "loop reliability", "autoresearch", "coding agents"]
toc: true
newsletter:
  subject: "The winning candidate was in the other agent's folder"
  preheader: "28 kills of a two-agent fleet: what the store kept, and where the workdirs lied."
  body: |
    When a fleet of coding agents dies mid-run, they all die together, in the middle of each other's writes. I wanted to know whether chi's shared store survives that, so I killed a two-coder fleet 28 times at random points and checked it after each kill.

    The store held up every time: no corrupt database, no half-recorded experiment, no archived candidate without its row. The part that surprised me was the workdirs. After most kills, neither agent's folder held the best candidate, and three times it sat in the folder of the agent that didn't get credit for it.

    The post has the harness, the numbers, and the one place to look for the champion after a crash. [Read the case study](https://getchi.dev/blog/multi-agent-fleet-crash-recovery/).
---

Two coding agents share one run. One is mid-benchmark, the other just wrote a
result, and the process dies. If that can leave the shared state half-written,
multi-agent fleet crash recovery starts with working out which records to
trust.

My post on [resuming a single-agent chi run after a crash](/blog/resume-coding-agent-run-after-crash/)
ended by admitting I hadn't killed a fleet. This is that test.

## Does a multi-agent fleet's shared store survive a crash?

In my test, yes. I killed a two-coder chi fleet 28 times (20 with `SIGKILL`, 8
with `SIGINT`, the Ctrl-C signal) at random points between 0.9 and 2.1 seconds
into a run that takes about two seconds to finish. After every kill, the SQLite
database passed `PRAGMA integrity_check`, and the experiment rows, the `RESULT`
events, the JSONL mirror lines and the archived candidate files matched one for
one.

What doesn't survive is the end-of-run work: marking the run finished,
releasing tasks, and copying the champion (the best correct candidate so far)
somewhere you'd look for it. The champion is safe, but only in one place.

## The setup: two scripted coders, one store

chi is an autoresearch harness: it runs coding agents in a loop against an
evaluator and keeps whatever measurably improves a score. A fleet is several of
those agents (coders) on one problem. In chi 0.2.0 each coder runs in its own
thread with its own copy of the problem pack, `workdir-<id>`
([`chi/orchestrator/loop.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py),
lines 402 and 419), and all of them write to one SQLite file per run.

To make it free, I used chi's `scripted` adapter, which replays canned
candidates and really evaluates each one, on the bundled `optimize_function`
pack (speed up a deliberately slow O(n²) prefix sum). Two coders, six
candidates each, with overlapping scripts so they sometimes submit the same
code:

```yaml
run_name: fleet-kill
problem: problems/optimize_function
budgets:
  total_usd: 0.0
coders:
  - {id: c1, model: scripted, adapter: scripted, script: c1.json}
  - {id: c2, model: scripted, adapter: scripted, script: c2.json}
policies:
  max_iterations: 6
```

A short Python script started `chi run`, slept a random interval, sent the
signal, and checked the run directory. I ran it on 2026-11-21. Each kill left
between 1 and 7 experiment rows.

## What did 28 kills leave behind?

| Check, after each kill | Result |
|---|---|
| `PRAGMA integrity_check` | `ok` in 28 of 28 |
| ledger rows = `RESULT` events = mirror lines = archive files | 28 of 28 |
| archived file hashes to its row's `code_hash` | every file, every kill |
| archive file with no ledger row, or the reverse | 0 |
| run `status` | `running`, `ended_at` null, 28 of 28 |
| task rows | both `claimed` in 27; one `verified`, one `claimed` in 1 |

Two plain choices explain it.

**Every write is its own transaction.** The store is one connection in WAL mode
behind a lock, and every statement commits on its own
([`chi/store/db.py`](https://github.com/kdpisda/chi/blob/main/chi/store/db.py),
lines 24-48). A kill can land between two writes, never inside one.

**The archive is written before the row.** When a candidate passes the
correctness check and gets a score, chi writes its exact bytes to
`champions/<hash>.py` first and records the experiment second
([`chi/eval/runner.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py),
lines 150-170). The worst a kill can do is leave an archived file with no
row, which nothing reads. I never caught even that.

One window I can't rule out: the row, the mirror line and the `RESULT` event are
three separate writes
([`chi/store/ledger.py`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py),
lines 32-48). A kill between them leaves a row the mirror lacks. The database is
the source of truth, so nothing is lost, but a mirror mismatch after a crash
isn't corruption.

## Where is the champion after a fleet crash?

Only in `champions/`. This is the fleet-specific finding.

Of the 28 kills, 17 had a champion that wasn't the baseline. In 13 of those 17,
neither coder's workdir held the champion's code. In 3 more, it sat in the
*other* coder's workdir. Only once was it in the workdir of the coder the
ledger credits with it.

Here is one of them, run `fleet-kill-5e41a7`, killed at 2.04 seconds. The
ledger (author, correct, score in ms, hash):

```text
baseline  1  35.489  sha256:5dcafb15912b
c1        1  35.813  sha256:28af90265cf1
c2        1  35.855  sha256:d5e06725e0f6
c2        1   0.137  sha256:5e9514b1661e
c2        1   0.089  sha256:3e5d4732d3fa
c2        1   0.065  sha256:8cde7ccf634f
```

The champion is `8cde7ccf`, the `itertools.accumulate` version, credited to
`c2`. But `workdir-c2/candidate.py` held c2's next attempt, a variant using
`operator.add`, which it was evaluating when the kill landed. The champion's
bytes were in `workdir-c1`, because c1 had reached the same code on its own and
was still benchmarking its copy when the kill landed. And `workdir/`, the shared directory a finished run
exports the champion to, still held the O(n²) baseline.

The export happens at the very end of the fleet loop (loop.py, lines 438-445):
a finished run copies the archived bytes into `workdir/` and, if the problem has
a holdout workload, gates the champion on it. A killed run reaches neither step.
A coder's workdir is scratch space it overwrites every iteration, never a record
of what won.

## How do you restart a killed fleet?

Same as the single-agent case: export the champion into a copy of the problem
pack and start a new fleet on it. `chi champion --export` reads from
`champions/`, not from any workdir:

```sh
cp -r problems/optimize_function pack2
chi champion runs/fleet-kill-5e41a7 --export pack2/candidate.py
# point the fleet's `problem:` at pack2, then
chi run fleet2.yaml
```

The new fleet measured the exported candidate as its baseline (about 0.06 ms).
The ledger, dead ends, dedup cache and budget spent don't carry over.

Before you restart, check the score you're inheriting. In 6 of the 17 kills, the
"champion" was a still-quadratic variant that beat the baseline by 0.5 to 3.2 ms
out of about 35. The baseline itself, the same code every time, measured anywhere
from 27.7 to 45.9 ms across the 28 runs, so those wins are noise. A killed run
also skipped the holdout gate, so the best row is one unchecked measurement.

## What should you clean up?

Two things lie after a fleet crash.

- **The run says `running` forever.** Only the end of the fleet loop sets
  `ended_at` and the final status (loop.py, line 447).
- **Every coder's task stays `claimed`,** with a 900-second lease that nobody
  will renew. `expire_stale_leases` in
  [`chi/store/tasks.py`](https://github.com/kdpisda/chi/blob/main/chi/store/tasks.py)
  would return them to `pending`, but nothing on the `chi run` path calls it.
  In one kill, `c1` had finished, so its task said `verified` and `c2`'s said
  `claimed`. That's a per-coder snapshot, not a live agent.

## Where this falls short

- **Scripted coders, small runs.** Two coders, about two seconds per run. Real
  agents spend minutes per iteration, so a kill usually lands mid-iteration and
  loses that iteration's work.
- **No resume in place.** You restart from the champion, not from where each
  coder stopped.
- **A known race, not exercised here.** Two coders evaluating the same new
  candidate at once can both miss the dedup cache, and the second insert fails
  and stops that coder. It's covered in [shared memory for multi-agent systems](/blog/multi-agent-shared-memory-vs-message-passing/)
  and still open on `main`.
- **Not in the gap list yet.** The [public gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md)
  lists git-backed experiment history as the nearest planned piece. Sweeping
  killed runs isn't on it.

## Try it on your own fleet

Why chi's agents share a store instead of messaging each other is in the
[blackboard section of chi's concepts doc](/docs/concepts/). If you run agents in parallel today,
kill a two-coder `scripted` fleet and check `champions/` against `chi ledger`.
It costs nothing.
