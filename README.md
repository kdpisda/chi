# Chi (χ)

Chi ("kai") is an open-source autoresearch harness: point a fleet of LLM coding
agents (any vendor) at any problem with a programmatic evaluator — a build, a
correctness check, and a score — and let them iterate unattended.

Status: v1 (Phases 0–1) — single coder agent, two adapters (headless vendor
CLIs and a LiteLLM tool loop), enforced SQLite+JSONL run store, hard budget
caps, two-layer steering, deterministic watchdog.

## Try it in 10 seconds (no API key)

Chi ships an offline demo that needs no provider, no key, and no network. The
`scripted` adapter replays three canned candidates for the bundled
`optimize_function` problem and really evaluates each one:

    uv tool install getchi
    chi run examples/offline.yaml   # run from a checkout of this repo

You'll see the champion beat the O(n²) baseline as the run replaces it with the
O(n) `itertools.accumulate` rewrite — a real "★ new best", zero setup:

    "baseline_score": 25.18,   # O(n²) prefix sums
    "champion_score": 0.055,   # O(n) itertools.accumulate
    "status": "done"

Run it inside the interactive session (`chi`, then `/run examples/offline.yaml`)
to watch the `★ new best` lines stream in live. This is the whole harness —
evaluator, run store, champion selection, watchdog — exercised with no key.

## Quick start

    uv tool install getchi         # real install; then just run `chi`
    chi                            # opens the full-terminal session (Textual UI)

For local development instead: `uv tool install --editable .` (or
`uv venv --python 3.12 && uv pip install -e ".[dev]"`).

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

- `scripted` — deterministic playback of canned candidates; powers the test
  suite and the no-key `examples/offline.yaml` demo.

## Design

See `docs/superpowers/specs/2026-07-25-chi-v1-design.md` (v1 design, grounded
in a multi-agent fleet postmortem) and `docs/product-spec-v1.md` (full product
spec). License: Apache-2.0.
