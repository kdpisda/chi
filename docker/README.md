# chi-agent image

The container image chi's Docker sandboxes (`chi/agents/sandbox.py`) run coder
agents in. It ships the minimum an agent needs to work on a candidate —
python3, git, curl, ca-certificates, Node 20 — plus the npm-distributed
`claude` CLI, running as a non-root `agent` user with home `/home/agent`.

## Build

```sh
docker build -t chi-agent -f docker/chi-agent.Dockerfile .
```

Then point a coder at it in `fleet.yaml`:

```yaml
coders:
  - id: coder-1
    model: claude
    adapter: json_stream
    command: "claude -p ..."
    sandbox: docker-cli        # or "docker" for the full jail
    sandbox_image: chi-agent
```

The `codex` and `grok` CLIs are vendor-distributed binaries, not npm packages;
extend this image (`FROM chi-agent`) and add them if you run those coders
sandboxed.

## Security posture — two tiers

Both tiers mount **only the coder's workdir** read-write at `/workspace`, run
with `--rm`, and never mount popcorn/leaderboard auth — so chi's gated
submission holds by construction: the agent physically cannot submit; chi
submits from the host.

### `sandbox: docker` — full jail

`--network none`, no host home, no auth of any kind. Strongest tier: no
exfiltration channel, no credentials. Suitable for agents that don't need a
vendor API from inside the container.

### `sandbox: docker-cli` — vendor CLI tier (opt-in, weaker)

CLI coders (claude/grok/codex) cannot run in the full jail: they need (a)
network access to their vendor API and (b) their host auth state. This preset
trades isolation for usability:

- `--network bridge` — the agent has **network egress**;
- read-only mounts of allowlisted vendor auth dirs that exist on the host
  (`~/.claude`, `~/.codex`, `~/.grok` → `/home/agent/...`), nothing else.

**Know the tradeoff before opting in.** With egress plus a valid vendor
credential, a malicious or prompt-injected agent can use the vendor API itself
as an exfiltration channel (anything readable in `/workspace` can be sent out),
or talk to arbitrary hosts. What docker-cli still prevents:

- host filesystem access beyond the workdir and the read-only auth dirs;
- reading any host credential other than the allowlisted CLI auth
  (popcorn/leaderboard auth is **not** mounted — gated submission holds);
- modifying the mounted auth (`:ro`);
- anything persisting after the run (`--rm`).

This is a middle tier: stronger than `sandbox: none` (host shell, full user
permissions), weaker than the `docker` jail. See SECURITY.md, "Sandbox tiers".
