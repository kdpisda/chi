# Chi (χ)

Chi ("kai") is an open-source autoresearch harness: point a fleet of LLM coding
agents (any vendor) at any problem with a programmatic evaluator — a build, a
correctness check, and a score — and let them iterate unattended.

Status: v1 (Phases 0–1) — single coder agent, two adapters (headless vendor
CLIs and a LiteLLM tool loop), enforced SQLite+JSONL run store, hard budget
caps, two-layer steering, deterministic watchdog.

## Quick start

    uv venv --python 3.12 && uv pip install -e ".[dev]"
    cp .env.example .env          # add your provider keys
    uv run chi validate examples/fleet.yaml
    uv run chi ping --fleet examples/fleet.yaml
    uv run chi run examples/fleet.yaml

While a run is live, steer it (optional — runs are unattended by default):

    uv run chi steer runs/<run_id> "stop micro-tuning; try itertools"

Inspect results:

    uv run chi status runs/<run_id>
    uv run chi ledger runs/<run_id> --negative
    uv run chi champion runs/<run_id> --export best.py

## Define your own problem

A problem is a directory with a `problem.yaml` (see
`problems/optimize_function/`): entrypoint commands for correctness and
benchmark, held-out seeds, and a score metric/direction. Correctness is a hard
gate; candidates never see reference outputs.

## Coder adapters

- `litellm_loop` — tools-in-a-loop over any LiteLLM-routable model (Anthropic,
  OpenAI, DeepSeek, GLM, MiniMax, ...).
- `cli_subprocess` — drives a headless vendor CLI per iteration, e.g.:

      coders:
        - id: c1
          model: claude
          adapter: cli_subprocess
          command: "claude -p --permission-mode acceptEdits {prompt_file}"

- `scripted` — deterministic playback, used by the test suite.

## Design

See `docs/superpowers/specs/2026-07-25-chi-v1-design.md` (v1 design, grounded
in a multi-agent fleet postmortem) and `docs/product-spec-v1.md` (full product
spec). License: Apache-2.0.
