---
title: "chi — an autoresearch harness"
description: "chi (χ) is an open-source autoresearch harness: point a fleet of coding agents at any problem with a programmatic evaluator and let it improve autonomously until you stop it."
hero:
  eyebrow: "chi (χ), pronounced \"kai\" · open source · Apache-2.0"
  heading: "Point a fleet of coding agents at a problem. Let it *research*."
  lede: >
    chi is an **autoresearch harness**. Give it any problem with a programmatic
    evaluator — a build, a correctness check, a score — and it runs a fleet of
    heterogeneous coding agents that improves the answer autonomously, round
    after round, until you stop it.
features:
  - tag: "director/"
    title: "Autonomous director"
    body: >
      One task in. The director runs the fleet in rounds, meta-reviews results
      from the store, researches the web only when stuck, and rewrites the
      fleet's steering itself. Halts on a dead eval; otherwise runs until stopped.
  - tag: "fleet/"
    title: "Multi-agent fleet"
    body: >
      Heterogeneous coders — the `claude`, `codex`, and `grok` CLIs driven
      through structured JSON streams, or any LiteLLM-routable model in a tool
      loop. Different models explore different regions of the solution space.
  - tag: "store/"
    title: "Blackboard store"
    body: >
      Agents never chat directly. Every experiment, finding, and score lands in
      an enforced SQLite + JSONL store keyed by code hash — deduplicated,
      queryable, and complete enough to reconstruct any agent from scratch.
  - tag: "ledger/"
    title: "Negative-results ledger"
    body: >
      Dead ends are first-class data. Ruled-out approach classes carry evidence
      and get hard-blocked in steering, so the fleet stops re-exploring the
      same precision trick for the fifth time. This is the anti-plateau mechanism.
  - tag: "watchdog/"
    title: "Deterministic watchdog"
    body: >
      A zero-cost, no-LLM monitor watches eval recency and repeated diff hashes.
      A stalled or looping agent is detected and killed by code, not by asking
      a model whether it feels stuck.
  - tag: "eval/"
    title: "Two-tier eval, gated submits"
    body: >
      A cheap local proxy loop for the inner iterations; the authoritative tier
      is rationed by a token bucket and gated by correctness on held-out seeds.
      NoiseGuard re-benchmarks apparent wins — ranked submissions stay manual.
  - tag: "sandbox/"
    title: "Sandboxed agents"
    body: >
      Opt-in Docker tiers per coder: a full jail with no network and no
      credentials, or a vendor-CLI tier with read-only auth mounts. Leaderboard
      credentials are never mounted — agents physically cannot submit.
  - tag: "tui/"
    title: "Terminal-native session"
    body: >
      Bare `chi` opens a Claude Code-style session: scrolling transcript,
      slash-command dropdown, fuzzy pickers, live status bar. Free text talks
      to the operator; typed text during a run becomes a steering directive.
  - tag: "problems/"
    title: "Pluggable problems"
    body: >
      A problem is a directory with a `problem.yaml`: entrypoint commands,
      held-out seeds, a score metric. Ask the operator to scaffold one around
      an evaluator you already have — it writes and verifies the pack.
---
