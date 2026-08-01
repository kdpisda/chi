---
title: Changelog
description: "What shipped in each chi release."
date: 2026-08-01
---

# Changelog

The docs on this site track the latest release. Older versions link to their
GitHub release notes from the version selector in the header.

## v0.2.0

The autonomous director became genuinely usable, plus safety and onboarding
hardening — driven by a multi-agent usability audit against real user journeys.

**New**

- **Director self-stop** — give it a goal and walk away: *"get it under 500µs then
  stop"* sets a target score, *"don't spend over $5"* sets a cost ceiling. The
  director halts itself when the goal or cap is met.
- **Sandboxed eval** — run an untrusted candidate's correctness + benchmark inside a
  jail (`eval_sandbox: docker`), so a hostile candidate can't reach the host.
- **Offline, no-key demo** — `chi run examples/offline.yaml` improves a champion with
  zero API keys (a scripted fleet). See [Getting started](/docs/getting-started/).
- **`/director` and `/resume` replay** — see the rounds a director run completed,
  read back from the run store.
- **NoiseGuard for local noisy evals** — median-of-N verification of an apparent
  improvement, not just for leaderboard problems.

**Fixed**

- **The director is stoppable** — `/stop` (and a bare "stop") now halt a running
  director; quitting warns instead of silently killing it.
- **Export ships the *verified* champion**, not whatever the coder last wrote — every
  scored candidate is archived by hash and the export is hash-checked.
- **Domain-generic brain** — the director's research and steering prompts are
  templated from your problem, no longer hardcoded to a specific CUDA kernel.
- A perma-plateau now escalates to a research round; a zero-eval timeout steers the
  coder to a smaller edit; `query_ledger` searches the right tables.

## v0.1.0

First public release: the autoresearch harness, the conversational operator, the
multi-agent fleet, the SQLite blackboard with a negative-results ledger, the
deterministic watchdog, two-tier eval with submission gating, and the Textual UI.
