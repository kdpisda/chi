---
title: "Resume a Coding Agent Run After a Crash: What chi Keeps"
description: "Can you resume a coding agent run after a crash? I killed a chi run mid-loop, read what it left behind, and restarted from its best candidate. Commands inside."
date: 2026-11-11T02:33:13Z
draft: false
category: "Guide"
tags: ["loop reliability", "autoresearch", "coding agents", "CLI", "getting started"]
toc: true
newsletter:
  subject: "I killed my agent loop halfway. Here's what survived."
  preheader: "One SIGKILL, one Ctrl-C, and the three steps that got the best candidate back."
  body: |
    Overnight agent loops die: a laptop sleeps, an SSH session drops, someone hits Ctrl-C. The question the next morning is whether the work is gone.

    I killed a chi run partway through, twice, and read what it left on disk. The short answer: chi can't pick a killed run back up where it stopped. But every candidate that passed the correctness check is archived the moment it is scored, so the best one is never lost, and you can seed a new run with it.

    The post shows the exact state a killed run leaves (including one status that will mislead you), and the three steps that turned a dead run's champion into the next run's baseline. [Read the walkthrough](https://getchi.dev/blog/resume-coding-agent-run-after-crash/).
---

You started an agent loop before bed. In the morning the terminal is gone: the
laptop slept, the SSH session dropped, or you hit Ctrl-C to fix something and
meant to restart it. Now you want to know whether you can resume the coding
agent run after the crash, or whether a night of evaluations went with it.

## Can you resume a coding agent run after a crash in chi?

Not in place. chi 0.2.0 has no command that restarts a killed run where it
stopped. What it does do is write each result to disk as it happens, and archive
the exact source of every candidate that passed the correctness check at the
moment it was scored. So a crash loses the in-flight iteration, not the best
result so far. The way back is to export that best candidate (the champion) and
make it the starting point of a new run.

I checked this on 2026-11-11 by killing a run partway through, once with
`SIGKILL` and once with Ctrl-C (`SIGINT`), using chi's offline demo so it cost
nothing. Everything below is from those runs.

## How I killed the run

The offline demo, `examples/offline.yaml`, uses a scripted adapter that replays
three canned candidates for a prefix-sum function and really evaluates each one.
The shipped baseline is a deliberately slow O(n²) version. The full run takes
about two seconds, so I used a small Python script that starts `chi run
examples/offline.yaml`, polls the run's SQLite store, and sends the signal as
soon as three experiments are recorded: the baseline and the first two
candidates. The third candidate never got evaluated.

Both kills left the same state behind. Ctrl-C exited with code 130, and
neither printed the JSON summary a finished run prints.

## What does a killed chi run leave behind?

The run directory looks exactly like a finished one:

```text
offline-demo-cb973e/
  champions/      one .py file per correct, scored candidate
  chi.db          the SQLite store
  mirror/         append-only JSONL copies of events and experiments
  steering.md
  workdir/
```

`chi ledger` on the killed run lists all three experiments it got through
(trimmed to author, correct, score in ms, hash):

```text
baseline  1  36.05   sha256:5dcafb15912b
replay    1  34.20   sha256:650927feba59
replay    1   0.122  sha256:410b80234e4e
```

And `champions/` holds three files, one per row, named by code hash. That is by
design: [`chi/eval/runner.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py)
(lines 150-162) writes a correct, scored candidate's bytes to
`champions/<hash>.py` before the experiment row is recorded, so the archive
never lags behind the ledger. The comment there says why: coders overwrite and
revert the working file, so "the on-disk workdir file ... is not a reliable
record of what actually won." In my killed run the workdir file did happen to
match the champion, but nothing guarantees that in general.

`chi champion` works on the killed run and picks the best correct row:

```text
$ chi champion ~/.local/share/chi/runs/offline-demo-cb973e
{"code_hash": "sha256:410b80234e4e...", "score_value": 0.12247300000467476}
```

The last events in `chi status` show where it stopped: a `RESULT` for the
second candidate, `ITERATION_COMPLETE` for iteration 1, then `ITERATION_START`
for iteration 2 and nothing after it.

## What does a killed run get wrong?

Three things, all because the cleanup code never ran.

**It still says `running`.** The `runs` row is created with status `running`,
and only the end of the fleet loop sets `ended_at` and the final status
([`chi/orchestrator/loop.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py),
line 447). A killed process never gets there, so `chi status` shows
`"ended_at": null, "status": "running"` indefinitely. The session index that
`/resume` reads has the same stale status. In the chi session, `/resume
offline-demo-cb973e` printed:

```text
resumed offline-demo-cb973e [running] — started 2026-11-11T02:36:50 in ...
champion: 0.12247300000467476 (sha256:410b80234e4…)
(/status /ledger /champion inspect it; /steer still reaches it)
```

