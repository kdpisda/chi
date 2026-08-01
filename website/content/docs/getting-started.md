---
title: "Getting started"
description: "Install chi, open a session, configure providers, and run your first autoresearch loop."
weight: 10
---

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- At least one LLM provider: an API key (Anthropic, OpenAI, DeepSeek, GLM,
  MiniMax, ... — anything LiteLLM routes) **or** an installed vendor CLI
  (`claude`, `codex`, `grok`)
- Optional: Docker, if you want sandboxed coders

## Install

Install chi from PyPI (the distribution is named `getchi`; the command stays `chi`):

```sh
uv tool install getchi
```

From source, for development:

```sh
git clone https://github.com/kdpisda/chi
cd chi
uv tool install --editable .   # or: uv venv --python 3.12 && uv pip install -e ".[dev]"
```

## Open a session

```sh
chi
```

Bare `chi` opens the full-terminal session (Textual UI): a scrolling
transcript, a bottom input with a slash-command dropdown, a live status bar,
and modal fuzzy pickers. `chi --plain` gives a minimal line-based REPL, used
automatically when stdout is not a terminal.

Free text in the session goes to chi's **operator** — an LLM with tools over
the engine. It starts runs, steers them, and answers questions from the run
store; it never invents numbers. Slash commands are the deliberate moves:

| Command | What it does |
|---|---|
| `/setup` | apply the recommended model setup for this machine |
| `/vendors` | pick providers (fuzzy picker; alias `/providers`) |
| `/models` | pick coder models — saved as your global defaults |
| `/setkey <provider>` | store an API key (masked input, saved `0600`) |
| `/run [fleet.yaml]` | start a run; iteration results stream in live |
| `/status` | run state |
| `/steer <text>` | send a steering directive (or just type — free text during a run steers) |
| `/stop` | stop the active run at the next iteration boundary |
| `/ledger [negative]` | show experiments, or the dead-ends ledger |
| `/champion` | show the best candidate (`--export <file>` writes the verified source) |
| `/director` | replay the rounds the autonomous director has run |
| `/resume [run_id]` | reattach to any past session; replays a director run's rounds |
| `/quit` | leave the session |

## First run (no API key)

See the whole loop end to end with zero setup — a scripted fleet that really
evaluates and improves a champion, no key, no network:

```sh
chi run examples/offline.yaml
```

It runs against `problems/optimize_function` — a pure-Python "make this function
faster" problem — and collapses an O(n²) baseline to an O(n) champion (~25 ms →
~0.06 ms) at $0.

## Let it run on its own

With models configured (`/setup` or `/setkey`), hand the operator a goal and it
starts the [director](/docs/concepts/#the-director) — chi's research loop that
runs the fleet in rounds, reviews its own results, researches when stuck, and
re-steers the agents on its own:

```
› improve problems/optimize_function on its own until I stop you
```

Give it a stop condition and walk away — the director halts itself when the goal
or the budget is met:

```
› get problems/optimize_function under 0.001 ms, then stop
› improve it on its own but don't spend more than $2
```

Stop it anytime with `/stop` (or a bare "stop"); watch progress with `/director`.

## Non-interactive use

Everything works headless for scripts and CI:

```sh
chi providers --enable anthropic,deepseek
chi providers --probe            # actually run each installed CLI to confirm it works
chi models --pick anthropic/claude-sonnet-5,claude
chi validate examples/fleet.yaml
chi ping --fleet examples/fleet.yaml
chi run examples/fleet.yaml
chi steer runs/<run_id> "stop micro-tuning; try itertools"
chi status runs/<run_id>
chi ledger runs/<run_id> --negative
chi champion runs/<run_id> --export best.py
```

## A minimal fleet.yaml

```yaml
run_name: toy
problem: problems/optimize_function
budgets:
  total_usd: 2.0
  per_role_usd: { coder: 1.5 }
coders:
  - { id: c1, model: anthropic/claude-sonnet-5, adapter: litellm_loop }
policies:
  max_iterations: 10
```

Budgets are hard caps, enforced per run and per role. Next: the moving parts,
in [Concepts](/docs/concepts/).
