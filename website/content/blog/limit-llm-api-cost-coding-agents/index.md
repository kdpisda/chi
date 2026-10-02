---
title: "How to Limit LLM API Cost for Coding Agents (and Where Caps Leak)"
description: "A dollar cap only binds the spend it can see. How to limit LLM API cost for coding agents, with a $0 repro where chi logged $2.00 against a $0.50 cap."
date: 2026-10-02T02:32:15Z
draft: false
category: "Guide"
tags: ["coding agents", "budgets", "cost control", "autoresearch", "fleets"]
toc: true
newsletter:
  subject: "My $0.50 budget cap let $2.00 through. No error."
  preheader: "Which spend a cap actually sees, and what to do about the rest."
  body: |
    I set a $0.50 cap on a chi run and pointed it at a fake CLI coder that reports $0.40 per iteration. The run did all five iterations. The event log holds $2.00. The run summary says $0.

    That is a real gap in chi today, and the post walks through where it comes from: the cap only counts calls that go through chi's own LLM wrapper, and a vendor CLI's cost is logged but never charged against it.

    If you run agent loops overnight, the useful part is the checklist: which caps work, which leak, and the worst-case arithmetic for the ones you have to bound yourself. [Read the walkthrough](https://getchi.dev/blog/limit-llm-api-cost-coding-agents/).
---

You start an agent loop before bed with a dollar cap in the config. In the
morning the provider dashboard shows more than the cap. The question is not
whether the agent was greedy. It is whether the cap could ever see the spend.

This post is about how to limit LLM API cost for coding agents in an
autoresearch loop (a loop where agents edit code, an evaluator scores it, and
the best candidate is kept). I'll use chi, the harness I build, because I can
show you the exact line where its own cap stops binding. I ran the repro below
on 2026-10-02 at `$0`.

## How do you limit LLM API cost for a coding agent?

Enforce the cap in the code path that makes the paid call, not in the prompt.
Asking a model to be frugal is not a control. A real cap does three things on
every call: check the running total before the request, record the actual cost
after it, and refuse when the total is already over.

chi's version is `BudgetTracker` in
[`chi/providers/budgets.py`](https://github.com/kdpisda/chi/blob/main/chi/providers/budgets.py).
Every LiteLLM call goes through `chat()` in
[`chi/providers/llm.py`](https://github.com/kdpisda/chi/blob/main/chi/providers/llm.py),
which calls `budget.check(role=role)` first (line 44) and `budget.record(cost,
role=role)` after (line 57). A total cap and per-role caps are both supported:

```yaml
budgets:
  total_usd: 2.0
  per_role_usd: { coder: 1.5 }
```

That is the whole mechanism. Which means the cap covers exactly the calls that
pass through `chat()`, and nothing else.

## Does the cap stop at the cap?

Not exactly, because the check runs before the call and the cost is known only
after it. I ran a fake completion priced at $0.40 against a $0.50 cap:

```text
1 ran, cost 0.4 spent 0.4
2 ran, cost 0.4 spent 0.8
3 blocked: total budget $0.50 exhausted (spent $0.8000)
```

The second call started with $0.40 spent, under the cap, so it ran and took the
total to $0.80. The third was refused. So the real bound for this path is the
cap plus the cost of one call (more with several coders running in parallel,
since each can pass the check before another records). Size your cap with that
margin, and keep individual calls small.

Two smaller details from `llm.py`. If LiteLLM cannot price a model, `_cost_of`
returns `0.0` and the call counts as free (lines 18-32), so an unpriced model
is uncapped. And the tracker keeps its running total in memory:
`BudgetTracker.record()` writes it to the run's `budgets` table, but nothing
reads it back. Under the director, each round is a fresh `run_slice` that builds
a new tracker (`chi/orchestrator/loop.py:488`), so the per-run total restarts
every round. The check that does span rounds is the director's cost ceiling
(`chi/director/loop.py:183`), covered below.

## What happens when the coder is a vendor CLI?

The cap does not see it. This is the gap, and I'd rather you hear it from me.
A `json_stream` coder runs a vendor CLI such as `claude`, parses the final
`result` event, and reports its `total_cost_usd` in the iteration outcome
(`chi/agents/json_stream.py:98,200`). Nothing in that adapter calls
`budget.record`. The only caller of `record` in the package is `chat()`.

Here is the repro. A fake CLI that prints one result event costing $0.40:

```python
# fakecli.py
import json
print(json.dumps({"type": "result", "total_cost_usd": 0.40, "num_turns": 1,
                  "is_error": False,
                  "usage": {"input_tokens": 1000, "output_tokens": 100}}))
```

A fleet with a $0.50 total cap and five iterations:

```yaml
run_name: cap-test
problem: problems/optimize_function
budgets:
  total_usd: 0.50
coders:
  - id: fake
    model: fake-cli
    adapter: json_stream
    command: "python3 fakecli.py {prompt_file}"
policies:
  max_iterations: 5
```

`chi run` finished all five iterations and printed `"total_cost_usd": 0`,
`"status": "done"`. Then I read the run's SQLite store:

```text
ITERATION_COMPLETE cost_usd: 0.4  (x5)    sum(cost_usd) = 2.0
BUDGET_BLOCK events: 0
budgets table: empty
```

The per-iteration cost is on the events, so the money is visible after the
fact. It is just never charged against the cap, and the run summary's total
comes from the same tracker, so it reads `$0`. The older
posts and docs on this site describe budgets as hard caps without that
qualifier; they are accurate for LiteLLM-routed coders and not yet for vendor
CLIs. I've corrected the two posts that said it, and the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md)
is where open gaps are tracked.

## How do you bound CLI coder cost today?

Bound it from the outside, with arithmetic you can check before you start.
There is one chi control that sees CLI spend: the director's cost ceiling. Each
round it sums `cost_usd` over the run's event log, which includes the CLI
coders' reported costs (`chi/director/round.py:62-64`), and stops when that
reaches the ceiling you set (`chi/director/loop.py:183`). It only checks
between rounds, so a round can overshoot. Reading the code, it also adds that
running total to its own counter each round (`loop.py:92`), which would make it
stop earlier than the number you gave it. I haven't run a multi-round director
to confirm that, so treat the ceiling as conservative, not exact.

Inside a round, three knobs bound a CLI coder, none of them a live dollar cap:

- `policies.max_iterations` (default 20, `chi/config.py:32`) limits how many
  times the CLI is launched.
- `policies.iteration_timeout_seconds` (default 600, line 33) limits how long
  each launch can run.
- The vendor's own cap. As of October 2026, Claude Code's CLI reference lists
  `--max-budget-usd` as "Maximum dollar amount to spend on API calls before
  stopping (print mode only)", with the cap-enforcement behaviors requiring
  v2.1.217 or later
  ([source](https://code.claude.com/docs/en/cli-reference), fetched 2026-10-02).

If you put a per-launch cap in the coder's `command`, the worst case is
`max_iterations x per-launch cap`, plus a little overshoot per launch. For 20
iterations at $1 that is about $20, which is a number you can decide you
accept. I haven't run that flag through chi's adapter, so check the cost the
events record on a one-iteration run before you trust it overnight. The
iteration costs are in the run's SQLite store either way (`chi.db` in the run
directory), and this is the query I used:

```sql
SELECT SUM(cost_usd) FROM events;
```

## Where this falls short

chi's budget is a hard cap only for coders that call models through its own
wrapper. CLI coders, unpriced models and per-round resets under the director
all leak, and I haven't fixed any of them yet. The director's ceiling is the
backstop, and it only acts between rounds. A cap that blocks the next call
also cannot refund the one in flight. If a hard dollar limit matters more to
you than anything else, set the limit at the provider (a key with a spend limit
is the one control no harness bug can bypass) and treat chi's cap as a second
line.

To see the loop, the event log and the `$0` accounting in about ten seconds,
start with the [offline demo](/blog/first-autoresearch-loop-no-api-key/). The
[getting started guide](/docs/getting-started/) has the `budgets:` block, and
[concepts](/docs/concepts/) explains the director.
