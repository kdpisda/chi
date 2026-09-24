---
title: "Run your first autoresearch loop in 10 seconds (no API key)"
description: "Install chi, run a real autoresearch loop offline with zero API keys, read the run store, then watch the holdout gate catch an overfit champion."
date: 2026-09-24T02:00:00Z
category: "Guide"
tags: ["getting started", "tutorial", "autoresearch", "benchmarking"]
toc: true
---

Most agent tools want an API key before they'll show you anything. chi ships a
demo that doesn't. A **scripted** coder replays canned candidates, but everything
else is real: the evaluator, the run store, champion selection and the watchdog.
You see the whole harness work before spending a cent.

## 1. Install

chi is on PyPI as `getchi`. The command you run is `chi`.

```sh
uv tool install getchi      # or: pip install getchi
```

The demo configs live in the repo, so clone it and run from the root:

```sh
git clone https://github.com/kdpisda/chi && cd chi
```

## 2. Run the offline demo

```sh
chi run examples/offline.yaml
```

This runs the bundled `optimize_function` problem, *"make this prefix-sum function
faster"*. Its baseline is deliberately O(n²), re-summing the whole prefix at
every index:

```python
def solve(xs: list[float]) -> list[float]:
    return [sum(xs[: i + 1]) for i in range(len(xs))]
```

The scripted coder then plays three candidates in turn: the same naive
approach, a hand-written O(n) running total, and a rewrite with
`itertools.accumulate`. Each one goes through the correctness gate on three
held-out seeds and then gets benchmarked. The run ends with a summary like this:

```json
{
  "run_id": "offline-demo-ec3e17",
  "iterations": 3,
  "baseline_score": 46.46,
  "champion_score": 0.068,
  "total_cost_usd": 0,
  "status": "done",
  "holdout": null
}
```

Scores are `runtime_ms`, so your exact numbers depend on your machine. The shape
won't change: the O(n) `itertools.accumulate` rewrite beats the O(n²) baseline
by several hundred times, for $0.

To watch the `★ new best` lines stream in live, run the same config inside the
interactive session instead: type `chi`, then `/run examples/offline.yaml`.

## 3. Read what happened

Everything the run did is in the store, and three commands read it back.

**`chi ledger`** lists every experiment, keyed by code hash, with its score and
measured noise:

```sh
chi ledger runs/<run_id>
```

```json
{"author": "baseline", "correct": 1, "seeds_passed_json": "[11, 27, 43]",
 "score_value": 46.46, "noise_std": 17.68, ...}
{"author": "replay", "correct": 1, "score_value": 0.098, "noise_std": 0.0045, ...}
{"author": "replay", "correct": 1, "score_value": 0.068, "noise_std": 0.0091, ...}
```

**`chi status`** gives the run's state plus its raw event log: iteration
starts, results, heartbeats and the stop.

**`chi champion --export`** writes out the winning source. The export is
hash-checked against the archived candidate, so what you get is the version that
was actually scored, not whatever a coder happened to write last:

```sh
chi champion runs/<run_id> --export best.py
```

```python
import itertools


def solve(xs: list[float]) -> list[float]:
    # O(n) in C: itertools.accumulate does the running sum natively.
    return list(itertools.accumulate(xs))
```

## 4. Watch chi catch a cheat

A benchmark win isn't always a real win. The second offline demo replays a
candidate that is correct everywhere but only fast on the benchmark's exact
input size:

```python
def solve(xs):
    if len(xs) == 4000:          # the benchmark's input size
        return list(itertools.accumulate(xs))
    return [sum(xs[: i + 1]) for i in range(len(xs))]
```

```sh
chi run examples/holdout.yaml
```

It takes the score, and the **holdout gate** catches it. chi re-scores the
champion on a workload whose files never enter an agent's workdir, then compares
the gain it claimed with the gain it actually delivered:

```json
"holdout": {
  "verdict": "overfit",
  "claimed_gain_pct": 99.85,
  "holdout_gain_pct": -1.62,
  "detail": "claims +99.8% on the benchmark but realises only -1.6% held out (-2% of the claim, floor 50%)"
}
```

Under the director, an overfit champion can't satisfy a target score, and
`chi champion --export` warns before you ship it. Swap the script for
`examples/holdout_honest.json` and a genuine rewrite passes the same gate.
[Read how the holdout works](https://github.com/kdpisda/chi/blob/main/docs/holdout.md).

## 5. Point real models at it

Once you've seen the loop work, swap the scripted coder for a real one. Store a
key and pick a model from inside the session (`/setkey`, `/models`), or write a
small fleet file:

```yaml
run_name: toy
problem: problems/optimize_function
budgets:
  total_usd: 2.0                 # hard cap for the whole run
  per_role_usd: { coder: 1.5 }
coders:
  - { id: c1, model: anthropic/claude-sonnet-5, adapter: litellm_loop }
policies:
  max_iterations: 10
```

```sh
chi validate fleet.yaml && chi run fleet.yaml
```

Or skip the YAML and just tell the session's operator what you want. It starts
the autonomous director, which runs until the goal or the budget is met:

```
› get problems/optimize_function under 0.001 ms, then stop
› improve it on its own but don't spend more than $2
```

## Next

- [Getting started](/docs/getting-started/): providers, models, every slash command.
- [Concepts](/docs/concepts/): the director, store, ledger, watchdog and steering.
- [Problems & evaluators](/docs/problems/): wrap your own evaluator in a problem pack.
