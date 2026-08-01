---
title: "Concepts"
description: "The director, the fleet, the blackboard store, the negative ledger, the watchdog, and steering — chi's moving parts and why each exists."
weight: 20
date: 2026-08-01
---

chi's design comes from a postmortem of running a multi-model agent fleet by
hand: the fleet kept re-trying ruled-out approaches, wasted scarce
authoritative evals, stalled silently, and had no clean way to be steered.
Each mechanism below removes one of those failure modes.

## The director

The director turns one human task into sustained auto-research. You give a
goal once — `keep improving this kernel until I stop you` — and chi runs the
fleet in rounds without per-round human steering. Each round:

1. **Run** — the fleet runs a bounded slice (a few iterations), then hands control back.
2. **Review** — a deterministic digest is built from the store: champion,
   dead-end classes, near-parity candidates, per-coder progress.
3. **Classify** — explicit rules decide `improving | plateaued | stuck`. The
   LLM's read is advisory; the rule decides, so the loop can't talk itself in
   circles.
4. **Research** — only when *stuck*: one web-capable call that must return
   concrete, genuinely different techniques.
5. **Steer** — the strategist rewrites `steering.md`: a hard "do not retry"
   block for repeated dead classes, a "promising bases" block for near-misses,
   researched ideas, and a new strategy for the weakest coder on a plateau.
   Coders hot-reload it.

Supervising it is deliberately thin:

- **Watch.** Every round emits one line —
  `round N: <state> · best <score> · Σ <benches> $<cost>`. The director has no
  auto budget cap; that running cost counter is your guardrail.
- **Interject.** Just type. Plain text while the director runs is folded into
  the next round as a priority directive; it never interrupts the in-flight slice.
- **Detach.** Ctrl-D detaches, the loop keeps running; `/resume` reattaches
  and replays the rounds from the store.
- **Stop.** `/stop` halts at the next round boundary. The director stops
  itself only if the eval goes dead.

One hard rule survives full autonomy: **ranked leaderboard submissions stay
manual.** When a candidate looks like a new champion, NoiseGuard re-benchmarks
it and only a median that clears the promote margin counts as real — then the
director surfaces the verified improvement and *you* fire the submission. A
ranked submit changes public state irreversibly; a single noisy benchmark is
not evidence enough to automate that.

## What is the operator?

Free text in a session goes to the operator: an LLM with tools over the
engine (`start_run`, `steer`, `query_ledger`, `start_director`,
`scaffold_problem`, read-only `explore` and `fetch`, ...). Two properties
matter:

- **It never invents state.** Scores, dead ends, and status come from tools
  over the store, or not at all.
- **It discovers before it asks.** It lists directories and fetches pages
  itself instead of asking you to run commands for it.

If you have a vendor CLI but no API key, the operator can run through the CLI
itself using a JSON-action protocol — chi executes the actions, so the CLI
needs no tool permissions.

## The fleet

Coders are deliberately heterogeneous — different models demonstrably excel at
different problem regions — and each one runs behind a small `Agent` protocol
with a pluggable adapter:

| Adapter | What it drives |
|---|---|
| `json_stream` | a vendor CLI in structured streaming mode (`claude -p --output-format stream-json`). chi parses the typed event stream: it sees every tool call the agent makes, reads real cost and token counts, and records any direct submission attempt. Default for claude coders. |
| `cli_subprocess` | a headless vendor CLI per iteration (codex, grok, or anything with a batch mode) |
| `litellm_loop` | tools-in-a-loop over any LiteLLM-routable model |
| `scripted` | deterministic playback, used by the test suite |

Adapters register by name in a registry, so adding a new substrate is a
registration, not a core edit. Before a run, `chi providers --probe` actually
executes each installed CLI to confirm the account, model, and flags work —
broken templates and rejected accounts surface before the run, not five
silent failures in.

## The blackboard store

Agents never talk to each other directly. Every iteration writes to an
enforced store — SQLite plus append-only JSONL — that holds tasks,
experiments keyed by code hash, findings, and scores. Direct consequences:

- **Dedup.** Re-proposing an already-tried candidate is a lookup, not a re-run.
- **Reconstructability.** Any agent can be respawned with a fresh context
  seeded purely from the store — chi's answer to context rot.
- **Auditability.** `/ledger`, `/champion`, and the director's review digest
  are all queries over the same records that drove the run.

## The negative-results ledger

The single biggest failure of a hand-run fleet is re-exploring dead ends.
chi makes negative results first-class: a ruled-out approach class is recorded
with evidence (failure mode, seed, error magnitude) and scope. The ledger
feeds the director's "do not retry" steering block, and `chi ledger --negative`
shows you exactly which roads are closed and why.

## The watchdog

A deterministic, zero-LLM monitor runs alongside the fleet. It watches
**eval recency** (an agent that has stopped producing evaluations is stalled,
whatever its transcript says) and **repeated diff hashes** (an agent emitting
the same candidate is looping). Detection kills and respawns the agent from
the store. No model is asked to self-report being stuck — LLMs are bad at
that, and the monitor costs nothing.

## Steering

Steering is two-layer:

- **The harness steers itself.** The director's strategist rewrites
  `steering.md` every round (dead-class blocks, promising bases, new
  strategies). Coders hot-reload it between iterations, never mid-eval.
- **You steer whenever you want.** Typed text during a run becomes a
  directive; headless, `chi steer runs/<id> "..."` does the same. Human
  directives take priority in the next round.
