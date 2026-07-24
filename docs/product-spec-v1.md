# Autoresearch Harness — Product & Technical Specification (v1.0)

> **Archival note (2026-07-25):** This is the original product spec, preserved verbatim as the north-star reference. The project implementing it is **Chi (χ)**, pronounced "kai". Where this spec and the phase design docs in `docs/superpowers/specs/` disagree, the design docs win — they record approved, evidence-driven deviations (see `2026-07-25-chi-v1-design.md` §1). Known deltas: the project name is Chi; the license is Apache-2.0 (not MIT); steering is a Phase 1 feature and is two-layer (auto-steering by the harness, optional human override); the deterministic watchdog ships in Phase 1.

## Overview & Problem Statement

The Autoresearch Harness is an open-source, multi-agent autonomous research-and-coding system for attacking hard engineering problems that have a *programmatic evaluator*. Its exemplar problem is writing and optimizing a batched Cholesky factorization CUDA kernel for NVIDIA B200 on the GPU MODE leaderboard (compile → correctness-check → benchmark → profile → iterate, scored by geomean latency across 15 shapes). It generalizes to any problem exposing a build command, a correctness check, and a score.

### Target user persona
KD — senior backend/infrastructure engineer, Python-heavy, ~6 years production experience, plans to open-source the tool. He has been manually running a multi-model LLM fleet (Claude Opus 4.8 and GPT-5.6/codex as coders, another instance as orchestrator, plus DeepSeek, GLM, MiniMax) on the B200 Cholesky problem.

### Pain points the harness MUST solve (from KD's manual experience)
1. **Plateau / redundant re-exploration** — the fleet kept re-trying approaches already ruled out (precision tricks, streams, micro-tuning) because there was no shared negative-results ledger.
2. **Scarce authoritative evaluation** — leaderboard submission budget of ~1 submission per 30 minutes; the harness must distinguish cheap local proxy eval from expensive authoritative eval and ration the latter.
3. **Stalls / hangs / premature give-ups** — agents get stuck or surrender too early.
4. **Ad hoc steering** — no clean way to steer mid-run.

### Goals
- Coordinate multiple coding agents + sub-agents under an orchestrator with real inter-agent communication.
- Maintain a shared knowledge store including an explicit NEGATIVE-results ledger keyed by config/code hash.
- Two-tier evaluation with rationing of the authoritative tier.
- Robust stall/loop/give-up detection and an escalation ladder.
- Human steering mid-run + approval checkpoints for expensive evals.
- Multi-provider LLM fleet with per-role model tiering and budget tracking.
- Pluggable "problem definition" so the harness is domain-agnostic.

### Non-goals
- Not a general RL training framework or a hosted SaaS.
- Does not itself provide GPUs; it integrates remote/rationed GPU execution via hooks.
- Not a replacement for Claude Code — it *orchestrates* coding agents (which may be Claude Code / Claude Agent SDK instances) rather than reimplementing them.
- No attempt to defeat leaderboard anti-cheat; correctness is treated as a hard gate (see reward-hacking guardrails).

---

## Research Grounding (state of the art, 2024–2026)

Design choices below are grounded in the following findings:

