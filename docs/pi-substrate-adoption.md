# Adopting pi's substrate under chi's orchestration

**Date:** 2026-07-26
**Status:** Design proposal (not yet approved)
**Author:** synthesized from a deep-read of `earendil-works/pi` (the Pi agent harness) and a full session of dogfooding chi against a live GPU MODE competition.

## Why this doc exists

Chi and pi solve **different halves** of the same problem and it's worth being precise:

- **pi** is a polished single-agent *coding substrate* — a self-extensible coding-agent CLI. It has **no** multi-agent orchestration, blackboard, negative-results ledger, or evaluation-driven search loop (verified by reading the source; its only multi-agent piece is a *subagent example* that fans out and threads text via a `{previous}` placeholder).
- **chi** is a multi-agent *autoresearch orchestrator* — parallel fleet, blackboard, negative ledger, watchdog, two-tier eval, gated/rationed submission, auto-submit. That layer is chi's real IP and pi deliberately omits it.

The uncomfortable finding from a full day of running chi against a real competition: **almost every failure was in the substrate layer that pi has already solved well**, while chi's orchestration layer worked. This doc catalogs the specific pi mechanisms worth adopting, tied to the concrete chi failures they would have prevented, with a prioritized plan.

## The failures this proposes to fix (evidence)

Real breakages observed while running chi's fleet on GPU MODE leaderboard 776 (batched Cholesky / B200), driving claude + grok + codex CLIs:

1. **Agents bypassed chi's submission gate.** The CLI coder agents have a shell; they ran `popcorn-cli` directly, firing their own submissions outside chi's mutex/ration/approval. chi's rails only governed its *own* submit path. (Patched with a fragile PATH shim that itself had a bug — it over-blocked benchmarks.)
2. **CLI command templates were guessed and wrong.** codex needed `--skip-git-repo-check --sandbox workspace-write`; grok needed `--prompt-file` not `--no-interactive`. Both failed silently (exit 1/2) until discovered by hand.
3. **codex couldn't run at all** (ChatGPT-account model rejection) — no clean capability model to detect/handle this; it just errored every iteration until the watchdog killed it.
4. **The eval produced fabricated scores.** The scaffolded `bench.py` invented numbers (834.28, 664.3) when the real popcorn benchmark returned null — chi drove the CLI by prompt-file + stdout-grep, so it couldn't tell a real measurement from a parsed artifact.
5. **Shift+Enter multiline input** was impossible (worked around with Ctrl+J and trailing-backslash) because terminals can't report the modifier.

Every one of 1–4 traces to the same root cause: **chi drives sub-agents crudely — write a prompt file, `subprocess.run`, grep stdout — instead of through a typed protocol it controls.**

## What to adopt from pi (ranked by leverage against the above)

### 1. Drive agents through a typed JSON protocol, not prompt-file + stdout-grep — **HIGHEST LEVERAGE**

**pi mechanism:** `pi --mode json -p` turns the agent into an NDJSON-streaming subprocess with a typed event vocabulary (`message_end`, `tool_result_end`, usage, etc.). pi's subagents, its `server`/`ipc` control plane, and its evals all compose over this one primitive (`packages/coding-agent/src/modes/rpc/`, the subagent example, `packages/server/src/ipc/`).

