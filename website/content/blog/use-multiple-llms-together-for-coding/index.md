---
title: "Use Multiple LLMs Together for Coding: One Mixed Agent Fleet"
description: "How to use multiple LLMs together for coding: one fleet config, three adapters, one shared store. What the agents share, what chi can't see, what's unmeasured."
date: 2026-10-29T02:34:48Z
draft: false
category: "Deep dive"
tags: ["multi-agent", "fleets", "coding agents", "autoresearch", "adapters"]
toc: true
newsletter:
  subject: "Two coders, same three candidates, one eval each"
  preheader: "What actually changes when a fleet mixes Claude, Codex and an API model."
  body: |
    Running two different coding agents against the same benchmark sounds like a scheduling problem. In chi it is mostly a bookkeeping problem, and the interesting part is what the agents share and what the harness can see of each.

    I wrote up how a mixed fleet is wired: one `coders:` list, three adapters, one SQLite store keyed by code hash. In a $0 run with two scripted coders handed identical candidates, the second coder's three evals were all cache hits.

    The post is also upfront about the part I have not measured: whether mixing models finds better candidates than running one model twice. [Read the walkthrough](https://getchi.dev/blog/use-multiple-llms-together-for-coding/).
---

You have a benchmark number to push and more than one coding agent you trust.
Maybe you pay for Claude Code and Codex, maybe you have an API key for a
third model. The obvious move is to run them all at once. The less obvious
part is what "together" means: do they talk, do they share a checkout, who
decides which candidate wins?

This post is about how to use multiple LLMs together for coding in an
autoresearch loop (agents edit code, an evaluator scores it, the best
candidate is kept). I'll use chi, the harness I build, because I can show you
the exact lines that decide what the agents share. I ran the repro below on
2026-10-29 at `$0`.

## How do you use multiple LLMs together for coding?

Give each model its own working copy, point them all at the same evaluator,
and let a shared store decide the winner. The models never talk to each other.
In chi that is one `coders:` list in a fleet config, where each entry names a
model and an adapter, which is the piece that knows how to drive that kind of
agent:

```yaml
coders:
  - { id: c1, model: claude, adapter: json_stream,
      command: 'claude -p "Follow the instructions in {prompt_file} exactly." --allowedTools Bash,Edit,Write,Read' }
  - { id: c2, model: codex, adapter: cli_subprocess,
      command: 'codex exec --skip-git-repo-check --sandbox workspace-write "Follow the instructions in {prompt_file} exactly."' }
  - { id: c3, model: anthropic/claude-sonnet-5, adapter: litellm_loop }
```

I loaded that exact file with `load_fleet` on 2026-10-29 and it validated.
(The two `command` strings are the templates chi ships in
[`chi/providers/catalog.py`](https://github.com/kdpisda/chi/blob/main/chi/providers/catalog.py).
I did not run the real CLIs for this post, since that needs accounts and keys.)

## What are the three adapters, and how do they differ?

An adapter is the thing that runs one iteration of one agent. chi registers
four by name in
[`chi/agents/registry.py`](https://github.com/kdpisda/chi/blob/main/chi/agents/registry.py)
(lines 53-56), and `known_adapters()` returned this on my run:

```text
['cli_subprocess', 'json_stream', 'litellm_loop', 'scripted']
```

`scripted` replays canned candidates for tests and the
[offline demo](/blog/first-autoresearch-loop-no-api-key/). The other three are
the ones a mixed fleet uses:

| Adapter | Drives | What chi can see |
|---|---|---|
| `json_stream` | a vendor CLI in structured streaming mode (the claude recipe) | Each tool call, plus the cost and token counts the CLI reports |
| `cli_subprocess` | any headless CLI (the codex and grok recipes) | Its exit code, a timeout, and the evals it recorded. No tool calls, no cost |
| `litellm_loop` | any model LiteLLM can route, with a tool loop chi owns | Every request, because it goes through chi's own wrapper |

The "what chi can see" column is the real cost of mixing. A stream-parsing
adapter reads the final result event for `total_cost_usd`
([`json_stream.py:98`](https://github.com/kdpisda/chi/blob/main/chi/agents/json_stream.py)).
The plain subprocess adapter returns only an eval count and a note
([`cli_subprocess.py`](https://github.com/kdpisda/chi/blob/main/chi/agents/cli_subprocess.py)),
so the codex coder in the config above reports no cost at all. Budget caps
have a related blind spot, which I measured in
[the cost-cap post](/blog/limit-llm-api-cost-coding-agents/).

## What do the agents share?

Everything that matters goes through one store, and nothing else is shared.
In [`chi/orchestrator/loop.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py):

- Each coder gets its own copy of the problem directory,
  `workdir-<id>`, when there is more than one coder (line 402).
- Each coder runs in its own thread (line 419).
- An exception in one coder is caught and recorded, so it can't sink the
  others (line 229).
- All of them write to the same SQLite store, and experiments are keyed by
  the hash of the candidate source.

That last point has a visible effect. Before running any benchmark, the
evaluator looks the code hash up
([`chi/eval/runner.py:71`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py)),
and if any agent has already scored that exact source, it returns the stored
result marked `cached`. I checked it with the smallest fleet that shows it:
two scripted coders, `a` and `b`, both replaying
`examples/demo_candidates.json`, three iterations each.

```bash
chi run two-coders.yaml
```

```text
{ "run_id": "two-coders-772a1e", "iterations": 3,
  "baseline_score": 54.58, "champion_score": 0.084,
  "total_cost_usd": 0, "status": "done" }
```

Then I read the store. The `experiments` table held four rows: the baseline
and three by coder `a`. Coder `b` evaluated the same three candidates and
recorded nothing, and its events show iteration-complete with no `RESULT`
event, because each lookup hit a result `a` had already stored. Two agents
with the same idea paid for one benchmark.

Two caveats. Scripted coders are deliberately identical, so this shows the
mechanism and not how often real models collide. And the lookup is by exact
source hash, so two candidates that differ by a comment are two benchmarks.

## Do mixed models find better candidates than one model run twice?

I don't know, and I haven't measured it. chi's
[concepts page](/docs/concepts/) says different models excel at different
problem regions, and that is the design bet behind a mixed fleet. It is a
bet, not a result I can point at, and I have no controlled run that compares
a mixed fleet with a single-model fleet of the same size on the same problem.

What the code does give you is diversity you can steer. `resolve_strategy`
([`chi/config.py:132`](https://github.com/kdpisda/chi/blob/main/chi/config.py))
assigns each coder a strategy: the problem pack's list first, round-robin by
position, then the coder's own `strategy` field, then a default of
`strategy-<id>`. So you can give two copies of the same model different
starting directions, and you can give two different models the same one. If
you want to know which helps on your problem, run both and compare the
champion scores. The run store has the data.

## What does chi pick if you let it choose?

If you'd rather not write the list, `chi providers` detects what's installed
and the recommender builds a fleet from it
([`chi/providers/recommend.py`](https://github.com/kdpisda/chi/blob/main/chi/providers/recommend.py)).
Reading the code: it adds one vendor-CLI coder (claude through `json_stream`
if the CLI is on PATH, otherwise codex through `cli_subprocess`) and one
API coder on the strongest eligible model. It does not build a three-way mix
for you.

## Where this falls short

- **Subscription CLIs are opaque.** A `cli_subprocess` coder gives chi no cost
  and no tool-call trace, so the budget cap can't bound it and the run summary
  undercounts spend. See the [known gaps](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).
- **No evidence yet that mixing wins.** Treat the fleet as a way to spend
  several agents' attention on one evaluator, not as a measured improvement
  over one good model.
- **Same-hash dedup only.** Near-duplicate candidates still cost a benchmark.
- **The CLI recipes are shipped templates.** Vendor flags change, which is why
  `chi providers --probe` exists: it runs each installed CLI once before the
  run, so a broken template shows up before five failed iterations.

If you want to see the machinery without any keys, run the
[offline demo](/blog/first-autoresearch-loop-no-api-key/), then change the
`coders:` list. When you're ready for the real thing, the
[docs](/docs/concepts/) cover the fleet and its adapters, and the repo is at
[github.com/kdpisda/chi](https://github.com/kdpisda/chi).
