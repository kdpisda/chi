---
title: "chi vs pi-autoresearch vs karpathy/autoresearch: an honest comparison"
description: "Three ways to run an autoresearch loop: Karpathy's reference setup, a single-agent pi extension, and chi's multi-agent harness. What each does well, and when to pick it."
date: 2026-09-24T03:00:00Z
category: "Comparison"
tags: ["autoresearch", "pi-autoresearch", "karpathy autoresearch", "comparison", "alternatives"]
toc: true
---

Every autoresearch tool runs the same loop: change the code, measure it, keep
what's better, revert what isn't, repeat. The tools differ in what they put
*around* that loop: how many agents run it, what stops it fooling itself, and
what you're left with in the morning.

This is a fair comparison of three of them, written by the people who build one
of the three. We point out where the others are ahead. All details are from each
project's README and source as of **September 2026**.

## The short version

- **[karpathy/autoresearch](https://github.com/karpathy/autoresearch)** is the
  original. It's a deliberately tiny reference setup: one agent, one file
  (`train.py`), one metric (`val_bpb`), and a fixed 5-minute training budget
  on one NVIDIA GPU. Read it to understand the idea, or use it if LLM training
  is exactly your problem.
- **[pi-autoresearch](https://github.com/davebcn87/pi-autoresearch)** is an
  extension for the [pi](https://pi.dev/) coding agent. It runs one agent on
  **any repo**: you write a `measure.sh` that prints a metric and an optional
  `checks.sh`. It has the best ergonomics of the three: per-experiment git
  commits, a live dashboard, and a skill that turns the result into reviewable
  branches.
- **[chi](https://github.com/kdpisda/chi)** is a harness for **fleets** of
  agents from different vendors. It's built around the controls a long
  unattended run needs: a shared store with dedup, a negative-results ledger, a
  code-only watchdog, noise and holdout gates, hard dollar budgets, and a
  director that re-steers the fleet itself.

## Feature by feature

| | karpathy/autoresearch | pi-autoresearch | chi |
|---|---|---|---|
| What it is | reference repo + `program.md` | extension for the pi agent | standalone CLI harness |
| Agents | 1 (bring your own CLI) | 1 (pi) | a fleet, mixed vendors |
| Models | whatever agent you run | whatever pi is configured for | vendor CLIs (`claude`, `codex`, `grok`) + any LiteLLM model |
| What can be optimized | `train.py` for LLM training | any repo, via `measure.sh` | a problem pack: one candidate file + check + bench commands |
| Correctness gate | — (one metric) | optional `checks.sh` blocks a keep | required hard gate on held-out seeds |
| Protected evaluator | `prepare.py` is off-limits by instruction | — | holdout workload never enters an agent workdir; optional Docker eval sandbox |
| Noise handling | — | MAD confidence score (advisory) | NoiseGuard re-benchmarks wins (median-of-N) before they count |
| Overfitting check | — | — | **holdout gate**: re-scores the champion on an unseen workload |
| Dead-end memory | results log + agent context | `.auto/prompt.md` notes | negative-results ledger, hard-blocked in steering |
| Stall detection | — | re-prompts an idle agent after context compaction | deterministic watchdog: eval recency + repeated diff hashes |
| Autonomy | runs until you stop it | runs until stopped or `maxIterations` | director: rounds, rule-based classification, research when stuck |
| Spend control | — | `maxIterations`, provider key limits | hard USD caps per run and per role, plus a director cost ceiling |
| Output | modified `train.py`, results log | a commit per experiment + finalize-to-branches | exported champion file (hash-verified) + full run store |
| Dashboard | analysis notebook | terminal widget + browser dashboard | terminal session + JSON (`chi status`, `chi ledger`) |
| Iteration hooks | — | `before.sh` / `after.sh` | — |
| License | MIT | MIT | Apache-2.0 |

## Where pi-autoresearch is ahead

**It works on any repo, with no setup.** Its README lists the domains: test
speed, bundle size, build time, Lighthouse scores, LLM training. You write a
script that prints `METRIC name=number` and you're done. chi currently needs a
problem pack built around one editable file. For "make my test suite faster this
afternoon", pi-autoresearch is the shorter path today.

**Its output is easier to review.** Every experiment is committed, and the
`autoresearch-finalize` skill regroups the kept changes into independent,
reviewable branches. chi exports one verified champion file. The most common
complaint about autoresearch output is that humans can't review or merge it, so
this matters.

**Its reporting is richer.** An always-visible results table, a continuous
confidence score on every run, a browser dashboard, and per-experiment
`hypothesis` / `learned` notes. chi measures noise on every eval, but it only
acts on it when a candidate looks like a win.

We track all of these as gaps in chi's
[public gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).
A zero-config repo mode is the next big item.

## Where chi is ahead

**It runs many agents from different vendors, and they don't repeat each
other's work.** Different models explore different parts of a problem. chi runs
them in parallel, each in its own worktree, and every result goes to a shared
store keyed by code hash. If one agent proposes something another already tried,
that's a lookup, not a second run.

**It catches a win that doesn't transfer.** This is the most common criticism of
the whole pattern. The most-cited autoresearch result, a 53% speedup on
Shopify's Liquid engine, came with its own author's caveat: *"probably somewhat
overfit."* chi's optional [holdout gate](https://github.com/kdpisda/chi/blob/main/docs/holdout.md)
re-scores the champion on a workload that never enters any agent's workdir. It
flags a champion that moved the benchmark without actually getting faster, and
under the director that champion can't count as meeting your goal. You can see
it catch a cheat, offline, in
[this walkthrough](/blog/first-autoresearch-loop-no-api-key/#4-watch-chi-catch-a-cheat).

**Its controls don't rely on the model.** chi checks for stalls in code: 10
iterations with no new eval, or the same diff hash repeated, and the agent is
restarted. The director decides whether a round is improving, plateaued or stuck
using explicit rules, and the LLM's view is only advisory. Ruled-out approach
classes go into a ledger with evidence, and steering then blocks them outright.

**Its budgets are real.** Dollar caps per run and per role are enforced by the
harness, and the director stops itself at a target score or a cost ceiling you
give it in plain language.

**Correctness is required.** Every candidate has to pass a correctness command
on held-out seeds, against reference outputs the agent never sees, before its
score counts.

## Where karpathy/autoresearch fits

It isn't really competing with the other two. It's the reference design, and it
does what it set out to do. Its choices (one file to edit, an evaluator the agent
can't touch, a fixed time budget so every experiment is comparable) are the right
ones, and both pi-autoresearch and chi build on them. If you want to understand
autoresearch, read its `program.md` first. If your problem is small-scale LLM
training on one GPU, run it as it is.

## Which one should you use?

- **You want to learn how autoresearch works** → karpathy/autoresearch.
- **You already use pi, and your target is a whole repo** (test time, bundle size,
  build time) → pi-autoresearch.
- **You have a well-defined evaluator and want to run for hours or days**,
  with several models, hard budgets, and protection against noise, dead ends,
  stalls and overfit wins → chi.

You can also combine them. A benchmark you've written for one tool usually just
needs wrapping to work in another.

*Found something wrong or out of date? The tools on this page change fast.
[Open an issue](https://github.com/kdpisda/chi/issues) and we'll fix the comparison.*
