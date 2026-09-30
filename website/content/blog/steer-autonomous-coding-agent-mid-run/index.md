---
title: "How to Steer an Autonomous Coding Agent While It Runs"
description: "Redirect a long-running coding agent without restarting it. How chi's steering.md works: what agents read, when, how directives survive, and what it can't do."
date: 2026-09-30T02:31:58Z
draft: false
category: "Guide"
tags: ["coding agents", "steering", "autoresearch", "fleets", "human in the loop"]
toc: true
newsletter:
  subject: "Redirecting a coding agent without losing its context"
  preheader: "One file, read between iterations. What it can change, and what it can't."
  body: |
    An overnight agent loop is hard to correct. If you notice it grinding on the wrong idea, the options are to kill it and lose its context or to wait until morning. chi has a third option: a plain markdown file the agents re-read before every iteration.

    I ran through exactly what happens when you write to that file: which text the agent receives, what gets logged, and how the director's own rewrites avoid erasing your note.

    It also has a hard limit: a directive can't interrupt an iteration that's already running, and I traced where that bites. [Read the walkthrough](https://getchi.dev/blog/steer-autonomous-coding-agent-mid-run/).
---

You start a coding-agent loop before bed. At 11pm you check the log and see it
spending every iteration on micro-tuning a loop that needs a different
algorithm. Killing the run throws away the agent's state. Letting it go wastes
the night.

The mechanism I use in chi is small: a markdown file that the agents re-read
before each iteration, and that you append to from another terminal. This post
walks through how that works, what I verified by running it, and where it stops
being useful.

## How do you steer a coding agent without restarting it?

Write the instruction into a file the agent re-reads between iterations, not
into the running process. In chi that file is `steering.md` in the run
directory, and the command is:

```bash
chi steer <run_dir> "stop micro-tuning; try itertools"
```

That appends a numbered block to the file (the code is
[`chi steer`](https://github.com/kdpisda/chi/blob/main/chi/cli.py)). Inside an
interactive chi session, `/steer <text>` or plain typed text during a run does
the same thing. Nothing signals the agent and nothing restarts. The next
iteration simply starts with different instructions.

## What does the agent actually receive?

Before every iteration, the run loop calls `steering.refresh()`
(`chi/orchestrator/loop.py:212`). That re-reads the file and builds the text
the coder sees: an auto-generated digest of the run, then a divider, then your
file verbatim (`chi/orchestrator/steering.py:48`). The vendor-CLI adapter puts
it under a "Steering directive (obey it)" heading in the prompt
(`chi/agents/cli_subprocess.py:18-19`).

I checked this against a finished run of `examples/offline.yaml` (chi 0.2.0,
2026-09-30). I ran `chi steer` on its run directory, then called `refresh()`
from a short script. The combined text it returned:

```text
# Auto-steering digest (run offline-demo-b0b175)
- Champion: 0.07421499999793468 (runtime_ms) hash=sha256:3b005691637…
- Ruled out: nothing yet.
- Focus: beat the champion; record every attempt honestly.

---

# Operator directives (optional)
...
<!-- Write directives below this line. -->

## §op 2026-09-30T02:36:07.890223Z
stop micro-tuning; try itertools
```

The digest is deterministic: it is built from the ledger (current champion, the
approach classes ruled out so far) with no model call
(`chi/orchestrator/steering.py:51-71`). So the agent gets fresh facts from the
harness and your instructions from you, in one block.

## How do you know the agent saw your change?

By hash. `refresh()` hashes your file and, when the hash changes, appends a
`STEER_UPDATE` event containing the full text. Each adapter then emits a
`STEER_ACK` the first time it runs under a new hash
(`chi/agents/protocol.py:83-91`), and every `ITERATION_START` records the hash
it ran under.

In my check the store held three `STEER_UPDATE` events, each with a different
hash: the template as the run started, the file after my first `chi steer`, and
the file after a second `chi steer`. Calling `refresh()` again with no edit
added nothing. Unchanged file, unchanged hash, no new event.

One caveat on what this proves. The offline demo's scripted adapter replays canned
candidates and ignores the directive text. So this run shows the plumbing (file,
hash, event, prompt text), not a model obeying an instruction. Whether a given
model follows "try itertools" is up to the model.

## What happens when the director rewrites the file?

With the autonomous director running, a strategist rewrites `steering.md` every
round with dead-end blocks and fresh strategies. That could erase your note,
so it doesn't: the director writes its own block, then re-appends everything
from the first `## §op` heading onward
(`chi/director/strategy.py:77-90`). Your directives, whether from `chi steer` or
typed text, sit under that marker and survive.

Typed text during a director run is handled differently from a plain run. It is
folded into the director's next round rather than the next iteration, except
for a bare `stop`, which halts the director at the next round boundary
(`chi/session/engine.py:1091-1093`). The [concepts page](/docs/concepts/#steering)
covers the two layers.

## Where does steering fall short?

Three limits I'd want to know about before relying on it.

- **It can't interrupt a running iteration.** The file is read once, at the
  start of each iteration. An iteration can run up to
  `iteration_timeout_seconds`, which defaults to 600
  (`chi/config.py:33`). A directive you write now lands on the next iteration,
  up to ten minutes later in the worst case. A stop request has the same
  granularity: the loop checks for it at the top of each iteration
  (`chi/orchestrator/loop.py:206`).
- **It is advice, not enforcement.** The text goes into a prompt. The harness
  doesn't check that the agent followed it. The parts of chi that do enforce
  behavior are separate: the [watchdog](/blog/stop-coding-agent-looping/) reaps
  a stalled loop, and budgets stop spend.
- **The directive is only as good as its wording.** The template's own
  conventions (numbered `§` directives, `SUPERSEDES §N`, a `DEAD — do not retry`
  heading) are from my hand-run fleet, not a spec models are trained on. I have
  no measurement that they raise obedience.

The larger gaps in chi are listed in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Try it

Run `chi run examples/offline.yaml`, then `chi steer` at the run directory it
prints and open `steering.md`. It takes about ten seconds and costs $0. The
[getting started guide](/docs/getting-started/) covers a run with real
coders, where steering starts to matter. If it's useful, a star on the
[repo](https://github.com/kdpisda/chi) helps other people find it.