`/resume` reattaches the session to the run's store so you can inspect it and
replay any director rounds. It does not restart anything
([`chi/session/engine.py`](https://github.com/kdpisda/chi/blob/main/chi/session/engine.py),
`_cmd_resume`). For a killed run, "running" means "nobody marked it finished,"
and `/steer` writes to a `steering.md` that no process is reading.

**The task keeps its lease.** The coder's task stays `claimed` with a lease 900
seconds out (the default `lease_seconds` in `chi/config.py`). There is a helper
that returns expired leases to `pending`, `expire_stale_leases` in
`chi/store/tasks.py`, but nothing on the `chi run` path calls it today. It
doesn't matter for recovery, since you won't reuse this run, but don't read a
`claimed` task as a live agent.

**The champion was never exported or gated.** On a clean finish, chi copies the
champion's archived bytes into `workdir/` and, if the problem has a held-out
workload, runs the holdout check on it (loop.py, lines 437-445). A killed run
skips both. So whatever you recover has not been checked against the holdout,
even if the problem defines one.

## How do you restart from the best candidate?

Seed a new run with the champion as its starting candidate. With the offline
demo, that was three steps. First, a copy of the problem pack with the champion
exported into it:

```sh
cp -r problems/optimize_function problems/optimize_function_resumed
chi champion ~/.local/share/chi/runs/offline-demo-cb973e \
  --export problems/optimize_function_resumed/candidate.py
```

`--export` reads the archived bytes from `champions/`, not the live workdir file
([`chi/cli.py`](https://github.com/kdpisda/chi/blob/main/chi/cli.py),
`champion`). Second, a copy of the fleet config pointing at the new pack:

```yaml
run_name: offline-demo-restart
problem: problems/optimize_function_resumed
```

Third, `chi run restart.yaml`. The new run measures its baseline on the
recovered champion, so every later candidate has to beat that, not the original
O(n²) code:

```text
"baseline_score": 0.08678300000042327,
"champion_score": 0.06839400001013018,
"status": "done",
```

The baseline went from about 35 ms in the killed run to under 0.1 ms in the
restart. The gap between 0.087 and 0.068 is a different story: at a tenth of a
millisecond the timing noise is the same size as the difference, and in an
earlier restart the same two candidates came out the other way round. If that
kind of win matters to you, read
[why most agent benchmark improvements are noise](/blog/agent-benchmark-improvement-is-noise/)
before trusting it.

## What doesn't carry over to the new run?

Each run gets its own `chi.db` under its own run directory
([`chi/store/db.py`](https://github.com/kdpisda/chi/blob/main/chi/store/db.py),
`Store.open`). So the restarted run starts with:

- **An empty ledger.** No record of the candidates the killed run already
  evaluated, so identical code can be evaluated again instead of hitting the
  dedup cache.
- **An empty negative ledger.** Dead ends the fleet recorded are not visible to
  the new agents. If one cost you real money, re-enter it with `chi deadend`
  (evidence required) or put it in the new run's steering.
- **A fresh budget.** The cost ceiling counts from zero again. Lower it by what
  the killed run spent if you want the pair to stay under your original cap.

The killed run's directory is still there afterwards, so you can keep reading it
with `chi ledger` and `chi status` for the full history. The
[run history walkthrough](/blog/inspect-coding-agent-run-history/) covers what
each of those commands shows.

## What about the director?

The director, chi's autonomous multi-round loop, runs inside the chat session as
an in-process loop, so closing the session stops it. Its completed rounds are in
the store, and `/resume <run_id>` or `/director` replays them. A detached
director that survives closing the terminal is listed in
[`docs/director.md`](https://github.com/kdpisda/chi/blob/main/docs/director.md)
as planned, not shipped. I didn't kill a director run for this post, so I'm not
claiming more than the doc says. For an overnight director run today, start the
session inside `tmux` or `screen` so a dropped connection doesn't end it.

## Where this falls short

- **No in-place resume.** You restart from the champion, not from the iteration
  where the run died. The in-flight iteration's work is lost.
- **Stale status.** A killed run reports `running` until you delete or ignore
  it. Nothing sweeps it.
- **Fresh memory.** The new run doesn't inherit the ledger, the dead ends, or the
  budget spent.
- **Tested small.** One scripted coder, one problem pack, two kills. A
  multi-coder fleet writes to the same store and archive, but I haven't killed
  one for this post.

None of this is in the [public gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md)
yet, which lists the related missing piece (git-backed experiment history) and
what is already planned.

## Try the kill yourself

Run `chi run examples/offline.yaml`, then try the same recovery on the run
directory it prints. The [concepts page](/docs/concepts/) explains the store,
the champion and the ledger that make the recovery possible.