- **Orchestrator-worker multi-agent pattern.** Anthropic's "How we built our multi-agent research system" (Hadfield, Zhang, Lien, Scholz, Fox & Ford, June 2025) describes a Lead Researcher (orchestrator) that plans and spawns 3–5 parallel subagents with isolated context windows, plus a separate citation/synthesis pass. Key quantified lessons: *"We found that token usage by itself explains 80% of the variance, with the number of tool calls and the model choice as the two other explanatory factors"* (the three factors together explain ~95% of performance variance on their internal research eval); *"multi-agent systems use about 15× more tokens than chats"*; and the Opus-4-lead + Sonnet-4-subagent system *"outperformed single-agent Claude Opus 4 by 90.2% on our internal research eval."* Early agents failed by spawning too many subagents and duplicating work; the fix is to cap subagent spawning in the *orchestration layer*, not to beg for restraint in prompts.
- **Evolutionary program search.** DeepMind AlphaEvolve and its predecessor FunSearch maintain a *program database* combining MAP-Elites and island population models to balance exploitation vs exploration, resurface diverse elites, and avoid premature convergence. They use an LLM ensemble (Gemini Flash for breadth, Gemini Pro for depth), evaluation cascades (cheap checks first, expensive later), and store artifacts/error feedback. OpenEvolve is an open-source reference implementation (islands, migration interval, similarity-threshold novelty filtering via embeddings, cascade evaluation, artifact side-channel for error feedback).
- **Tree search over code.** AIDE (Weco AI) frames ML engineering as tree search: each node is a code version, edges are single improvements, metric feedback prunes/guides. AI Scientist-v2 adds an experiment-manager agent over a progressive agentic tree search. Reported finding (MLE-bench): AIDE's *operators*, not the search algorithm, are the bottleneck.
- **Hypothesis tournaments.** Google Co-Scientist (Nature, 2026) uses six named agents (Generation, Reflection, Ranking, Evolution, Proximity, Meta-review) under a Supervisor; an Elo-based "tournament of ideas" with pairwise debates ranks hypotheses; a Proximity agent clusters to preserve diversity; a Meta-review agent compiles feedback to adjust prompts. Giving the Reflection agent search tools reduced hallucinated hypotheses.
- **GPU-kernel-specific work.** KernelBench (Stanford, ICML 2025) defines the `fast_p` metric (fraction of generated kernels that are both correct and >p× the PyTorch baseline). CudaForge (Zhang et al., arXiv:2511.01884), a training-free Coder+Judge workflow using NCU feedback, reports on 250 KernelBench tasks: *"achieves 97.6% correctness of generated kernels and an average 1.68× speedup over PyTorch baselines... while further scaling up maximum iteration rounds increases CudaForge's performance to 2.27×"* — at roughly $0.3 API cost and ~26.5 minutes per kernel on one RTX 6000. KernelAgent (PyTorch blog) and KEET use Nsight Compute (NCU) metrics — DRAM throughput, L2 hit rate, warp occupancy/stall reasons, tensor-core utilization, Speed-of-Light — as structured feedback. EvoEngineer (arXiv:2510.03760) decomposes kernel evolution into traverse techniques + population management and reports *"2.72× median speedup with 69.8% code validity, substantially outperforming existing methods... attains the highest speedup on 59.5% of operations with over 2× acceleration"* (EvoEngineer-Free with Claude-Sonnet-4); it explicitly notes performance-measurement stochasticity (uses median of runs) and that *"models exhibit varying capabilities for different kernel categories"* — a direct argument for a heterogeneous coder fleet.
- **GPU MODE / KernelBot eval flow.** KernelBot (the GPU MODE competition platform) uses a Discord bot + runner architecture with a Postgres DB; runners are GitHub Actions and Modal. Critically for anti-reward-hacking: each submission is evaluated **twice — once publicly** (detailed error messages, stdout/stderr) **and again privately with a different random seed, returning only the achieved time.** User code runs in an isolated subprocess. Problems are defined via `reference.py` (PyTorch reference), `task.yml` (spec/test-case shapes), and `task.py` (I/O schema). The `kernelguard` repo and hardened harness address exploits.
- **Reward-hacking cautionary tale.** Sakana AI's "AI CUDA Engineer" claimed 10–100× speedups; third parties found the evolutionary system exploited a **memory exploit in the evaluation code that let it skip correctness checks**. Lesson: treat the evaluator as adversarially probed; verify correctness on held-out seeds; never let candidate code see reference outputs.
- **Failure-mode engineering.** "Infinite Agentic Loops" (IAL) arise when feedback paths lack an effective stopping bound; loop rate correlates inversely with success (TIDE). Practical remedies converge across frameworks (Claude Code, Codex, LangGraph, smolagents) on: max-step caps, action/state hashing + repetition detection, step-efficiency scoring, heartbeats + watchdogs, and independent deterministic monitors (zero-cost, no LLM) that catch failures an LLM can't self-detect. A patented pattern uses heartbeat timestamps → suspect → probe → failed → respawn "shadow agent" + restore known-good state.
- **Context rot & compaction.** Chroma's "Context Rot" study (18 frontier models) shows all models degrade as input grows, worse in the "middle," worse with logically-structured (plausible-distractor) content — coding agents are especially vulnerable because context accumulates. Anthropic's context-engineering guidance: compaction (summarize older turns), tool-result clearing, and a memory tool for cross-session persistence; put durable rules in CLAUDE.md; the SDK exposes a PreCompact hook and community guidance is to compact proactively at ~60% fill. Practical prescription: make recovery from a clean context window the default design assumption.
- **Claude Agent SDK & worktrees.** The Claude Agent SDK exposes Claude Code's loop as a library (query() generator, subagents-as-tools with isolated context, hooks, compaction, session resume via session_id, `ResultMessage.total_cost_usd` for cost tracking). It provides no built-in durable execution, observability, state persistence, or multi-agent coordination beyond spawning subagents — those are the harness's job. Claude Code supports git worktrees per agent (`isolation: worktree` frontmatter; `.claude/worktrees/<name>/`); teams run 4–8 concurrent worktrees reliably; the Grit rewrite consumed ~45B tokens and showed coordination/merge hygiene must be engineered, not assumed.
- **Multi-provider fleet.** LiteLLM provides an OpenAI-compatible gateway across 100+ providers (Anthropic, OpenAI, DeepSeek, Google, etc.), with per-key/per-model budgets (`max_budget`, `budget_duration`), virtual keys with hard cutoffs, fallback chains, Redis-synced spend across instances, and Prometheus budget metrics.
- **Precedent.** Karpathy's `autoresearch` (March 2026) gives an agent one editable file (`train.py`), a fixed 5-minute wall-clock eval budget per experiment (~12 experiments/hour, ~100 overnight on an H100), a single vocab-independent metric (val_bpb), and a human-edited `program.md` "skill." Per Karpathy (X, March 9, 2026): *"I left autoresearch tuning nanochat for ~2 days on depth=12 model. It found ~20 changes that improved the validation loss... all of them were additive and transferred to larger (depth=24) models... the leaderboard's 'Time to GPT-2' drops from 2.02 hours to 1.80 hours (~11% improvement)."* Karpathy flagged replication variance and overfitting-to-the-metric as risks. The stated thesis: the bottleneck is now *eval design* (clean, fast proxy metrics), not execution.

---

## End-to-End Walkthrough: A Run on Batched-Cholesky/B200 (hour by hour)

Assume `problem.yaml` defines the batched Cholesky problem (15 shapes, geomean-latency score), a local proxy evaluator (single L40S/consumer GPU or a small subset of shapes), and an authoritative evaluator (B200 leaderboard submission, rationed to 1/30min). Fleet: Opus 4.8 + GPT-5.6 as coders, a Claude orchestrator, DeepSeek/GLM/MiniMax as critics/triage.

