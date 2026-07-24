# Chi (χ) v1 Design — Phases 0–1

**Date:** 2026-07-25
**Status:** Approved (KD, 2026-07-25)
**Scope:** Phases 0–1 of the Autoresearch Harness product spec (`docs/product-spec-v1.md`), which Chi implements. This document concretizes those phases and records the evidence-driven deviations from the spec. Later phases (orchestrated parallel coders, islands/bandits, two-tier eval rationing, TUI) are out of scope here and get their own design docs.

---

## 1. Context and evidence base

Chi is an open-source CLI harness for autonomous multi-agent research on problems with a programmatic evaluator (build → correctness-check → score). The full product vision, research grounding, and phased plan live in `docs/product-spec-v1.md`.

This design is additionally grounded in a forensic review of KD's manual fleet experiment (July 2026, batched-Cholesky/B200 on GPU MODE; artifacts in `/Users/kd/Projects/autoresearch/`), where six agents from five vendors (claude-fleet, codex, pi, deepseek-v4, glm-5.2, nemotron) coordinated through a hand-rolled file blackboard on a shared GPU sandbox.

**Validated by the experiment (adopt):**

- File-based blackboard coordination across heterogeneous vendors; no agent-to-agent chat was ever needed. Broadcast "breakthrough" events enabled a compose-on-win posture.
- A negative-results ledger with measured evidence and scope (`dead-ends.md`) genuinely stopped re-exploration — for agents that obeyed it.
- `STEERING.md` hot-reload steering with numbered sections and explicit supersede semantics; agents cited it verbatim in their work.
- `best_pointer.json` (champion + hash): an agent recovered from a cleared context by reseeding from the store alone.
- Codified measurement discipline (min-of-k, noise floors, proxy-vs-authoritative skew).
- A structured stop protocol (scorecard → ceiling declaration with evidence → principled stop → pivot posture).

**Failure modes observed (fix structurally):**

1. **Undetected infinite loop (~30h):** one agent cycled three identical "optimizations", posted seven near-identical "final submission" messages, and never produced a single GPU benchmark datapoint. No monitor existed.
2. **Voluntary protocol compliance:** strong models followed the bus conventions; weak ones didn't (1 claim recorded where 560+ had existed; 2 of 6 agents registered).
3. **No claim leases:** stale claims required manual audit and manual release.
4. **Non-durable bus:** an epoch reset destroyed 1,100+ messages of coordination history (shared dir untracked by git).
5. **Confounded rule-outs:** two negative results were later reversed by a human epistemic reset (tested in the wrong regime/structure); unscoped negatives nearly killed the winning direction.
6. **Eval-tier confusion:** a sub-noise local "win" burned an authoritative submission and regressed the public score.

