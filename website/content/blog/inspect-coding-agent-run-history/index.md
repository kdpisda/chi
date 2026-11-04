---
title: "Inspect Coding Agent Run History: What chi's Store Records"
description: "How to inspect coding agent run history in chi: what status, ledger, champion and the SQLite store show after a run, and what they omit. Checked on a real run."
date: 2026-11-04T02:31:56Z
draft: false
category: "Guide"
tags: ["getting started", "autoresearch", "coding agents", "evaluation integrity", "CLI"]
toc: true
newsletter:
  subject: "The agent's wrong answer is in the ledger. Its reason isn't."
  preheader: "I read a chi run back command by command and found what the store doesn't keep."
  body: |
    After an agent loop ends, you want to know three things: what won, what was tried, and what was thrown out. chi keeps all of it in one SQLite file per run, with a handful of commands to read it.

    I ran a three-candidate loop where the second candidate was deliberately wrong, then read the run back with `chi status`, `chi ledger`, `chi champion` and plain SQL. Most of it is what you would hope for. One thing is not: a rejected candidate is recorded, but the reason it failed is not.

    The post walks through each command with real output, and says exactly where the record stops. [Read the walkthrough](https://getchi.dev/blog/inspect-coding-agent-run-history/).
---

To inspect coding agent run history in chi, point three commands at the run
directory: `chi status` for the run row and recent events, `chi ledger` for every
experiment, and `chi champion` for the winner. Everything they print comes from
one SQLite file, so you can also open it with any SQL tool. I ran a small loop on
2026-11-04, deliberately slipped a wrong candidate into it, and read the run
back. This post shows what you get, and where the record stops short.

## Where does a chi run keep its history?

In a run directory, one per run. `chi run` prints the path as `run_dir` in its
summary. After `chi run examples/offline.yaml --runs-root /tmp/runs` I got:

```text
offline-demo-79558f/
  chi.db          SQLite store, WAL mode
  champions/      source of every correct, scored candidate
  mirror/         events.jsonl, experiments.jsonl (plus negative_ledger.jsonl)
  steering.md     operator directives
  workdir/        the live candidate and problem files
```

The database is the source of truth. The `mirror/` files are append-only copies
for `tail -f` and `grep`
([`chi/store/db.py`](https://github.com/kdpisda/chi/blob/main/chi/store/db.py)
calls them "write-only copies"). Without `--runs-root`, runs go under chi's
system data directory.

## What does `chi status` show?

The run row, then the last 10 events, newest first
([`chi/cli.py`](https://github.com/kdpisda/chi/blob/main/chi/cli.py) `status`).
The run row includes the full fleet config as JSON, which is why it is long. The
events are the useful part. Trimmed to type and payload from my run:

```text
17 STOP                {"status": "done"}
16 ITERATION_COMPLETE  {"candidate_hash": "sha256:3b005691...", "iteration": 2, "evals_run": 1, ...}
15 RESULT              {"code_hash": "sha256:3b005691...", "correct": true, "score_value": 0.0760}
14 HEARTBEAT           {}
13 ITERATION_START     {"iteration": 2, "strategy": "strategy-replay", ...}
```

Ten events is a window, not a history. A 3-iteration run emitted 17 events
here, so `chi status` already could not show all of it. For the rest, read
`mirror/events.jsonl`, or query the `events` table.

## What does `chi ledger` show?

Every experiment in the run as one JSON line: the code hash, who wrote it,
whether it passed the correctness gate, the score and the noise estimate. In the
offline demo, all four rows (the baseline plus three candidates) are correct. I
wanted to see a rejection, so I made a second run from a copy of the config with
a script of three candidates: the O(n) loop, an `itertools.accumulate` version
that drops its last element, and the final `accumulate` rewrite. Reading the
ledger back (columns trimmed):

```text
hash      author    correct  seeds_passed     score_ms   noise_std
5dcafb15  baseline  1        [11, 27, 43]     44.1771    7.7494
410b8023  replay    1        [11, 27, 43]      0.1102    0.0356
566c1229  replay    0        []                None      None
3b005691  replay    1        [11, 27, 43]      0.0771    0.0120
```

The wrong candidate is there: `correct` is 0, no seeds passed, and it has no
score because the evaluator never benchmarks a candidate that fails correctness
([`chi/eval/runner.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py)).
That is the property that matters for trust. A broken candidate cannot be the
champion, because the champion query only considers rows with `correct=1` and a
non-null score (`chi/store/ledger.py:58`).

## What does the ledger not tell you?

Why the candidate was rejected. This is the gap I found, and it is worth
knowing before you build on the store.

The evaluator does compute a reason. On a failure it builds a string like
`correctness failed on seed 11: ...` and puts it in the `detail` field of the
result it returns (`runner.py:102-106`). But the `experiments` table has no
column for it, and the `RESULT` event payload is only `code_hash`, `correct` and
`score_value`. In my run the rejected row is a hash and a zero. The reason lived
in the calling process and went away with it.

Two related things follow:

- **Rejected source is not archived.** `champions/` held three files for my
  four rows, one for each correct, scored candidate. The wrong candidate's
  bytes are only in the workdir for as long as nothing overwrites them.
- **Identical code is never re-evaluated.** The ledger is also the dedup cache,
  keyed by code hash. If a coder resubmits a rejected candidate, `evaluate()`
  returns the stored row with `detail="cached"` (`runner.py:71-77`) and runs
  nothing.

I would rather this were different, so I'm listing it as a known limit and not
working around it in the post. If you are debugging a rejection, rerun the
candidate with `chi eval` or run its `check.py` by hand.

## What does `chi champion` return?

The best correct experiment by score, and with `--export <path>` it writes that
candidate's bytes out:

```text
$ chi champion /tmp/runs2/reject-demo-bd6916
{"code_hash": "sha256:3b005691...", "score_value": 0.0771}
```

Export reads from `champions/<hash>.py`, not from the live workdir, because
coders overwrite and revert the candidate file and the live copy is often not
the winner. It also re-hashes the bytes and refuses to export if they don't
match the champion hash (`cli.py`, `champion`). If a holdout check ran, its
verdict is printed with the champion and a warning appears on `overfit` or
`regressed`. My runs had no holdout, so the offline summary shows `"holdout":
null`. The [holdout post](/blog/coding-agent-overfits-benchmark/) covers what
that verdict means.

## Where do dead ends go?

Into a separate table, the negative ledger, and only with evidence attached.
`chi deadend` refuses an entry with no `--evidence-json`:

```text
$ chi deadend --run-dir "$RD" --agent replay --approach-class x --summary "no evidence"
REJECTED: dead-end entries require non-empty --evidence-json
$ chi deadend --run-dir "$RD" --agent replay --approach-class slice-last \
    --summary "dropping the last element fails correctness" \
    --evidence-json '{"code_hash":"sha256:566c1229","seeds_passed":[]}'
n_904cc80d
```

`chi ledger "$RD" --negative` prints those rows. This is where I would put the
missing rejection reason: a dead end is a place you or an agent can record why
an approach failed, with the hash as evidence. Nothing writes to it
automatically in an offline run, I filed that entry by hand. `chi query <text>`
does a substring match over experiment strategies and dead-end class and
summary, which is the same lookup the built-in LLM loop exposes to its agent as a
`query_knowledge` tool (`chi/agents/litellm_loop.py:30`). It does not search code. My `chi query slice` returned the dead end and no
experiments.

## How do I read the store directly?

Open the database read-only and ask SQL questions. For a run directory `$RD`:

```python
import sqlite3
c = sqlite3.connect(f"file:{RD}/chi.db?mode=ro", uri=True)
c.execute("select correct, count(*), min(score_value) from experiments group by correct")
# [(0, 1, None), (1, 3, 0.0771)]
c.execute("select type, count(*) from events group by type")
# CLAIM 1, DEAD_END 1, HEARTBEAT 3, ITERATION_COMPLETE 3, ITERATION_START 3,
# RESULT 4, STEER_ACK 1, STEER_UPDATE 1, STOP 1
```

The store uses WAL mode, which lets readers and a writer coexist. I only read
finished runs, so I haven't tested reading one mid-run. The tables are `runs`, `tasks`, `events`, `experiments`,
`negative_ledger`, `challenges`, `agents` and `budgets`
([schema](https://github.com/kdpisda/chi/blob/main/chi/store/schema.sql)).

## Where this falls short

The record is thorough about what happened and thin about why. Rejection reasons
are not stored, rejected source is not archived, and `chi status` shows a window
of 10 events. The scores in this post come from a single prefix-sum function on a
shared sandbox, so don't read the milliseconds as a benchmark. The wrong
candidate was one I wrote to get a rejected row, not a mistake a model made. For
the larger list of known gaps see the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Run it yourself

```sh
uv tool install getchi
git clone https://github.com/kdpisda/chi && cd chi
chi run examples/offline.yaml --runs-root /tmp/runs
chi ledger /tmp/runs/offline-demo-*
```

Then swap one entry in a copy of `examples/demo_candidates.json` for a wrong
answer and read the ledger again. The [first-loop post](/blog/first-autoresearch-loop-no-api-key/)
covers the demo itself. If chi is useful to you, a star on the
[repo](https://github.com/kdpisda/chi) helps others find it.