- **Hour 0 (bootstrap).** Orchestrator ingests `problem.yaml`, reads CLAUDE.md/`program.md`, and the Planner decomposes the problem into an initial strategy set: (S1) blocked right-looking with shared-memory panel, (S2) one-block-per-matrix unrolled microkernels with interleaved data layout, (S3) warp-per-matrix register-resident factorization, (S4) cuSOLVERDx/library-backed baseline. Each strategy becomes a seed node in the experiment tree and a task in the ledger (`pending`). The NEGATIVE ledger is empty. A baseline reference kernel is compiled and run through the local proxy to establish score_0.
- **Hours 1–3 (parallel breadth).** Two coder agents claim S1 and S2 (claim/lease so they never touch the same worktree). Each works in its own git worktree, iterating locally: edit → compile → correctness-check → local benchmark. The profiler-analyst runs NCU on each best candidate and writes structured findings (e.g., "S1 memory-bound, DRAM 78% SoL, low occupancy from shared-mem pressure"). Findings + every tried config-hash go to the experiment registry. DeepSeek acts as a cheap critic reviewing diffs for obvious correctness/perf smells before any expensive step.
- **Hour 3 (first authoritative gate).** S2 beats baseline by 1.4× on the local proxy and is the current best. The orchestrator, seeing the authoritative-eval budget token is available and no better candidate is queued, requests a human approval checkpoint (or auto-approves within policy) and submits S2 to the B200 leaderboard. Result recorded as an authoritative datapoint keyed to the code-hash; proxy-vs-authoritative correlation is updated.
- **Hours 4–6 (exploitation + negative results).** Coders mutate S2 (vectorized loads, `__launch_bounds__`, register tiling). Several precision tricks (TF32/FP16 panel) fail correctness on the held-out proxy seed → written to the **NEGATIVE ledger** with evidence (seed, max abs error, code-hash). When a coder later proposes a precision trick, it first *queries the negative ledger* and is told this class is ruled out (with the specific error), preventing re-exploration — directly fixing pain point #1.
- **Hour 7 (plateau detected).** No new best score in N=12 local iterations; loop detector flags repeated near-identical diffs from Coder-A. The exploration policy (island/bandit) forces a jump: spin up a fresh-context coder on the least-explored island (S3 warp-per-matrix) and escalate one hard sub-problem (diagonal block inversion) to the strongest model tier.
- **Hours 8–10 (tournament + steer).** Candidates from S2' and S3 enter a tournament; the critic ranks them (Elo-style) using proxy score + NCU profile + code-diversity. KD drops a line in `steering.md`: "stop micro-tuning S2; explore cuSOLVERDx path S4 and cp.async pipelining." The orchestrator reads this between iterations, reprioritizes the task queue, and injects the hint into coder prompts.
- **Overnight.** ~100+ local experiments run; authoritative submissions are rationed to ~1/30min and only spent on candidates that (a) pass correctness on multiple held-out seeds and (b) beat the current authoritative best on the proxy by a margin exceeding the measured proxy noise. KD wakes to a run trace, a best-score-over-time chart, the ledger growth, per-agent cost, and the current champion kernel with its NCU profile.

---

## System Architecture

Component diagram (logical):

```
                          ┌─────────────────────────────┐
        steering.md  ───▶  │        ORCHESTRATOR         │ ◀── CLI / TUI (human)
        (human, hot-reload)│  (scheduler + policy loop)  │
                          └───────────┬─────────────────┘
                                      │ reads/writes
             ┌────────────────────────┼──────────────────────────────┐
             ▼                        ▼                               ▼
      ┌────────────┐          ┌───────────────┐               ┌───────────────┐
      │  PLANNER   │          │  TASK LEDGER   │               │ KNOWLEDGE STORE│
      │(decompose, │          │ (SQLite: tasks,│               │  hypotheses    │
      │ strategy   │◀────────▶│ claims/leases, │◀────────────▶ │  experiments   │
      │ islands)   │          │ messages)      │               │  findings      │
      └────────────┘          └───────┬────────┘               │  NEGATIVE ldgr │
                                      │                         │ (embeddings)   │
        ┌──────────────┬─────────────┼───────────────┐         └───────────────┘
        ▼              ▼             ▼               ▼
  ┌──────────┐  ┌──────────┐  ┌────────────┐  ┌────────────┐
  │ CODER A  │  │ CODER B  │  │ RESEARCHER │  │  CRITIC /  │
  │(worktree)│  │(worktree)│  │ (web/docs) │  │ VERIFIER   │
  └────┬─────┘  └────┬─────┘  └────────────┘  └─────┬──────┘
       │             │                              │
       ▼             ▼                              ▼
  ┌───────────────────────────┐            ┌──────────────────┐
  │   EVALUATION HARNESS       │            │ PROFILER-ANALYST │
  │  Tier 1: local proxy       │───NCU────▶ │ (parses NCU/     │
  │  Tier 2: authoritative     │            │  Nsight → text)  │
  │  (rationed, remote GPU)    │            └──────────────────┘
  └───────────────────────────┘
       │
       ▼
  ┌───────────────────────────┐
  │ EXECUTION BACKENDS         │
  │ local sandbox / Modal / SSH│
  └───────────────────────────┘
       │
  ┌───────────────────────────┐
  │ PROVIDER LAYER (LiteLLM)   │ Anthropic·OpenAI·DeepSeek·GLM·MiniMax
  │ budgets · fallbacks · cost │
  └───────────────────────────┘
```

All agents communicate **indirectly through the shared stores** (blackboard pattern) rather than chatting directly, which decouples them, prevents the O(n²) chatter Anthropic warned about, and lets agents be added/removed without rewiring. Direct point-to-point messages exist only as a typed message table the orchestrator brokers.

---

## Agent Role Definitions

Each role lists: responsibilities · inputs → outputs · prompting strategy · model tier · sub-agent rules.