**Adopt in chi:** replace `chi/agents/cli_subprocess.py`'s "prompt file → run → grep" with an adapter that drives each vendor CLI in its **headless-structured mode** and parses a stream of typed events:
- claude: `claude -p --output-format stream-json` (already emits structured JSON events).
- codex: `codex exec --json` / experimental JSON event stream.
- grok: `grok --prompt-file … --output-format json` (grok's help documents `--output-format plain|json|streaming-json`).

**What this fixes:** (a) chi *sees* each tool call the agent makes, so it can **mediate submissions** (fixes failure #1 without a PATH-shim hack); (b) chi distinguishes a real eval result from a parsed artifact (failure #4); (c) structured errors replace silent exit codes (#2, #3). This single change dissolves the most bugs.

### 2. Dependency-injected tool "operations" seam

**pi mechanism:** every built-in tool (`read/write/edit/bash/grep/find/ls`, in `packages/coding-agent/src/core/tools/`) separates its logic from an injectable `*Operations` interface. `defaultReadOperations` uses local FS; a sandbox extension (`gondolin`) overrides `operations` to reroute all I/O into a VM without touching tool code.

**Adopt in chi:** model chi's *submission* and *eval-execution* as injectable operations. Agents get a `submit` operation that always routes through chi's gate; there is no direct popcorn access to bypass. This is the *clean* version of the PATH shim — mediation by construction, not by intercepting a binary on `$PATH`.

### 3. A real provider/substrate capability layer

**pi mechanism:** `packages/ai/` — ~38 providers behind lazy registration (`register-builtins.ts`), a flat message/content model with normalized streaming (`AssistantMessageEvent` union), tool-call normalization per provider family, generated+content-hashed model/pricing data, 7 OAuth flows with double-checked-lock refresh, and a **capability-flags-as-data** `compat/` layer (declarative per-model quirks instead of `if provider == …`).

**Adopt in chi:** a `SubstrateInfo` with capability flags per CLI/model — *can it run headless? what's its JSON flag? does this account/model actually work? what's its model-select syntax?* — discovered once and cached, replacing the hand-written `CLI_SUBSTRATES` templates that broke. This is where failures #2 and #3 get designed out.

### 4. Native modifier-key module for the TUI

**pi mechanism:** `packages/tui/native/darwin/src/darwin-modifiers.c` (+ win32) exposes `isModifierPressed(name)` — because terminals cannot report standalone/held modifier state (e.g. Shift+Enter vs Enter). Prebuilt `.node` binaries ship so no compile step is needed.

**Adopt in chi:** the real fix for the Shift+Enter multiline problem currently worked around with Ctrl+J/backslash. A small native helper (or the same prebuilt-binary approach) queries OS modifier state directly.

### 5. Supply-chain hardening (chi has none; it's open-source)

**pi mechanism (steal the whole checklist):** exact-pinned direct deps enforced in CI (`scripts/check-pinned-deps.mjs`), `.npmrc` `min-release-age=2` (refuses deps younger than 2 days), a published-CLI shrinkwrap with an **install-script allowlist** that fails CI on any new lifecycle-script dep, `npm audit signatures`, SHA-pinned GitHub Actions, OIDC trusted publishing.

**Adopt in chi:** the Python equivalents — hash-pinned `requirements`/lockfile, a dependency-review gate, pinned CI actions, and a documented sandboxing story (chi runs untrusted agent-generated code and *needs* one; pi's honest "no sandbox, containerize it" + DI-operations + containerization docs are the right model).

### 6. Minimal-core + code-extension model

**pi mechanism:** the core is deliberately small; tools, slash commands, providers, and UI are added by a first-class code-extension system (`(pi: ExtensionAPI) => void` modules, ~1,700-line typed surface). Sandboxing, subagents, and plan-mode are all *extensions*, not core.

**Adopt in chi (longer-term):** make chi's **problem definitions, execution backends, and agent substrates pluggable code** rather than the hardcoded `if adapter == …` / `if backend == …` switches they are today. Each new backend (popcorn, Modal, SSH) or substrate (a new CLI) becomes a registered extension, not a core edit.

### 7. Entry-log session model with reversible compaction (evaluate, lower priority)

**pi mechanism:** a session is an append-only log of typed *entries* (messages, tool results, compaction markers), serialized to SQLite (`packages/storage/sqlite-node/`), with summarization-compaction emitted as an *event in the log* (inspectable/reversible), gated by the model's real context window.

**Relation to chi:** chi already has an event-log store and uses fresh-context-respawn (a deliberate, cheaper choice). Worth adopting only the *compaction-as-inspectable-event* idea if long single-agent sessions become common; the respawn-from-store model is fine for the fleet.

## What NOT to change — chi's actual value

Do not dilute these; pi has none of them and they are the reason chi exists:
- Parallel multi-agent fleet with per-agent worktrees.
- SQLite **blackboard** with cross-agent dedup and shared knowledge.
- First-class **negative-results ledger** (the anti-plateau mechanism).
- Deterministic **watchdog** (eval-recency / repeat-hash — it correctly killed the dead codex agent today).
- Two-tier eval with **gated + rationed + auto** submission and the rank-protection rails.
- The **eval → improve → submit** autoresearch loop itself.

## Proposed sequencing

1. **JSON-protocol agent adapter** (item 1) — highest leverage; dissolves failures #1–#4. Prototype against the claude CLI's `stream-json` first, then grok/codex.
2. **Mediated submission via a DI operation** (item 2) — retires the PATH-shim hack; makes the gate un-bypassable by construction.
3. **Substrate capability layer** (item 3) — kills the broken-template class of bugs; folds in codex/grok/claude detection.
4. **Native-modifier TUI fix** (item 4) and **supply-chain hardening** (item 5) — independent, ship anytime.
5. **Extension model** (item 6) — larger refactor; do once the above stabilize.

Each is independently shippable and testable behind chi's existing `Agent`/backend protocols; none require touching the orchestration layer.

## pi-chat findings (their Slack/chat + workflow layer)

Analyzed `earendil-works/pi-chat` for orchestration patterns. **Headline: it confirms chi's core value is genuinely differentiated.** pi-chat is *not* a multi-agent coordinator — it's a **single-agent-per-conversation chat bridge**: one pi agent per channel, strictly serial per-conversation job queue, and multi-channel "scale" is just `tmux` launching N independent pi processes that share nothing but the filesystem. There is **no blackboard, no message bus, no task delegation, no result merging** — its only cross-agent surface is a read-only 15s status heartbeat (observability, not coordination). So neither pi nor pi-chat has chi's fleet/blackboard/negative-ledger/eval-loop; that remains uniquely chi's.

But pi-chat has **two mechanisms that are the clean answer to problems chi hit today**, plus reusable durability patterns:

### 8. Real sandboxing: one micro-VM per agent (the answer to the agent-bypass problem)

**pi-chat mechanism:** every conversation runs in a **Gondolin micro-VM** (QEMU/Alpine); the agent's `read/write/edit/bash` tools execute *inside* the VM (`src/gondolin.ts`), host `/workspace` and `/shared` mounted in. The agent has a shell — but it's a *sandboxed* shell with no access to host credentials or host processes.

**Why chi needs this:** today chi's CLI agents had a *host* shell and fired `popcorn-cli` directly, bypassing chi's submission gate and touching real credentials/quota. My PATH-shim patch was a workaround for the absence of a sandbox. The correct fix is pi-chat's model: **run agents in a sandbox where they physically cannot reach the submission credential or the host popcorn binary** — chi mediates all privileged actions (submission) at the boundary. This subsumes the shim.

### 9. HTTP-layer secret injection (agents act with credentials without seeing them)

**pi-chat mechanism:** `createHttpHooks` (via Gondolin) swaps secrets into outbound requests to allowed hosts *at the HTTP layer* — the agent triggers an authenticated call but never sees the raw secret (`src/gondolin.ts:29-44`). Plus an RSA-OAEP + AES-256-GCM out-of-band secret-exchange (`src/secrets.ts`).

**Adopt in chi:** the general pattern for "let an agent *use* a capability (submit, call an API) without holding the credential." A submission proxy that injects the popcorn auth only for chi-approved, gated submits is the credential-safe version of mediated submission (item 2).

### 10. Event-sourced durable log with replay boundaries (durability/resume)

**pi-chat mechanism:** an append-only JSONL log per conversation (`src/log.ts`), with a **consumption boundary** = the last `job_completed` record. Failed jobs **do not advance the boundary**, so a crashed turn's work auto-replays — clean **at-least-once** semantics (`src/runtime.ts:248-257`). Plus an **arming watermark** (`armAfterCurrentTail`) that distinguishes catch-up/history from genuinely-new work on reconnect, and a **PID-aware file lock** with dead-owner recovery.

**Adopt in chi:** chi's store is event-based but its task lifecycle doesn't have this crisp replay semantic. For durable, resumable multi-hour fleet runs (exactly what we just tried), the "boundary = last completed; failures re-run; watermark to avoid re-triggering on resume" pattern is worth lifting. chi's SQLite claim/lease is already stronger than the file lock, so keep that.

### Also worth noting (smaller)
- **Adapter interface** (`LiveConnection` in `src/live/`) — a clean narrow template for pluggable source/sink connectors (chat, search APIs, doc stores). Discord uses a gateway socket, Telegram long-polls — both webhook-free.
- **Prompt = delta slice, not full history** — feed the agent only records since the last completed turn (token-efficient; maps to "new evidence since last synthesis").
- **Corrective to item 1:** pi-chat does *not* demonstrate the `--mode json` subprocess protocol — it integrates as an in-process *extension* and fans out via tmux. The json-mode recommendation still stands (from pi-core's rpc mode + subagent example), but note the in-process extension model is pi's other integration path.

## Appendix: source pointers (in the cloned `earendil-works/pi`)

- json/rpc mode + subprocess protocol: `packages/coding-agent/src/modes/rpc/`, `packages/server/src/ipc/`, subagent example at `packages/coding-agent/examples/extensions/subagent/`.
- DI-operations tools: `packages/coding-agent/src/core/tools/read.ts` (and siblings).
- provider layer: `packages/ai/src/` (`models.ts`, `types.ts`, `providers/register-builtins.ts`, `compat/`, `auth/`).
- differential TUI + native modifiers: `packages/tui/src/tui.ts`, `packages/tui/native/darwin/src/darwin-modifiers.c`.
- supply-chain: `scripts/check-pinned-deps.mjs`, `scripts/generate-coding-agent-shrinkwrap.mjs`, `.npmrc`, `.github/workflows/`.
- extension API: `packages/coding-agent/src/core/extensions/types.ts`.
