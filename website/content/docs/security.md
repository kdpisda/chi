---
title: "Security"
description: "The threat model, sandbox tiers, and why submission gating holds even under full autonomy."
weight: 40
date: 2026-08-01
---

chi orchestrates LLM agents that **write and execute code**, then runs that
code through an evaluator. That is inherently risky, and the security story is
explicit about what chi controls and what you must provide. The authoritative
reference is `SECURITY.md` in the repository; this page is the practical
summary.

## Threat model in one paragraph

Agent-generated code runs with real permissions. Problem material, fetched
pages, and repo contents can carry prompt injections that steer an agent.
A `problem.yaml`'s entrypoints are shell commands chi executes. So: treat any
run over untrusted input as running untrusted code, and only run problem packs
you trust or authored.

## Sandbox tiers

By default chi runs agents on the host, with the permissions of the user who
launched it. Two opt-in Docker tiers (`sandbox:` on a coder in `fleet.yaml`)
tighten that. Both mount **only the coder's workdir** read-write at
`/workspace`, run with `--rm`, and never mount leaderboard credentials.

| Tier | Network | Credentials | Use when |
|---|---|---|---|
| `docker` | none (`--network none`) | none | the agent doesn't need a vendor API from inside the container. Full jail: no exfiltration channel. |
| `docker-cli` | bridge (egress) | read-only mounts of allowlisted vendor auth dirs (`~/.claude`, `~/.codex`, `~/.grok`) | CLI coders that need their vendor API and auth to run at all. **Deliberately weaker** — opt in knowingly. |
| *(none)* | host | host | trusted problems, maximum convenience. Prefer running chi itself inside a container or VM for a hard boundary. |

The `docker-cli` tradeoff, stated plainly: with network egress plus a valid
vendor credential, a prompt-injected agent can exfiltrate anything readable in
`/workspace`. What it still prevents: host filesystem access beyond the
workdir, reading any host credential other than the allowlisted CLI auth,
tampering with the mounted auth (`:ro`), and persistence (`--rm`).

The `chi-agent` image the sandboxes use ships python3, git, curl, Node 20, and
the `claude` CLI, running as a non-root user:

```sh
docker build -t chi-agent -f docker/chi-agent.Dockerfile .
```

```yaml
coders:
  - id: coder-1
    model: claude
    adapter: json_stream
    sandbox: docker-cli        # or "docker" for the full jail
    sandbox_image: chi-agent
```

## Submission gating

Authoritative submissions (e.g. to a live leaderboard) are guarded by layered,
mostly-structural controls:

- **Serialized and rationed.** A process-global mutex plus a token-bucket
  ration; optional human approval on top.
- **Auto-submit is conservative.** When enabled, it fires only on a correct
  candidate that beats the current best by a clamped margin.
- **NoiseGuard.** An apparent improvement is re-benchmarked N times; only a
  median that clears the promote margin counts as real.
- **Ranked submits are always manual.** Even under the fully autonomous
  director, chi surfaces the verified improvement and a human fires the ranked
  submission. Public rank changes irreversibly; one noisy benchmark is not
  enough evidence to automate that.
- **Structural enforcement.** Sandboxed agents never see leaderboard
  credentials — they *cannot* submit; chi submits from the host. On the local
  path, a CLI shim forwards benchmarks but refuses ranked submits outside
  chi's gate, and the `json_stream` adapter records any direct submission an
  agent attempts.

## Budgets and secrets

- Hard per-run and per-role USD caps; a request past the cap is blocked.
- Provider keys live in `.env` / `~/.config/chi/credentials.env` (created
  `0600`), never in tracked config.
- The operator's `explore` tool refuses to read obvious secret files out to
  the model, and its `fetch` tool blocks internal, loopback, and metadata
  addresses (SSRF), including via redirects.

## Supply chain

Direct dependencies carry version floors (and ceilings where a hazard is
known); `uv.lock` is the resolved ground truth. CI verifies the lockfile is in
sync and runs `pip-audit` against resolved dependencies.

## Reporting a vulnerability

Open a private security advisory on the
[repository](https://github.com/kdpisda/chi), or email the maintainer. Please
don't file public issues for security-sensitive reports.