**Design responses (the five deviations from the spec's phasing/detail):**

- The store is **enforced**, not conventional: agents can only write through `chi` subcommands; adapters do the bookkeeping, not the model's goodwill (fixes 2).
- The deterministic **watchdog ships in Phase 1**, not Phase 3 — including a *no-new-eval-datapoint* signal (fixes 1).
- The store is **append-only SQLite (WAL) with JSONL mirrors**, versioned per run (fixes 3, 4).
- The negative ledger carries **scope, evidence, confidence, and a challenge mechanism** from day one (fixes 5). Tier calibration (6) is Phase 4 but the schema records `eval_tier` on every experiment now.
- **Steering ships in Phase 1, not Phase 3 — and it is two-layer.** KD names steering the #1 pain point in autoresearch-type problems, and the experiment agrees: the `STEERING.md` epistemic reset was the highest-leverage single act of the entire run. But in that run *the human was the steering engine*; Chi's end users must not need to be. Chi therefore **generates the steering layer itself** (auto-steering), and the human file is an optional override, never a requirement. v1 includes the hot-reloaded steering file, durable steering history, and observable per-agent uptake (§6); LLM-driven auto-steering (plateau detection → generated epistemic resets) lands with the fleet in Phase 3.

## 2. Goals and non-goals (v1 = Phases 0–1)

**Goals:**

- Phase 0: repo skeleton, config loading (`fleet.yaml`, `problem.yaml`), LiteLLM provider layer with cost tracking and hard budget caps, structured JSONL logging, `chi ping`.
- Phase 1: a single coder agent iteratively improving a score on the `optimize_function` toy problem through the pluggable eval harness, with experiment registry (code-hash dedup), correctness gating, champion tracking, live steering via `steering.md`, and the watchdog — via **both** agent adapters (`cli_subprocess` and `litellm_loop`).

**Non-goals for v1** (explicitly deferred): parallel coders and orchestrated scheduling (Phase 2); islands, bandits, escalation ladder, advanced steering semantics (priority boosts, island weights, model-tier pinning) (Phase 3); tier-2 authoritative eval, rationing, human approval, Modal/SSH backends, embeddings, TUI (Phase 4); context compaction (never — see §5).

The store schema, however, is designed for all phases now so Phase 2+ needs no migration.

## 3. Architecture

```
chi/                        # Python 3.11+, package name: chi
  cli.py                    # typer entrypoints: operator verbs + agent verbs
  config.py                 # fleet.yaml / problem.yaml models (pydantic), validation
  store/
    db.py                   # SQLite (WAL) open/init, short-transaction helpers
    schema.sql
    ledger.py               # experiments, negative ledger, challenges
    events.py               # append-only typed events + JSONL mirrors
    tasks.py                # task state machine, atomic claim/lease
  agents/
    protocol.py             # Agent adapter contract (see §5)
    cli_subprocess.py       # wraps headless vendor CLIs (claude -p, codex exec, …)
    litellm_loop.py         # minimal tools-in-a-loop over LiteLLM
    context.py              # seed-context builder (reconstruct-from-store)
  orchestrator/
    loop.py                 # v1: single-agent run loop; Phase 2 grows the scheduler
    watchdog.py             # deterministic, no-LLM monitor (§6)
    steering.py             # auto-steering digest + operator-file hot-reload (§6)
  eval/
    problem.py              # ProblemDefinition loader (problem.yaml + entrypoints)
    runner.py               # tier-1 local eval: build → correctness → benchmark
    hashing.py              # normalized code_hash / config_hash
  providers/
    llm.py                  # LiteLLM wrapper: completion, cost, budget enforcement
    budgets.py              # per-role/per-run caps, spend accounting
  problems/
    optimize_function/      # toy problem: problem.yaml, reference.py, check.py, bench.py
  runs/                     # per-run artifacts (gitignored): db, jsonl mirrors, diffs, stdout
  tests/
```

Data flow (v1): `chi run fleet.yaml` → config validated → run row + store created under `runs/<run_id>/` → orchestrator seeds one task from the problem definition → launches one agent via its adapter → agent iterates (edit candidate → `chi eval` → result recorded) → watchdog observes events/heartbeats in parallel → run ends on budget, iteration cap, or task completion → champion + trace reported.

## 4. Store design

SQLite in WAL mode at `runs/<run_id>/chi.db` is the source of truth. Every insert to `events`, `experiments`, and `negative_ledger` also appends one JSON line to a matching `runs/<run_id>/mirror/*.jsonl` — greppable, rsync-able, vendor-neutral. Mirrors are write-only conveniences; reads always come from SQLite. All writes go through `chi` subcommands or in-process store APIs; agents get no direct DB or mirror-file access.

Tables (full schema in `store/schema.sql`):

- `runs(run_id, problem, fleet_config_json, started_at, ended_at, status)`
- `tasks(task_id, run_id, strategy_hash, island, status, owner_id, lease_expires_at, attempts, ladder_rung, parent_task_id, priority, spec_json, created_at, updated_at)` — status machine and claim/lease semantics exactly per the product spec. Claims are a single atomic `UPDATE … WHERE status='pending'`.
- `events(event_id, run_id, ts, agent_id, type, task_id, payload_json, cost_usd, tokens_in, tokens_out)` — append-only. Typed: `ITERATION_START|ITERATION_COMPLETE|RESULT|DEAD_END|STATUS|HEARTBEAT|CLAIM|RELEASE|BREAKTHROUGH|STOP|WATCHDOG_KILL|BUDGET_BLOCK|STEER_UPDATE|STEER_ACK`.
- `experiments(code_hash PK, config_hash, run_id, task_id, strategy, island, author, eval_tier, correct, seeds_passed_json, score_metric, score_value, noise_std, parent_code_hash, artifacts_path, ts)` — the dedup/result cache. `eval_tier` is `proxy` for all of v1.
- `negative_ledger(neg_id, run_id, approach_class, summary, evidence_json, ruled_out_scope, confidence, experiment_context_json, authored_by, status, ts)` — `status ∈ active|challenged|reversed`. `experiment_context_json` records regime/structure the rule-out was measured in, so future challenges can argue confounding.
- `challenges(challenge_id, neg_id, agent_id, distinguishing_hypothesis, outcome, ts)` — the "why this case differs" log required to work past an active negative entry.
- `agents(agent_id, run_id, adapter, model, workdir, started_at, last_heartbeat_at, status)`
- `budgets(scope, run_id, cap_usd, spent_usd, updated_at)` — scope is `run` or `role:<name>`.

Concurrency: WAL + `busy_timeout` + short transactions. v1 has one writer per table class in practice (one agent + watchdog); the atomic-claim stress test lands in Phase 2.

## 5. Agent protocol and adapters

An agent is a **process** launched by the orchestrator with: `agent_id`, a claimed task (lease held), a workdir containing the candidate code, and a **seed context** built by `agents/context.py` purely from the store: problem statement, current champion (code + score), relevant findings, the negative-ledger slice for its strategy, and steering rules. Design invariant, proven in the field: *any agent must be reconstructable from the store alone.*

The adapter contract (`agents/protocol.py`):

- `start(seed_context, task) -> handle` — launch the underlying model/CLI.
- The adapter — not the model — writes `ITERATION_START`/`ITERATION_COMPLETE` events, heartbeats every `heartbeat_seconds`, and records every eval attempt as an `ExperimentResult`. Bookkeeping is structural.
- The model interacts with the world through a fixed toolset: edit files in workdir, run `chi eval` (build + correctness + benchmark, result auto-recorded with `code_hash` dedup — an identical candidate returns the cached result and is flagged), `chi query` (experiments + negative ledger), `chi deadend` (must include evidence fields; free-text-only dead-ends are rejected), `chi challenge` (distinguishing hypothesis against an active negative entry).
- `stop(reason)` — graceful terminate; adapter emits `STOP` with a structured reason.

Two adapters ship in v1:

1. **`cli_subprocess`** — runs a headless vendor CLI (`claude -p`, `codex exec`, configurable command template) per iteration or per session. The prompt template injects the protocol, seed context, and the required output contract. The CLI brings its own tool-use and sandboxing; Chi supplies the verbs and the bookkeeping wrapper.
2. **`litellm_loop`** — a deliberately minimal tools-in-a-loop over LiteLLM for non-CLI providers: tools are `read_file`, `write_file`, `run_eval`, `query_knowledge`, `report_deadend`. No subagents, no streaming complexity.

**Context strategy: fresh-context respawn only.** There is no compaction machinery. When an agent's session ends, hits its iteration budget, or is killed by the watchdog, the orchestrator respawns it with a freshly built seed context from the store. The experiment showed this recovery path works and context-rot research says it's the safer default.

## 6. Orchestrator, watchdog, and steering (v1)

`orchestrator/loop.py` in v1 is a plain deterministic loop (no LLM calls): create run → seed task → launch agent → supervise → finish. The Phase 2 scheduler grows here without changing agent-facing contracts.

**Steering.** The stated #1 pain point in autoresearch-type problems, and the experiment's highest-leverage artifact. Design principle: **the end user never needs to steer** — in the manual experiment the human wrote every directive; in Chi the harness produces the steering layer and a human *may* override it. Steering is two-layer:

1. **Auto-steering (default, always on).** Chi maintains the directive agents actually run under. In v1 this is deterministic: a generated digest of run state (current champion + score, active hypothesis, dead-ends slice, next focus) refreshed at every safe point — no human input required for a complete unattended run. In Phase 3 an LLM meta-review pass extends this to plateau-triggered epistemic resets and workstream rebalancing, i.e. the job KD performed by hand.
2. **Operator overrides (optional).** `runs/<run_id>/steering.md` is the human channel. When present, its directives take precedence over auto-steering. The orchestrator re-reads it at safe points only (iteration boundaries — never mid-eval), matching the field-proven cadence.
- On every change the orchestrator emits a `STEER_UPDATE` event carrying the full new content and its hash, so steering history is durable and auditable in the events table even though the file itself is mutable (the experiment lost its steering/bus history to an epoch reset; Chi cannot).
- The current steering content is injected into every seed context and every iteration prompt. Every event records the steering hash in force (`payload_json.steering_hash`), so any result is attributable to the directive it ran under.
- Adapters emit `STEER_ACK` the first time an agent runs under a new steering hash, making uptake observable per agent — a looping or non-compliant agent that ignores new steering becomes visible instead of silent.
- `chi steer "<directive>"` appends a timestamped directive; direct file edits are equally valid. Chi generates the file from a template documenting the conventions that worked in the field: numbered directives, explicit `SUPERSEDES §N` markers for overrides, and a DEAD/do-not-retask list.
- Steering can also write soft negative-ledger entries ("forbid approach X") — recorded with `authored_by=operator`.

`orchestrator/watchdog.py` runs as an async task in the same process, evaluating cheap deterministic signals every tick against `policies` from `fleet.yaml`:

- **Heartbeat staleness** — no heartbeat for `heartbeat_seconds × 3` → probe → kill.
- **Lease expiry** — task returned to `pending`, `attempts` incremented.
- **Repeated-action detection** — hash of (tool, args) and normalized candidate diffs; `repeat_k` identical hashes → kill + respawn with a mutated prompt note.
- **Eval-recency (the nemotron rule)** — more than `eval_recency_iters` (default 10) iterations without a *new* `experiments` row → kill + respawn. An agent that is "working" but producing no evaluated candidates is looping by definition.

Every kill writes a `WATCHDOG_KILL` event with the triggering signal and evidence, so runs are auditable.

## 7. Evaluation harness (tier 1 only)

`ProblemDefinition` loads a problem directory per the product spec's `problem.yaml` interface (`build`/`correctness`/`benchmark` entrypoints, score metric + direction, tolerance, held-out seeds). v1 implements only the local tier-1 backend: sandboxed subprocess with timeout, no network, candidate never sees reference outputs. Correctness on all listed seeds is a hard gate — an incorrect candidate records `correct=false` and cannot become champion. Benchmarks run median-of-k and store `noise_std`.

**Toy problem `optimize_function`:** minimize the median runtime of a pure-Python/NumPy function whose outputs must match `reference.py` within tolerance on held-out seeds. Ships in-repo as the Phase 0/1 test target and the template for user-defined problems.

## 8. Providers and budgets

`providers/llm.py` wraps LiteLLM: model-string routing (any vendor), per-call cost and token capture from LiteLLM's usage data, retry/backoff, and fallback chains from config. Before every call, `budgets.py` checks the run and role caps; an over-cap call raises, emits `BUDGET_BLOCK`, and (v1) ends the run gracefully. `chi ping` calls each configured model with a trivial prompt and prints latency, tokens, and cost per provider — Phase 0's acceptance gate. Keys come from `.env` (never config files).

## 9. CLI surface (v1)

Operator verbs: `chi run <fleet.yaml>`, `chi ping [--fleet fleet.yaml]`, `chi status [run_id]`, `chi steer "<directive>"`, `chi ledger [--negative]`, `chi champion [--export path]`, `chi validate <fleet.yaml|problem.yaml>`.

Agent verbs (used by adapters/models, always scoped by `--run` and `--agent`): `chi task claim|release`, `chi eval <candidate>`, `chi query <text|hash>`, `chi deadend`, `chi challenge`, `chi heartbeat`, `chi msg`.

## 10. Error handling

- Provider errors: LiteLLM retry/backoff then fallback chain; exhausted → `STATUS` event + agent stop, task lease returned.
- Eval subprocess: hard timeout per entrypoint; non-zero build/check exit recorded as a failed attempt with captured stderr (artifact side-channel), never crashes the run.
- Adapter crash: lease expiry returns the task; watchdog logs the kill.
- Store contention: WAL, `busy_timeout=5000`, transactions kept to single statements or tight blocks.
- Budget breach: hard stop with a clean final report; never a silent overrun.

## 11. Testing strategy

`pytest` throughout; CI runs with mocked providers (a `FakeLLM` LiteLLM stub) and a `ScriptedAgent` adapter that emits a predetermined sequence of candidates — this makes watchdog and dedup behavior deterministic to test. Key behavior tests: config round-trip validation; budget cap blocks the (N+1)th call; identical-candidate dedup returns cached result; correctness gate rejects a wrong candidate; champion selection over a scripted improving sequence; watchdog kills a scripted looping agent (repeat-hash) and a scripted no-eval agent (eval-recency) and the task re-leases; lease expiry on a killed process; a mid-run `steering.md` edit reaches the next iteration prompt and emits `STEER_UPDATE`/`STEER_ACK`. Both adapters run one real end-to-end `optimize_function` improvement in a non-CI integration test.

## 12. Acceptance criteria

**Phase 0:** `chi ping` reaches all configured providers through LiteLLM, printing per-call cost and tokens; a `$` cap blocks calls once exceeded; `fleet.yaml`/`problem.yaml` validate and round-trip; CI green with mocked providers.

**Phase 1:** on `optimize_function`, a single agent measurably improves the score end-to-end via **each** adapter (`cli_subprocess` with at least one vendor CLI; `litellm_loop` with at least one API provider); every attempt is recorded with a `code_hash`; re-proposing an identical candidate is served from cache; incorrect candidates are gated; the run ends with a champion and a reconstructable trace; the watchdog demonstrably kills an intentionally looping agent and an agent producing no eval datapoints, and the task returns to `pending`; editing `steering.md` mid-run changes the agent's next iteration prompt and is logged as `STEER_UPDATE` with a subsequent `STEER_ACK`.

## 13. Deferred decisions (revisit at their phase)

- Phase 2: atomic-claim stress testing, worktree-per-agent management, query-before-work enforcement across agents.
- Phase 3: LLM auto-steering / meta-review (plateau detection → generated epistemic resets, dead-list refresh, workstream rebalancing — the operator file stays an optional override, never a requirement); advanced operator steering (task-priority boosts, island weights, model-tier pinning per role); islands/bandit; escalation ladder; negative-ledger *blocking* (v1 ledger records and serves; blocking-with-challenge becomes mandatory when multiple agents run).
- Phase 4: proxy→authoritative calibration, rationing token bucket, approval checkpoints, Modal/SSH/leaderboard backends, embeddings retrieval, TUI, Postgres option.
- License: Apache-2.0 (already committed in-repo; supersedes the spec's MIT suggestion).
