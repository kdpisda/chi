---
title: "getchi 0.2.0: What Changed for Agent Loops Since 0.1.0"
description: "What getchi 0.2.0 changed if you tried 0.1.0: a director that stops itself, a verified export, an offline demo, and where each one still has limits."
date: 2026-10-03T02:32:38Z
draft: false
category: "Release"
tags: ["release", "getchi", "coding agents", "autoresearch", "director"]
toc: true
newsletter:
  subject: "The export that shipped the wrong file"
  preheader: "What changed in getchi 0.2.0, tested at $0, with the limits left in."
  body: |
    If you tried getchi 0.1.0 and bounced, 0.2.0 is the release that changed the most for someone running agent loops unattended. I re-tested the parts I could at `$0` and read the rest from the code.

    The one I'd check first is export. Coders revert their working file, so exporting "the candidate" could ship a loser while reporting the winner's score. I reset the live file to the slow baseline, exported, and got the champion anyway.

    The post also covers the self-stopping director, the offline demo and the sandboxed eval, and says plainly what each one doesn't do yet. [Read the release notes](https://getchi.dev/blog/getchi-0-2-0-release-notes/).
---

You tried getchi 0.1.0, hit a rough edge, and moved on. Or you never installed
it and want to know whether 0.2.0 is where an agent loop becomes something you
can leave alone. This is the short version: what changed, what I re-tested
today, and what still has limits.

getchi is the PyPI package for chi, an open-source autoresearch harness (a loop
where coding agents edit code, an evaluator scores it, and the best candidate
is kept). PyPI shows 0.1.0 uploaded on 2026-07-31 and 0.2.0 on 2026-08-01, and
0.2.0 is still the latest as of 2026-10-03
([PyPI](https://pypi.org/project/getchi/)). The official list is the
[changelog](/docs/changelog/); this post adds what I ran and what I read.

## What is new in getchi 0.2.0?

The changelog groups it as five additions and four fixes. The ones that matter
to someone running loops:

| Change | Why you'd care |
|---|---|
| Director self-stop | Hand off a goal and a spend cap, then walk away |
| Verified export | The file you ship is the champion, hash-checked |
| Offline demo | Try the whole loop with no API key |
| Sandboxed eval | Run an untrusted candidate's checks in a Docker jail |
| NoiseGuard for local evals | Re-benchmark an apparent win before believing it |

## Can the director stop itself when it hits a goal?

Yes. The director is chi's autonomous research loop. You give it a target
score and a cost ceiling, and it checks both after every round
([`chi/director/loop.py:174-187`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L174-L187)).
Two details from reading that code:

- A score that meets the target does not count if the latest holdout result
  says the champion is not shippable
  ([`loop.py:72-80`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L72-L80)).
  Note that the holdout gate itself came after 0.2.0. It is on `main`, not in
  the 0.2.0 wheel, so I'd treat it as unreleased.
- The cost check runs after a round finishes, so a round can overshoot the
  ceiling. It sums `result.cost_usd` per round. I haven't checked whether
  vendor-CLI spend reaches that number, and
  [where cost caps leak](/blog/limit-llm-api-cost-coding-agents/) shows a
  related gap in the per-run cap.

I did not run a live director in this session, because it needs a model. I read
it from the code. You reach it through the interactive `chi` session, not
`chi run`, and `/director` replays the stored rounds
([`chi/session/engine.py:705-731`](https://github.com/kdpisda/chi/blob/main/chi/session/engine.py#L705-L731)).

## Does export ship the best candidate or the last file written?

The best candidate, now. Coders overwrite and revert the working file, so the live
file is often not the champion. Now every scored
candidate is archived by hash and `chi champion --export` writes the archived
bytes, refusing if the hash doesn't match
([`chi/cli.py:449-470`](https://github.com/kdpisda/chi/blob/main/chi/cli.py#L449-L470)).

I tested it on 2026-10-03. After an offline run, I overwrote the live
`candidate.py` with the O(n²) baseline, then exported:

```
$ chi champion $RUN --export out.py
{"code_hash": "sha256:3b005691...", "score_value": 0.119...}
$ cat out.py
import itertools
def solve(xs):
    return list(itertools.accumulate(xs))
```

The exported file is the `itertools.accumulate` champion, and it differs from
the baseline sitting in the working directory. That is the whole fix, shown in
two commands.

## Can I try it without an API key?

Yes, and it takes seconds. The offline config replays three canned candidates
through the real evaluator and store
([`examples/offline.yaml`](https://github.com/kdpisda/chi/blob/main/examples/offline.yaml)).
Today's run:

```
$ chi run examples/offline.yaml
"iterations": 3,
"baseline_score": 47.46,
"champion_score": 0.119,
"total_cost_usd": 0,
"status": "done"
```

That is about 47 ms down to about 0.12 ms on a shared sandbox, in roughly 3
seconds of wall time. Timings drift run to run, so read it as two orders of
magnitude, not a benchmark. The agent is scripted, so it shows the harness
(scoring, store, champion), not model quality. The
[offline walkthrough](/blog/first-autoresearch-loop-no-api-key/) goes through
it step by step.

## What does the sandboxed eval protect against?

A candidate can attack the evaluator itself. The code comment cites a dogfood
candidate that froze the benchmark's `perf_counter` and hijacked `list` in the
caller's frame
([`chi/eval/runner.py:58-65`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L58-L65)).
Setting `eval_sandbox: docker` runs both the correctness and benchmark commands
in a container with `--network none`, mounting only the working directory
([`chi/agents/sandbox.py:47-76`](https://github.com/kdpisda/chi/blob/main/chi/agents/sandbox.py#L47-L76)).

I read this, I didn't run it today. You also need an image to run it in
(`eval_sandbox_image`). The `docker-cli` preset used for vendor CLI coders is
weaker on purpose: bridge network and read-only auth mounts, per the
[security docs](/docs/security/).

## Where this falls short

- 0.2.0 is two months old and `main` has moved: the holdout gate is not in the
  wheel yet.
- The self-stop goals only exist in the interactive director, and I haven't
  verified the cost ceiling against vendor-CLI spend.
- I haven't verified how the director's running cost is accumulated across
  many rounds. I'd set the ceiling with headroom, not to the dollar.
- The [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md)
  lists what chi still doesn't do.

## Try it

```
uv tool install getchi
chi run examples/offline.yaml
```

That is a `$0` run of the loop above. If it's useful, a star on
[the repo](https://github.com/kdpisda/chi) helps other people find it.
