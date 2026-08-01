---
title: "Problems & evaluators"
description: "Anatomy of a problem pack: problem.yaml, evaluator entrypoints, held-out seeds, two-tier evaluation, and fleet strategy."
weight: 30
date: 2026-08-01
---

chi is domain-agnostic. Anything that exposes a **programmatic evaluator** —
a way to build a candidate, check it is correct, and score it — can be a chi
problem: a function to make faster, a kernel to optimize, a heuristic to tune.

## A problem is a directory

A problem pack is a directory containing a `problem.yaml` plus whatever
scripts its entrypoints need. The bundled toy problem
(`problems/optimize_function`) is the reference:

```yaml
name: optimize_function
description: >
  Minimize the runtime of solve(xs) -> prefix sums of xs. Output must match
  reference.py within tolerance on all held-out seeds.
candidate: candidate.py
entrypoints:
  correctness: "{python} check.py {candidate} --seed {seed}"
  benchmark: "{python} bench.py {candidate}"
score:
  metric: runtime_ms
  direction: minimize
  repeats: 5
correctness:
  seeds: [11, 27, 43]
  tolerance: 1.0e-6
timeout_seconds: 60
```

The pieces:

- **`candidate`** — the file agents edit. Each coder works on its own copy in
  an isolated worktree.
- **`entrypoints`** — shell commands chi runs; `{python}`, `{candidate}`, and
  `{seed}` are substituted. `correctness` must exit 0 for a pass; `benchmark`
  prints the score. `build` and `profile` entrypoints are supported for
  compiled targets.
- **`score`** — the metric name, whether to minimize or maximize, and how many
  repeats to run (timings are noisy; chi records the spread, not just a point).
- **`correctness.seeds`** — held-out seeds. Correctness is a **hard gate**: a
  candidate that fails any seed can never become champion or be submitted, and
  candidates never see reference outputs. This is the guard against agents
  gaming the evaluator instead of solving the problem.
- **`timeout_seconds`** — a hung candidate is a failed candidate.

Validate a pack (or a fleet file) with:

```sh
chi validate problems/my_problem
```

## Scaffolding from an existing evaluator

If you already have a benchmark script, you don't have to write the pack by
hand. In a session:

```
› wrap the evaluator in ~/work/sim/bench.py as a chi problem
```

The operator's `scaffold_problem` tool delegates to a full-tool setup agent
that writes the `problem.yaml` and eval wrappers, then **verifies the pack by
running a baseline eval** before handing it back. Then point a run at the
returned directory.

## Two-tier evaluation

Real problems often have a cheap local signal and an expensive authoritative
one (a leaderboard, a remote GPU, a full test suite). chi treats these as
separate tiers:

- **Proxy tier** — fast and local; used for the inner loop, dozens to hundreds
  of runs per hour.
- **Authoritative tier** — rationed by a token bucket (e.g. one submission per
  window). chi only spends a token on a candidate that passed correctness on
  all seeds and beats the current champion by more than the measured noise.

Apparent improvements get re-benchmarked by **NoiseGuard** — a median-of-N
check against the champion — before chi treats them as real. Ranked
submissions to a live leaderboard additionally require a human; see
[Security](/docs/security/#submission-gating).

## Strategy and steering

You rarely tell coders *how* to solve the problem up front. Strategy lives in
`steering.md`, which the fleet hot-reloads between iterations:

- the director's strategist rewrites it every round — blocked dead-end
  classes, promising bases to build on, fresh strategies for lagging coders;
- your typed directives land there with priority
  (`stop micro-tuning; try itertools`).

Diversity comes from the fleet itself: heterogeneous models on separate
worktrees, deduplicated through the shared store, with the negative ledger
pruning the search space as it grows.
