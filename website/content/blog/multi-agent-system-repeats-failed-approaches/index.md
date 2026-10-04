---
title: "Why Your Multi-Agent System Repeats Failed Approaches"
description: "Agents retry what already failed because nothing durable says it failed. How chi's negative-results ledger works, and where its 'do not retry' is only advice."
date: 2026-10-04T02:32:13Z
draft: false
category: "Deep dive"
tags: ["autoresearch", "negative results", "multi-agent", "loop reliability", "fleets"]
toc: true
newsletter:
  subject: "Our dead-ends list nearly killed the winner"
  preheader: "A ruled-out approach needs evidence, a scope, and a way to be challenged."
  body: |
    A fleet of coding agents will try the same bad idea again and again if nothing durable records that it failed. The usual fix is a shared dead-ends list. I went through how chi's version works, because a list like that has its own failure mode.

    An entry with no scope can rule out the approach that would have won. In my hand-run fleet, two rule-outs were tested in the wrong regime and nearly closed the winning direction.

    I ran the ledger offline and checked what it enforces and what it only asks nicely. The answer is narrower than "hard-blocked", and I say so in the post. [Read the walkthrough](https://getchi.dev/blog/multi-agent-system-repeats-failed-approaches/).
---

A multi-agent system repeats failed approaches because every agent starts each
session knowing only what is in its context. If iteration 4 found that a float32
cast breaks correctness, iteration 40, or a different agent, has no way to know
unless something outside the context says so. The fix is a durable record of
what failed, written with evidence and read before each attempt. The hard part
is what that record is allowed to say.

I built chi's version after a hand-run fleet kept re-exploring dead ends. Here is
how it works, what I ran to check it, and where "do not retry" is advice rather
than a lock.

## What goes in a negative-results ledger?

A negative-results ledger is a table of approaches that were tried and ruled out,
each with the evidence that ruled it out. In chi it is the `negative_ledger`
table ([`chi/store/schema.sql`](https://github.com/kdpisda/chi/blob/main/chi/store/schema.sql)):

| Column | Meaning |
|---|---|
| `approach_class` | The family of idea, such as `float32-cast` |
| `summary` | One line on why it failed |
| `evidence_json` | Measured numbers: seed, error, hit rate |
| `ruled_out_scope` | Where the rule-out applies. Defaults to empty |
| `status` | `active` or `challenged` |

Agents write to it through `chi deadend`, or through a `report_deadend` tool
in the LiteLLM-routed adapter. The CLI path is
`chi/cli.py:298-318`; the tool path is `chi/agents/litellm_loop.py:185-196`.

## Can an agent write a dead end without evidence?

No, on both paths. Here is the CLI against a finished offline run:

```text
$ chi deadend --run-dir $R --agent replay --approach-class float32-cast \
    --summary "float32 loses precision"
REJECTED: dead-end entries require non-empty --evidence-json
exit=1
```

A prose-only "this didn't work" is rejected. The agent has to attach something
checkable, such as `{"seed":7,"max_abs_err":0.013}`. The check is only that the
JSON is non-empty, so it can't tell whether the numbers are real. It stops the
lazy entry, not the dishonest one.

## Why can a dead-ends list kill the winner?

Because a rule-out is a claim about a regime, and an agent can test the wrong
one. In the forensic review of my manual fleet, two negative results were later
reversed after a human reset: they had been tested in the wrong regime or
structure, and unscoped negatives "nearly killed the winning direction"
([design notes](https://github.com/kdpisda/chi/blob/main/docs/superpowers/specs/2026-07-25-chi-v1-design.md)).
That was a hand-run fleet, not a chi run.

So the ledger has a `ruled_out_scope` field. Two honest caveats about it:

- **It is optional.** `chi deadend --scope` defaults to an empty string
  (`chi/cli.py:305`), and the `report_deadend` tool never asks for one. I
  recorded an entry with no scope and it went in without complaint.
- **Where it shows up depends on the path.** The vendor-CLI and JSON-stream
  adapters print each entry to the agent as
  `- [class] summary (scope: ...)`
  (`chi/agents/cli_subprocess.py:49-52`, `chi/agents/json_stream.py:128-131`).
  The steering digest prints only the class names.

## How does an agent get a rule-out reversed?

By filing a challenge: a distinguishing hypothesis against a specific entry.
`chi challenge --neg-id <id> --hypothesis "..."` inserts a row in `challenges`
and flips the entry's status to `challenged`
(`chi/store/ledger.py:170-185`). I ran it on the `float32-cast` entry from
above, then asked the steering digest what the fleet would be told:

```text
# Auto-steering digest (run offline-demo-4d2d52)
- Champion: 0.0659 (runtime_ms)
- Ruled out (1): lru-cache — do not retry without a distinguishing hypothesis (chi challenge).
```

I had recorded two dead ends (`float32-cast` and `lru-cache`). The digest lists
one. A challenged entry drops out of it, because the digest and the agents' seed
context both read only `active` entries (`list_negatives` defaults to
`status="active"`, `chi/store/ledger.py:149`;
`chi/orchestrator/steering.py:54`; `chi/agents/context.py:24`).

So filing a challenge **immediately lifts the ban**. The agent that challenges
can retry, and so can every other agent, whether or not the hypothesis holds up.
That is the design: the cost of a wrong rule-out (a lost winning direction)
is higher than the cost of one extra experiment, and the experiment goes through
the normal evaluator.

## Is "do not retry" actually enforced?

No. This is the part I got wrong in earlier posts. I described the ledger as
feeding a "hard" block, and that overstates it. Reading the code:

- The ledger is consulted in exactly one kind of place: text that is put in the
  agent's prompt (the seed context, the steering digest, the director's strategy
  file). The evaluator only looks up the experiments table to dedupe identical code
  (`chi/eval/runner.py:71`). I found no read of the negative ledger anywhere in
  `chi/eval/`. An agent that ignores the text can submit a ruled-out approach and
  it will be evaluated like any other candidate.
- The director's strategy file does label a class "(repeated — hard block)" once
  it has two or more entries (`chi/director/review.py:19`,
  `chi/director/strategy.py:36-40`). That is a label inside a markdown file for
  the next round's agents to read. Nothing in code blocks the class.
- The director counts classes with `dead_classes`, which counts entries of
  *any* status (`chi/store/ledger.py:86-93`). So a challenged entry leaves the
  agents' view but stays in the director's tally and in its "DEAD" list.
- A challenge row starts with `outcome = 'pending'`, and I found nothing in
  the code that ever updates it. After my challenge the row still read
  `pending`. The challenge lifts the ban, but chi does not record whether the
  hypothesis turned out right.

My 2026 hand-run fleet had the same property: the ledger "genuinely stopped
re-exploration — for agents that obeyed it" (same design notes). That is the
honest claim. A ledger is memory plus pressure, not a gate. It works well on
agents that read their prompt and badly on ones that don't.

I've corrected the two earlier posts that said "hard-blocked" (the
[overview](/blog/what-is-chi-autoresearch-harness/) and the
[comparison](/blog/chi-vs-pi-autoresearch-vs-karpathy-autoresearch/)). The
[concepts doc](/docs/concepts/) still uses the older wording.

## What would you copy from this, with or without chi?

Four rules that came out of the design, none of which need chi:

1. **Require evidence to write.** An empty-evidence write is rejected, not
   warned about.
2. **Record a scope, and show it to the agent.** If you build this yourself,
   make scope required. chi doesn't.
3. **Give agents a way to challenge**, and decide up front whether a challenge
   lifts the ban immediately (chi) or only when a re-test passes.
4. **Keep the agent's view and your tally separate.** chi hides challenged
   entries from agents but still counts them for the director. Know which one
   each consumer reads.

## Where this falls short

- Enforcement is prompt-level only, as above.
- Scope is optional and the steering digest drops it. Evidence is checked for
  presence, not truth.
- `approach_class` is a free-text string the agent picks. Two agents can name the
  same idea differently, and the ledger won't merge them. `chi query` finds
  entries by a literal substring match (`chi/store/ledger.py:156-167`).
- Challenges are never resolved in the data.

These are listed with the other open items in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).
I ran all of this offline against the scripted demo: the entries above were
written by hand with `chi deadend`, not by a live model, so it shows the
mechanism and not how often a real agent obeys it.

## Try it

The ledger is part of every run. To see an empty one fill up, start with the
[offline demo that needs no API key](/blog/first-autoresearch-loop-no-api-key/),
then run `chi ledger <run_dir> --negative` after adding an entry. If
re-tried failures are what costs you, the repo is at
[github.com/kdpisda/chi](https://github.com/kdpisda/chi), and a star helps other
loop builders find it.
