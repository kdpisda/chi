# Chi (χ)

Chi ("kai") is an open-source autoresearch harness: point a fleet of LLM coding
agents (any vendor) at any problem with a programmatic evaluator — a build, a
correctness check, and a score — and let them iterate unattended.

Status: v1 (Phases 0–1) — single coder agent, two adapters (headless vendor
CLIs and a LiteLLM tool loop), enforced SQLite+JSONL run store, hard budget
caps, two-layer steering, deterministic watchdog.

## Quick start

    uv tool install --editable .   # or: uv venv --python 3.12 && uv pip install -e ".[dev]"
    chi                            # opens the full-terminal session (Textual UI)

The session has a Claude Code-style interface: scrolling transcript, bottom
input with a slash-command dropdown, live status bar, and modal fuzzy pickers.
`chi --plain` gives a minimal line-based REPL (also used automatically when
stdout is not a terminal).

Inside the session:

    /vendors            pick providers (fuzzy); store keys with `chi providers --set-key X`
    /models             pick coder models — saved as your global defaults
    /run fleet.yaml     start a run; iteration results stream in live
    just type           plain text while a run is active becomes a steering directive
    /stop  /status  /ledger  /champion  /quit

Everything also works non-interactively for scripts and CI:

    chi providers --enable anthropic,deepseek
    chi models --pick anthropic/claude-sonnet-5,claude
    chi validate examples/fleet.yaml
    chi ping --fleet examples/fleet.yaml
    chi run examples/fleet.yaml
    chi steer runs/<run_id> "stop micro-tuning; try itertools"

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
