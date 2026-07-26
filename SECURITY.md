# Security

## Threat model — read this before running chi unattended

Chi orchestrates LLM coding agents that **write and execute code**, and it runs
that agent-generated code through an evaluator. This is powerful and inherently
risky. Be explicit about what chi does and does not protect against.

### What chi runs
- **Agent-generated code**, executed by the eval harness (build / correctness /
  benchmark entrypoints) and, for `cli_subprocess`/`json_stream` coders, by the
  vendor CLI itself (which may have shell/file access).
- **Candidate submissions** to external evaluators (e.g. a leaderboard).

### In scope (chi provides controls)
- **Submission gating.** Ranked/authoritative submissions go through a
  process-global mutex + token-bucket ration + (optional) human approval
  (`chi/eval/submission.py`, `chi/eval/autosubmit.py`). Auto-submit only fires on
  a correct candidate that beats the current best by a clamped margin.
- **Agent submission mediation.** A `popcorn-cli` shim on the agents' PATH
  (`chi/eval/popcorn_shim.py`) forwards benchmarks but refuses *ranked* submits
  unless they come through chi's gate. The `json_stream` adapter additionally
  *records* any direct submission a structured-mode agent attempts.
- **Budgets.** Hard per-run and per-role USD caps (`chi/providers/budgets.py`).
- **Correctness gate.** Candidates that fail held-out seeds can never become
  champion or be submitted; candidates never see reference outputs.
- **No secrets in config.** Provider keys live in `.env` /
  `~/.config/chi/credentials.env` (created 0600), never in tracked config;
  `explore` refuses to read obvious secret files out to the model; `fetch`
  blocks internal/loopback/metadata hosts (SSRF).

### Out of scope (you must provide these)
- **Sandboxing of agent-executed code.** Chi does **not** yet run agents or their
  generated code in a sandbox. A coder agent with shell access runs with the
  permissions of the user who launched chi. The PATH shim mediates *submission*,
  not arbitrary code execution — a determined agent (or a prompt-injected one)
  can still touch the host. **If you run untrusted problems or want a hard
  boundary, run chi inside a container or VM.** A real per-agent sandbox
  (micro-VM, à la the pi harness's Gondolin) is the top remaining hardening item
  — see `docs/pi-substrate-adoption.md`, item 2.
- **Prompt injection.** Problem material, fetched pages, and repo contents can
  carry instructions that steer an agent. Treat any run over untrusted input as
  running untrusted code.
- **Malicious problem definitions.** A `problem.yaml`'s entrypoints are shell
  commands chi executes. Only run problem packs you trust or authored.

## Supply chain
- Direct dependencies carry version floors and, where a build/runtime hazard is
  known, ceilings (see `pyproject.toml`). `uv.lock` is the resolved ground truth.
- CI (`.github/workflows/ci.yml`) verifies the lockfile is in sync (`uv lock
  --locked`) and runs `pip-audit` against resolved dependencies.
- **Hardening still to do** (mirroring the pi harness's checklist): pin GitHub
  Actions to commit SHAs, add a dependency-review gate on PRs, and a
  minimum-release-age policy to avoid same-day compromised releases.

## Reporting a vulnerability
Open a private security advisory on the repository, or email the maintainer.
Please do not file public issues for security-sensitive reports.