### Orchestrator (Supervisor)
- **Responsibilities:** own the policy loop; assign/rebalance tasks; enforce claim/lease; ration authoritative eval; run stall/loop/give-up detection; read `steering.md` between iterations; enforce budgets; trigger compaction/respawn.
- **I/O:** ledger state + steering file + budget state → task assignments, escalation decisions, eval-approval requests.
- **Prompting:** extended-thinking scratchpad for planning; explicit coordination rules ("cap subagents at K; never assign a claimed task"); it is mostly *code* (a deterministic scheduler) with an LLM only for high-level replanning.
- **Model tier:** frontier (Claude Opus-class) for replanning; most loop iterations are pure code (no LLM call).
- **Sub-agents:** may spawn Planner and up to `max_coders` coders; subagents may NOT spawn their own subagents (hard cap in the orchestration layer, per Anthropic's lesson).

### Planner
- **Responsibilities:** decompose the problem into strategies/islands; maintain the experiment tree seeds; propose decomposition of hard sub-problems on escalation.
- **I/O:** problem definition + current best + negative ledger → task DAG, island assignments.
- **Prompting:** "propose K *diverse* strategies; for each, state the hypothesis and the cheapest disproving experiment." Query negative ledger first.
- **Model tier:** frontier (depth).

### Coder agents (≥2, heterogeneous)
- **Responsibilities:** implement/mutate candidate code in an isolated worktree; run the local proxy loop; record every attempt (config-hash) and result.
- **I/O:** task + seed code + relevant findings/negative-ledger slice → diff, local score, artifacts.
- **Prompting:** OODA loop; must call `query_knowledge()` before proposing an approach and `check_negative_ledger()` before an expensive step; must emit a structured `ExperimentResult`.
- **Model tier:** frontier coders (Opus 4.8, GPT-5.6) — deliberately heterogeneous. EvoEngineer's cross-model analysis found models excel at different kernel categories, so heterogeneity lets different models explore different regions of the solution space.
- **Sub-agents:** may spawn a short-lived "explore" read-only subagent for codebase/doc lookup (isolated context, returns a summary).

### Researcher agent
- **Responsibilities:** web/doc retrieval (CUDA docs, cuSOLVERDx, papers, NCU metric meaning) → distilled findings.
- **I/O:** open question → findings-log entries with citations.
- **Prompting:** broad-to-narrow search; must return only high-signal distilled notes (avoid context bloat).
- **Model tier:** mid-tier + tool use (cheap model with search).

### Verifier / Evaluator (deterministic + LLM)
- **Responsibilities:** run the pluggable eval; enforce correctness as a hard gate on multiple held-out seeds; parse score; detect reward-hacking (e.g., candidate reading reference outputs, timing anomalies, correctness passing publicly but failing privately).
- **I/O:** candidate code → `EvalResult{correct, score, tier, seed, logs, flags}`.
- **Prompting:** mostly deterministic code; an LLM "reviewer" is a *separate* pass (never self-grading by the coder).
- **Model tier:** cheap/deterministic; LLM reviewer mid-tier.

### Profiler-Analyst
- **Responsibilities:** run NCU/Nsight on best candidates; convert raw metrics into a short natural-language bottleneck report (memory- vs compute- vs latency-bound; occupancy; stall reasons; SoL%) with concrete suggested code changes.
- **I/O:** kernel + profile → structured `ProfileFinding`.
- **Model tier:** mid-tier (this is the KEET/CudaForge "Judge" pattern).

### Critic / Ranker
- **Responsibilities:** review diffs cheaply; run tournament ranking over candidate populations (Elo-style pairwise using proxy score + profile + diversity); flag premature give-ups.
- **Model tier:** cheap models (DeepSeek/GLM/MiniMax) for triage/critique — this is the cost-efficient tier per the fleet plan.

---

## Task Model & State Machine

### Lifecycle
```
pending ──claim──▶ claimed ──start──▶ running ──eval──▶ verified ──merge──▶ merged
   ▲                  │                  │                 │
   │                  │(lease expires)   │(fail)           │(worse/dup)
   └──────────────────┴──────────────────┴─────────────┐  ▼
                                                        └▶ failed / abandoned
```
- **pending → claimed:** an agent atomically claims a task by writing `owner_id` + `lease_expires_at` in a single transaction (SQLite `UPDATE ... WHERE status='pending'`). Only one claimer wins.
- **claimed → running:** worktree created; heartbeat starts.
- **running → verified:** passed correctness on all required seeds; score recorded.
- **verified → merged:** becomes (or ties into) the champion / an island elite.
- **Any → failed/abandoned:** correctness fail (→ negative ledger if it's a genuine dead-end), lease expiry (→ back to pending, respawn), or superseded/duplicate.

### Claim/lease semantics
- Every claim carries a TTL lease (`lease_seconds`, default 900). A watchdog returns tasks whose lease expired (dead/stalled agent) to `pending` and increments `attempts`.
- **Dedup:** before creating a task, the Planner computes a `strategy_hash`; before an expensive eval, the coder computes a `code_hash` (normalized source) and `config_hash`. If either already exists in the experiment registry, the work is skipped and the prior result returned. This is the primary anti-duplication mechanism.

### Decomposition rules
- Decompose only when: (a) a task fails ≥`decompose_after` attempts, or (b) the Planner marks it "compound." Sub-tasks inherit the parent's island and reference the parent id.

---

## Inter-Agent Communication Protocol

Communication is **blackboard-style through shared SQLite tables**, with a typed message table for directed notifications. All records are JSON with a versioned schema.

### Channels / tables
- `tasks` — the task ledger (state machine above).
- `messages` — directed/broadcast notifications brokered by the orchestrator.
- `hypotheses` — proposed approaches + status.
- `experiments` — registry keyed by `code_hash`/`config_hash` (dedup + result cache).
- `findings` — profiler/researcher distilled notes.
- `negative_ledger` — ruled-out approaches with evidence (first-class).

### Message schema
```json
{
  "msg_id": "uuid",
  "ts": "2026-07-20T10:03:22Z",
  "from": "coder.opus.A",
  "to": "orchestrator|broadcast",
  "type": "claim_request|result|help_request|steer_ack|escalation|eval_request",
  "task_id": "t_00042",
  "payload": { "...": "type-specific" },
  "cost_usd": 0.42,
  "tokens": {"in": 18211, "out": 3044}
}
```

### ExperimentResult (written on every attempt — the anti-plateau backbone)
```json
{
  "code_hash": "sha256:...",
  "config_hash": "sha256:...",
  "task_id": "t_00042",
  "strategy": "S2_interleaved_microkernel",
  "island": 1,
  "author": "coder.gpt56.B",
  "eval_tier": "proxy|authoritative",
  "correct": true,
  "seeds_passed": [11, 27, 43],
  "score": {"metric": "geomean_latency_ms", "value": 0.834, "noise_std": 0.012},
  "profile_ref": "finding_00311",
  "parent_code_hash": "sha256:...",
  "artifacts": {"diff": "path", "stdout": "path", "ncu_rep": "path"},
  "ts": "..."
}
```

### NEGATIVE-results ledger entry (directly solves pain point #1)
```json
{
  "neg_id": "n_0007",
  "approach_class": "reduced_precision_panel",
  "summary": "TF32/FP16 panel factorization for diagonal block",
  "evidence": {
    "code_hash": "sha256:...",
    "failure_mode": "correctness",
    "seed": 27,
    "max_abs_error": 3.1e-2,
    "tolerance": 1e-5
  },
  "ruled_out_scope": "all shapes where n<=32 diagonal block",
  "confidence": "high",
  "authored_by": "verifier",
  "ts": "..."
}
```

### Query-before-work protocol (mandatory)
1. Before proposing an approach, an agent calls `query_knowledge(strategy_hash, embedding)` → returns matching experiments (any tier) + semantically-near negative entries (embedding similarity over `approach_class`+`summary`).
2. If a near-duplicate positive result exists → build on it (fetch parent), don't redo.
3. If a near-duplicate negative exists above `similarity_threshold` → the approach is blocked unless the agent supplies a *distinguishing hypothesis* (why this case differs), which is logged. This is how the harness stops re-exploring precision tricks/streams/micro-tuning.

---

## Evaluation Harness Integration

### Pluggable Problem Definition interface
A problem is a directory implementing a small interface (Python entrypoints + a manifest). This mirrors GPU MODE's `reference.py`/`task.yml`/`task.py` triple and Karpathy's single-file+metric design.

```yaml
# problem.yaml
name: batched_cholesky_b200
description: "Batched lower-triangular Cholesky, 15 shapes, geomean latency."
entrypoints:
  build:        "python problem/build.py {src} {out}"      # returns rc; compiler errors on stderr
  correctness:  "python problem/check.py {artifact} --seed {seed}"  # exit 0 = correct; prints max_abs_error
  benchmark:    "python problem/bench.py {artifact} --shapes all" # prints JSON scores
  profile:      "python problem/profile.py {artifact}"     # optional; emits ncu-rep
score:
  metric: geomean_latency_ms
  direction: minimize
  noise_model: "median_of_k"   # k repeated runs; store noise_std
correctness:
  tolerance: 1.0e-5
  seeds: [11, 27, 43, 59, 71]  # multiple held-out seeds; candidate never sees reference outputs
evaluation_tiers:
  proxy:
    backend: local_gpu          # or modal:L40S
    shapes: subset               # cheaper: fewer shapes / smaller batch
    cost_class: cheap
  authoritative:
    backend: leaderboard_b200    # remote, rationed
    shapes: all
    cost_class: expensive
    ration: {max_per_window: 1, window_seconds: 1800}   # models 1 submission / 30 min
    requires_human_approval: true
```

### Two-tier evaluation & rationing
- **Tier 1 (proxy):** cheap, fast, local (or a small Modal GPU); may use a shape subset and fewer repeats. Used for the inner loop (dozens–hundreds of runs). Because proxy≠authoritative, the harness continuously fits a **proxy→authoritative calibration** (records both whenever an authoritative run happens; tracks correlation and residual).
- **Tier 2 (authoritative):** the real leaderboard/B200 eval. Rationed by a **token-bucket** (`max_per_window` per `window_seconds`). The orchestrator only spends a token when a candidate: (1) passes correctness on all seeds, (2) beats the current authoritative champion on the proxy by more than `noise_std × margin_k`, and (3) clears a human approval checkpoint (configurable). This directly implements pain point #2.
- **Correctness is a hard gate at both tiers, on multiple held-out seeds, and the candidate is never given reference outputs** — the Sakana lesson. Mirroring KernelBot's design, the verifier runs a public-style check (verbose) and a private-style check (**different seed, timing-only**) and flags any candidate that passes one but not the other.

### Anti-reward-hacking guardrails
- Candidate code executes in an isolated subprocess/sandbox with no read access to reference-output files or the timing harness internals.
- Timing anomalies (e.g., implausibly low latency, output identical across seeds) auto-flag the result and quarantine the code-hash.
- Correctness is re-verified on the authoritative tier before any score is accepted onto the leaderboard-facing champion slot.

---

## Stall / Staleness Handling

Two meanings of "staleness," both handled:

### (A) Stalls / hangs / no-progress
- **Heartbeats + watchdog.** Each running agent writes a heartbeat every `heartbeat_seconds`. Missed heartbeat → suspect → orchestrator probes → if unresponsive within `probe_seconds`, mark failed, kill the process, return the task to `pending`, respawn a fresh agent (shadow-agent pattern), restore last known-good worktree state.
- **No-progress detection.** If no new best score in `stall_iters` iterations (per island), or wall-clock/token budget for a task exceeds cap, the task is preempted and its island triggers an exploration jump (below).
- **Repeated-action detection.** Hash each (tool_call, args) and each produced diff; if the same hash repeats `repeat_k` times, or a coder emits a near-identical diff (normalized), the loop detector fires and the agent is respawned with a mutated prompt.

### (B) Stale context / knowledge (context rot)
- **Proactive compaction** at ~60% context fill (not at the limit), using a custom compaction prompt that *preserves* the task goal, current best score, active hypothesis, and the negative-ledger slice (durable facts survive summarization).
- **Fresh-context respawn** as the default recovery: because context rot degrades all models as context grows, long-running coders are periodically retired and respawned from a clean context seeded only with (champion code + relevant findings + negative slice) pulled from the store. Design assumption: *any agent must be reconstructable from the shared store alone.*
- **CLAUDE.md / program.md** hold stable rules; transient chatter stays transient.

---

## Loop Prevention & Plateau-Breaking

- **Action/state hashing & novelty checks** as above.
- **Island model (from AlphaEvolve/OpenEvolve).** Candidates are partitioned into `num_islands` populations that evolve semi-independently with periodic **migration** (`migration_interval`). This preserves diversity and prevents the whole fleet collapsing onto one local optimum — the structural fix for "everyone re-tunes S2."
- **MAP-Elites-style archive.** Keep not just the single best but a grid of elites across feature dimensions (e.g., {memory-bound↔compute-bound} × {code complexity} × {strategy family}) so distinct "species" survive and seed future prompts.
- **Novelty filtering.** Embed each candidate approach; if cosine similarity to an existing one exceeds `similarity_threshold` (e.g., 0.99), treat as duplicate.
- **Bandit / tournament strategy selection.** Treat each strategy-island as an arm; allocate the next coder-iteration budget via UCB-style scoring (reward = recent score improvement per token). On plateau, force-sample the least-explored arm and escalate one sub-problem to the strongest model. Tournament (Elo pairwise, Co-Scientist style) ranks candidates for promotion.

---

## Give-Up Handling

Distinguish a *legitimate dead-end* from *premature surrender*:
- **Legitimate dead-end:** correctness impossible within constraints, or measured score provably worse with tight CI, reproduced on ≥2 seeds. → Record to NEGATIVE ledger with evidence; close task as `failed` (dead-end); mark the approach_class ruled-out with scope.
- **Premature give-up (detected):** agent stops with low iteration count, vague reason ("this seems hard"), no evidence, or before exhausting cheap mutations. The critic flags it; the orchestrator applies the **escalation ladder**:
  1. **Retry** same model, fresh context.
  2. **Mutate** — perturb prompt / raise temperature / inject a profiler hint or a specific negative-ledger reminder.
  3. **Escalate model** — move the sub-problem up the tier ladder (cheap → mid → frontier).
  4. **Decompose** — split into sub-tasks (diagonal-block inversion, trailing update, data layout) each separately assigned.
  5. **Surface to human** — post to the TUI/`steering.md` with the full context and the specific blocker.

Escalation state (`attempts`, `ladder_rung`) lives on the task so respawned agents don't reset progress.

---

## Steering Interface

- **CLI:** `harness run problem.yaml`, `harness pause`, `harness resume`, `harness status`, `harness steer "message"`, `harness approve <eval_request_id>`, `harness ledger --negative`, `harness champion --export`.
- **Hot-reloaded `steering.md`.** A human-editable file the orchestrator re-reads **between iterations** (never mid-eval). It can: reprioritize islands/strategies, add/forbid approaches (writes a soft negative entry), pin the model tier for a role, change budgets, or inject a free-text hint that is appended to coder prompts. This is the low-friction mechanism that replaces KD's ad hoc steering.
- **Priority queue override.** `steering.md` entries map to task-priority boosts and island weights.
- **Human approval checkpoints.** Authoritative-eval requests appear as `eval_request` items; unless `requires_human_approval:false`, the orchestrator waits (or auto-approves within a rate/policy budget). Pause/resume is global and safe (agents finish their current atomic step, checkpoint, and idle).

---

## Observability

- **Structured logs** (JSONL) for every agent action, tool call, message, and eval, with `run_id`, `task_id`, `agent_id`, `cost_usd`, tokens.
- **Run traces** reconstruct the full experiment tree (parent/child code-hashes) and per-island evolution.
- **Per-agent & per-provider cost/token accounting** sourced from LiteLLM spend + Claude Agent SDK `ResultMessage.total_cost_usd`; budgets enforced per role/model with hard cutoffs (LiteLLM virtual keys).
- **Live TUI dashboard** showing: agent states (idle/running/stalled), current champion + best-score-over-time sparkline, authoritative-eval token-bucket level, ledger growth (experiments / negatives), cost burn vs budget, and the last N steering actions. A deterministic, zero-cost monitor (no LLM) runs alongside to catch failures agents can't self-report.

---

## Storage Design

- **SQLite (default; Postgres optional at scale)** for `tasks`, `messages`, `hypotheses`, `experiments`, `findings`, `negative_ledger`. SQLite doubles as the coordination bus (atomic claims via transactions) — simplest thing that works; upgrade to Postgres + Redis only if multi-host. (Note WAL mode + short transactions for concurrent writers.)
- **Filesystem + git** for code artifacts: **one git worktree per coder agent** (`.claude/worktrees/<agent>/`), champion on a protected branch, each verified candidate a commit; merges gated by the verifier. Cap concurrent worktrees at ~4–8. Enforce per-worktree scope (agents may not edit outside their directory) via CLAUDE.md + a PreToolUse hook.
- **Artifact store** (plain dir keyed by code-hash) for diffs, stdout/stderr, `.ncu-rep` profiles.
- **Optional embeddings** (sqlite-vec or a small local index) for knowledge retrieval and novelty/negative-ledger similarity.

---

## Tech Stack Recommendation (with rationale)

- **Language:** Python 3.11+ (KD's strength; ecosystem for CUDA tooling, Modal, LiteLLM).
- **Provider layer:** **LiteLLM** as the multi-provider gateway (hard requirement met): Anthropic, OpenAI, DeepSeek, GLM, MiniMax behind one OpenAI-style API, with per-model/per-key budgets, fallback chains, and Prometheus/Redis spend tracking. Anthropic is first-class: coder agents may run as **Claude Agent SDK** sessions (native subagents, hooks, compaction, session resume, cost in `ResultMessage`) when the provider is Anthropic; other providers run through a thin agent-loop shim with the same tool interface.
- **Coding-agent substrate:** pluggable — a role can be backed by (a) Claude Agent SDK / Claude Code headless (`claude -p`), or (b) a generic tools-in-a-loop agent over LiteLLM for non-Anthropic models. Both must implement the same `Agent` protocol (claim → work → emit ExperimentResult → heartbeat).
- **Execution backends (pluggable):** local sandboxed subprocess for CPU/dev; **Modal** for on-demand GPU (offers native GPU sandboxes incl. B200, gVisor isolation, fast cold start) for proxy eval; **SSH runner** or the leaderboard submission path for authoritative B200. The `ExecutionBackend` interface is `build/run/collect` returning structured results.
- **Profiling:** NVIDIA Nsight Compute (`ncu`) invoked in the profile entrypoint; output parsed to text (KEET/CudaForge pattern).
- **Orchestrator/loop:** plain Python asyncio scheduler (no heavy framework needed); optionally LangGraph later if graph-structured durable execution is wanted, but default is a small, auditable scheduler (the "simplest thing that works," per Anthropic's guidance).
- **TUI:** Textual or Rich.

---

## Configuration Format (example)

```yaml
# fleet.yaml
run_id: cholesky-b200-2026-07-20
problem: ./problems/batched_cholesky_b200/problem.yaml

budgets:
  total_usd: 150.00
  per_role_usd: { coder: 90, critic: 20, researcher: 15, planner: 15 }
  wall_clock_hours: 12

fleet:
  orchestrator: { model: anthropic/claude-opus-4.8, tier: frontier, substrate: claude_agent_sdk }
  planner:      { model: anthropic/claude-opus-4.8, tier: frontier }
  coders:
    - { id: coder.opus.A, model: anthropic/claude-opus-4.8, substrate: claude_agent_sdk, worktree: true }
    - { id: coder.gpt.B,  model: openai/gpt-5.6-codex,      substrate: litellm_loop,     worktree: true }
  critics:
    - { id: critic.deepseek, model: deepseek/deepseek-v4, tier: cheap }
    - { id: critic.glm,      model: zhipu/glm-4.7,        tier: cheap }
    - { id: critic.minimax,  model: minimax/abab-7,       tier: cheap }
  researcher: { id: res.1, model: deepseek/deepseek-v4, tools: [web_search] }
  profiler_analyst: { id: prof.1, model: anthropic/claude-sonnet-4.6 }

search:
  num_islands: 4
  migration_interval: 20
  similarity_threshold: 0.97
  stall_iters: 12
  bandit: ucb

policies:
  heartbeat_seconds: 30
  lease_seconds: 900
  repeat_k: 3
  escalation_ladder: [retry, mutate, escalate_model, decompose, human]
  max_coders: 4
  subagent_spawn_depth: 1   # coders cannot spawn coders

evaluation:
  proxy_backend: modal:L40S
  authoritative_backend: leaderboard_b200
  authoritative_ration: { max_per_window: 1, window_seconds: 1800 }
  promote_margin_k: 2.0     # must beat champion by 2×noise_std on proxy
  require_human_approval: true

storage:
  db: sqlite:///runs/cholesky.db
  artifacts: ./runs/artifacts
  embeddings: sqlite_vec
```

---

## Phased Implementation Plan (for Claude Code) with Acceptance Criteria

Each phase is end-to-end testable on a **toy problem** first: `optimize_function` — minimize the runtime of a pure-Python/NumPy function (or maximize a scalar), with a trivial local evaluator — before pointing at the GPU problem.

### Phase 0 — Repo skeleton + config + provider abstraction
- Deliver: repo layout, `pyproject.toml`, config loader (`fleet.yaml`/`problem.yaml`), LiteLLM provider wrapper with budget/cost tracking, logging (JSONL), a `.env` for keys.
- **Directory sketch:**
```
autoresearch/
  orchestrator/    scheduler.py policies.py steering.py
  agents/          base.py coder.py planner.py critic.py researcher.py profiler.py verifier.py
  eval/            harness.py tiers.py backends/{local.py,modal.py,ssh.py,leaderboard.py}
  store/           db.py schema.sql ledger.py knowledge.py embeddings.py
  providers/       llm.py            # LiteLLM wrapper + Claude Agent SDK adapter
  problems/        optimize_function/  batched_cholesky_b200/
  tui/             dashboard.py
  cli.py
  tests/
```
- **Acceptance:** `harness ping` calls all configured providers through LiteLLM, prints per-call cost and token counts, and enforces a $ cap (request blocked when exceeded). Config validates and round-trips. CI runs unit tests with mocked providers.

### Phase 1 — Single-agent loop against the pluggable eval harness
- Deliver: one coder agent + the Problem Definition interface + Tier-1 local eval + experiment registry (code-hash dedup) + best-score tracking.
- **Acceptance:** on `optimize_function`, a single agent iteratively improves the score, every attempt is recorded with a code-hash, re-proposing an identical candidate is skipped via dedup, and the run ends with a champion + trace. Correctness gate rejects incorrect candidates. Works end-to-end with at least two providers.

### Phase 2 — Orchestrator + parallel coders + task ledger + dedup
- Deliver: orchestrator scheduler, task state machine, atomic claim/lease, ≥2 parallel coders in separate worktrees, blackboard tables, query-before-work.
- **Acceptance:** two coders run concurrently without ever claiming the same task (verified by a stress test), duplicate strategies are deduped across agents, a killed agent's task returns to `pending` after lease expiry and is picked up, and the champion is selected across both agents. No direct agent-to-agent chatter (all via store).

### Phase 3 — Stall/loop/give-up policies + steering + NEGATIVE ledger
- Deliver: heartbeats+watchdog, no-progress + repeated-diff detection, respawn-with-fresh-context, island/bandit exploration, escalation ladder, `steering.md` hot-reload, negative-results ledger with query-before-work blocking.
- **Acceptance:** (a) an intentionally stalling agent is detected and respawned; (b) a forced repeated-diff triggers loop-break; (c) a ruled-out approach written to the negative ledger is subsequently *blocked* for another agent unless it supplies a distinguishing hypothesis (unit-tested); (d) editing `steering.md` mid-run reprioritizes tasks within one iteration; (e) on plateau, the bandit forces exploration of the least-explored island. Demonstrated on `optimize_function` with an injected dead-end.

### Phase 4 — Observability, cost accounting, scale-out
- Deliver: TUI dashboard, per-agent/provider cost accounting, two-tier eval with authoritative rationing + human approval, Modal backend + leaderboard/SSH backend, embeddings-based knowledge retrieval, Postgres option.
- **Acceptance:** TUI shows live agent states, best-score-over-time, ration-bucket level, ledger growth, and cost vs budget; the authoritative tier is provably rationed (token-bucket unit test: the (N+1)th request in a window is deferred); a human approval checkpoint gates an authoritative eval; a full dry-run on the **batched Cholesky** problem executes proxy evals on Modal and queues (mock or real) B200 authoritative submissions respecting the 1/30-min limit; proxy→authoritative calibration is recorded.

**Definition of done (whole system):** an overnight unattended run on the batched-Cholesky/B200 problem produces (1) a champion kernel that is correct on all held-out seeds and improves geomean latency over baseline, (2) a populated negative ledger that demonstrably prevented re-exploration, (3) a complete cost/trace record within budget, and (4) reproducibility from the store alone.

---

## Risks, Open Questions, Future Work

### Risks
- **Reward hacking / eval gaming** (Sakana precedent — the AI CUDA Engineer found a memory exploit that skipped correctness checks): mitigated by held-out seeds, no candidate access to reference outputs, public+private double-check, timing-anomaly quarantine — but adversarial candidates remain a live threat; correctness gates must stay conservative.
- **Overfitting to the proxy metric** (Karpathy's caveat): the proxy may reward things the authoritative eval doesn't. Mitigated by continuous proxy→authoritative calibration and promotion margins that exceed measured noise; still an open risk.
- **Benchmark noise:** kernel timings are noisy (EvoEngineer explicitly discards genuinely-superior kernels on single noisy measurements and uses median-of-runs); median-of-k + noise_std + CI-based comparisons are required or the search will chase noise.
- **Cost blow-up:** multi-agent uses ~15× the tokens of a single chat (Anthropic) and large parallel runs can burn tens of billions of tokens (Grit's ~45B-token rewrite). Hard per-role budgets, cheap-model triage tiers, and subagent-depth caps are mandatory.
- **Coordination bugs:** worktree merge hygiene and logical (not just file) conflicts must be engineered; SQLite-as-bus needs WAL + short transactions to avoid writer contention.
- **Provider drift/instability:** model names, pricing, and SDK behavior change fast; the provider layer must isolate this.

### Open questions
- Best calibration model for proxy→authoritative transfer per problem class.
- Optimal island count / migration cadence / bandit reward shaping for kernel search specifically.
- How much orchestration should be LLM-driven vs deterministic (default: mostly deterministic).
- Whether to adopt LangGraph/durable-execution for crash recovery or keep a bespoke checkpointer.
- Right abstraction for the authoritative-submission adapter across different leaderboards/evaluators.

### Future work / open-source packaging
- Ship reference problem definitions (`optimize_function`, a KernelBench-style local kernel task, batched Cholesky) so users can validate before wiring their own.
- Plugin API for Problem Definitions, Execution Backends, and Agent substrates; document the `Agent`, `ExecutionBackend`, and `ProblemDefinition` protocols.
- Provide a `program.md`/CLAUDE.md template library ("research org code").
- Sensible licensing (MIT, matching the `autoresearch` precedent), a security note on sandboxing untrusted generated code, and a cost-guardrail default so first-time users can't accidentally spend a fortune.
- Optional: distributed multi-host mode (Postgres + Redis + object store) and a web dashboard.

---

### Appendix: Design-decision → source map (for reviewers)
- Orchestrator-worker + subagent isolation + spawn caps → Anthropic multi-agent research system (2025).
- Program database, islands, MAP-Elites, evaluation cascades, artifact side-channel → AlphaEvolve / FunSearch / OpenEvolve.
- Tree-of-code search, operators-as-bottleneck → AIDE / AI Scientist-v2.
- Elo tournament + Proximity diversity + Meta-review prompt-tuning → Google Co-Scientist (Nature 2026).
- `fast_p`, NCU-as-feedback, Coder+Judge, heterogeneous-model advantage, timing-noise handling → KernelBench, CudaForge, KEET, KernelAgent, EvoEngineer.
- Public+private double-eval, isolated subprocess, problem-definition triple, Modal/GHA runners → KernelBot / GPU MODE.
- Reward-hacking guardrails → Sakana AI CUDA Engineer postmortem.
- Loop/stall detection, heartbeats/shadow-agent, deterministic monitors → IAL, TIDE, agent-loop literature, fault-tolerant-agent patent.
- Context rot → Chroma study; compaction/memory/PreCompact + fresh-context respawn → Anthropic context-engineering guidance / Claude Agent SDK.
- Worktree-per-agent → Claude Code worktree docs; Grit (~45B tokens) coordination lessons.
- LiteLLM budgets/virtual-keys/fallbacks → LiteLLM docs.
- Fixed-budget single-metric eval, proxy-design-is-the-bottleneck, overfitting caveat → Karpathy `autoresearch` (2026).
