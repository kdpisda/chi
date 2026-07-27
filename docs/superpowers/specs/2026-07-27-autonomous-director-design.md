# Chi Autonomous Research Director — Design

**Date:** 2026-07-27
**Status:** Draft (awaiting KD review)
**Scope:** Add a persistent, above-the-fleet LLM control loop ("the Director") so that
`chi` becomes true auto-research: the user gives ONE task, chi asks clarifying questions
if the task is thin, then it self-steers for hours — running the coder fleet, meta-reviewing
results, researching when stuck, mutating strategies, and breaking plateaus — with no
per-iteration human steering. Supersedes the current behavior where the conversational
operator kicks off `start_run` and then goes idle while coders iterate on a fixed,
deterministic steering digest.

This design does NOT change the deterministic orchestrator (coder loop, watchdog, task
ledger) beyond one already-merged bug fix and one small ops hook; it adds a new layer
above it.

---

## 1. Context and evidence base

Grounded in two autonomous cholesky/B200 runs on 2026-07-27 (real popcorn scoring,
benchmark-only, no leaderboard submit; artifacts under `~/.local/share/chi/runs/`). What
those runs proved, and what they exposed, is the entire justification for this layer.

**Worked (keep, build on):**
- The operator takes one prompt, starts the fleet, self-verifies the safety rails, reports
  honestly. Real B200 scoring throughout (the scoring blocker was fixed separately —
  `chi/eval/popcorn_score.py`).
- **Genuine self-improvement within a coder:** grok-B climbed 721µs → 638µs across
  iterations, learning from its own recorded dead ends.
- The negative ledger accrues specific, correct dead-end fingerprints; the deterministic
  watchdog correctly reaps a truly dead coder (codex, 0 evals).

**Exposed (this design's targets):**
1. **Plateau at champion parity, no breakthrough.** Both working coders converged to
   636–655µs against a 636µs seeded champion and could not break it in 8 iterations.
   Ideas were plentiful; what was missing was an above-fleet strategist to notice the
   plateau and force a genuinely different direction. *(→ Director loop + Strategist)*
2. **Noise ≫ signal.** The same champion kernel measured 636 / 652 / 686µs across
   benchmarks (~8% run-to-run spread). Coders plateaued *inside* that band, so a single
   benchmark cannot distinguish a real 0.5% gain from variance. *(→ NoiseGuard)*
3. **No dead-class enforcement / no near-miss retention.** `bf16` was independently
   dead-ended five times across both coders; `n8192-panel-inv` by both. The ledger records
   free-text dead ends but they never become a fleet-wide "stop trying this class"
   constraint, and a near-parity attempt (637.8 vs 636) was discarded as a dead end
   instead of retained as a promising base. *(→ Strategist: dead-class + near-miss buckets)*
4. **Coders can't research.** The coder CLIs (`claude -p …`, `grok …`) have no web access;
   the operator's `fetch` tool is GET-one-URL only, with no search. When the fleet is out
   of ideas there is nowhere for new ideas to come from. *(→ Researcher via the CLI brain)*
5. **Watchdog false-kill (FIXED, merged `main` this session).** The repeat-hash rule hashed
   the on-disk `candidate.py`, but coders revert that file to the champion after a losing
   benchmark, so its hash looked unchanged every round; grok-B was killed for "unchanged 6×"
   despite evaluating 7 distinct kernels. Fixed by feeding the watchdog the hash of the
   candidate actually evaluated (`ledger.latest_experiment`). Recorded here as prior
   context; no further work.
6. **0-eval timeout wastes rounds.** claude-A (champion-tuner) lost iteration 0 to the
   30-minute timeout editing the 97 KB kernel, then recovered. A 0-eval timeout should
   trigger a fast adaptation, not a silent repeat. *(→ small ops hook in the Director's
   round review, not a change to the deterministic loop)*

---

## 2. Decisions (KD, 2026-07-27)

- **Research arm: route via the CLI brain.** The Director's brain is a web-capable vendor
  CLI (`claude`/`grok`); when stuck it asks that CLI to research and return findings. No new
  search API key. Constraint: research quality depends on the CLI's own web access, and it
  is less controllable than a dedicated search tool — accepted for v1.
- **Termination: runs until the user stops it.** No target score, no automatic budget cap.
  Maximum autonomy; the user watches spend. The Director MUST surface a running B200-benchmark
  count and $ cost so the spend is always visible, and MUST stop promptly on the user's signal.
- **Run model: hybrid watch + detach.** Interactive clarify at kickoff, then a durable
  background loop the user watches live, can detach from (Ctrl-D), reattach to (`/resume`),
  and interject into (free text → an operator steer folded into the next round).

**Two judgment calls (KD may veto at review):**
- **J1 — Leaderboard submission stays manual even under full autonomy.** The Director
  surfaces a NoiseGuard-verified improvement and recommends a submit; the user fires the
  ranked submission. Rationale: KD's standing instruction "we do the actual submission," and
  a ranked submit changes his live public rank irreversibly. `auto_submit` remains OFF by
  default; the existing `AutoSubmitter` rails remain available if he later opts in.
- **J2 — Build the median-of-N NoiseGuard now**, despite the extra B200 benchmarks it
  spends, because without it the Director cannot tell a real gain from the ~8% noise and
  would chase variance.

---

## 3. Where the Director lives

A **dedicated `Director` component** (a separate control loop), not an extension of the chat
operator and not embedded in the orchestrator.

```
  you ── talk ──▶ chat operator ──starts/stops/queries──▶ Director (this design)
                                                             │  owns the round loop
                                                             ▼
                                          deterministic orchestrator (start_run slice)
                                                             │  runs coder agents
                                                             ▼
                                                     shared blackboard store
```

- The **chat operator** (`chi/session/operator.py`) stays a talker: it gains
  `start_director` / `stop_director` / `director_status` tools and otherwise is unchanged.
- The **orchestrator** (`chi/orchestrator/loop.py`) stays deterministic and LLM-free; it
  gains a *bounded-slice* entry point so the Director can run the fleet N iterations at a
  time and get control back. No LLM calls move into it.
- The **Director** is the only new LLM-driven control surface. It is unit-testable in
  isolation with a fake brain and a fake round-runner — no live CLI, no GPU.

Rejected: (A) folding the loop into the chat operator couples live chat with a
hours-long autonomous loop and is hard to test; (C) embedding meta-review in the
orchestrator pollutes the deterministic watchdog/loop with LLM latency and entangles
"run agents" with "decide strategy."

---

## 4. The Director loop

One **round**, repeated until stopped:

```
1. RUN     RoundRunner runs the fleet for a bounded slice (N iters or T minutes),
           then returns control with the round's new experiments.
2. REVIEW  MetaReviewer builds a deterministic digest from the store (best Δ vs champion,
           dead-end classes this round, near-parity cluster, per-coder progress) and asks
           the brain to read it: what's working, what's dead, are we improving/plateaued/stuck.
3. CLASSIFY state ∈ {improving, plateaued, stuck} from explicit rules over the digest
           (see §6), not vibes — the brain's read is advisory, the rule decides.
4. RESEARCH  only if stuck: Researcher fires ONE web-capable brain call
           ("stuck at 636µs; classes [bf16, panel-inv, …] are dead; what genuinely
           different B200 batched-cholesky techniques exist?") → findings text.
5. STEER   Strategist rewrites steering.md AND mutates the per-coder strategies:
           retire dead classes (hard fleet-wide "don't"), promote near-parity attempts to
           new bases, inject researched ideas, invent a new strategy on a plateau.
6. FOLD    any user free-text queued since last round is merged into the steering as a
           priority directive.
   → loop to 1.
```

The loop is a plain Python `while not stopped:` with each step a small, injectable
collaborator. Every round appends a `DIRECTOR_ROUND` event (state, decisions, spend) to the
store so the transcript and `/resume` see it.

---

## 5. Components

Each is a focused unit with one job, an explicit interface, and a fake for tests.

- **`Director`** (`chi/director/loop.py`) — owns the round loop, the stop flag, the running
  spend counters, and the `DIRECTOR_ROUND` event emission. Depends on the four collaborators
  below plus the store and an `emit` callback (same progress channel the fleet uses).
- **`RoundRunner`** (`chi/director/round.py`) — runs one bounded fleet slice via a new
  orchestrator entry point and returns `RoundResult(new_experiments, best, spend)`. This is
  the seam that turns today's run-to-completion `start_run` into resumable slices; the fleet
  code itself is unchanged apart from honoring a per-slice iteration cap.
- **`MetaReviewer`** (`chi/director/review.py`) — pure-function digest from the store
  (`RoundDigest`) + one brain call for the qualitative read. Digest is deterministic and
  independently testable.
- **`Researcher`** (`chi/director/research.py`) — fires a single web-capable brain call on
  stuckness; returns findings text (capped length). Reuses the CLI-brain runner the operator
  already has (`CliOperatorChat`'s runner seam). No-ops gracefully if the brain has no web.
- **`Strategist`** (`chi/director/strategy.py`) — the heart. Turns the digest + findings into
  (a) a rewritten `steering.md`, (b) mutated per-coder strategy labels, (c) an updated
  dead-class set and near-miss set persisted to the store. Deterministic where it can be
  (dead-class accumulation, near-miss promotion), brain-assisted only for inventing a new
  strategy.
- **`NoiseGuard`** (`chi/eval/noise.py`) — given a candidate that appears to beat the
  champion, re-benchmark it N times (default 3) and return the median; the Director believes
  an improvement only if the median clears the champion by the promote margin. Used for the
  "surface a verified improvement" recommendation (J1) and available to `AutoSubmitter` if
  auto-submit is ever enabled (J2). Costs N extra B200 benchmarks per candidate — bounded by
  firing only on apparent winners.

### Data added to the store
- `DIRECTOR_ROUND` event type (round index, state, actions taken, cumulative spend).
- Dead-class set and near-miss set: small keyed rows in the run's store (extend the negative
  ledger with a `class_key` and a `near_miss` flag rather than a new table where possible).

---

## 6. State classification (explicit rules)

Over a sliding window of recent rounds (default 2):
- **improving** — best score improved by > promote-margin (default 0.5%) *and* the
  improvement survives NoiseGuard. Action: keep current strategies, let it ride.
- **plateaued** — best within ±noise-band (default ~8%, configurable) of the prior best for
  the whole window, with new distinct candidates still being produced. Action: Strategist
  invents a new strategy / promotes a near-miss; NO research yet.
- **stuck** — plateaued *and* (a dead-class was hit again, or ≥ K rounds — default 2 — with no
  distinct new approach class). Action: Researcher fires, findings feed the Strategist.

Rules decide; the brain's qualitative read is logged as advice but cannot override the rule
(keeps the loop from talking itself in circles — a lesson from the manual campaign's
confounded rule-outs).

---

## 7. Control model (hybrid watch + detach)

- **Kickoff:** user gives the task in the session. If the task is thin (no problem dir / no
  goal / ambiguous), the operator asks clarifying questions using the existing `ask_fn`
  toggler before starting — one focused question at a time. Once it has a problem pack and a
  direction, it calls `start_director`.
- **Run:** the Director runs as a durable background loop under the existing `RunHandle`
  background-thread model, emitting to the same live event channel. The user watches rounds,
  scores, state transitions, research findings, and the running spend counter stream by.
- **Detach / reattach:** Ctrl-D detaches (loop keeps running); `/resume` reattaches from any
  terminal (the loop is store-backed, so a reattached session replays from `DIRECTOR_ROUND`
  events). Same mechanism as today's run resume.
- **Interject:** free text while the Director runs is queued and folded into the next round's
  steering as a priority directive (step 6). It does not interrupt an in-flight fleet slice.
- **Stop:** explicit `/stop` (or "stop"). The Director finishes any in-flight fleet slice's
  current iteration, writes a final `DIRECTOR_ROUND`, and halts. Because there is no automatic
  termination (KD's choice), the visible spend counter is the guardrail.

---

## 8. Error handling & safety

- **Spend visibility, not spend cap.** No auto-stop, so the Director MUST keep a live
  `benchmarks_run` and `cost_usd` counter in every `DIRECTOR_ROUND` and in the status line.
- **Ranked submit stays manual (J1).** The Director never fires a ranked leaderboard submit;
  it recommends one after NoiseGuard verification and the user executes it. `auto_submit`
  default OFF.
- **Brain failure is non-fatal.** A failed/empty research or strategy brain call degrades to
  "keep current strategies for one more round" and is logged — the loop never dies on a
  provider hiccup (same posture as `_free_text`'s recovery today).
- **Fleet-slice failure is isolated.** A RoundRunner exception ends that slice, is recorded,
  and the Director decides next round from whatever landed — one bad slice never sinks the run.
- **Research is read-only and SSRF-guarded** — reuses the existing `fetch` guards if the
  brain returns URLs to fetch.

---

## 9. Testing

- **Director loop** — fake brain (canned reviews/strategies) + fake RoundRunner (canned
  RoundResults) drive the loop through improving → plateaued → stuck → research → new-strategy
  and assert the emitted `DIRECTOR_ROUND` sequence and the stop behavior. No CLI, no GPU.
- **MetaReviewer digest** — pure-function tests over synthetic store states (best Δ, dead
  classes, near-miss cluster).
- **State classifier** — table tests: each of improving/plateaued/stuck from crafted windows,
  including the noise-band boundary.
- **Strategist** — dead-class accumulation and near-miss promotion are deterministic and
  unit-tested; new-strategy invention is tested with a fake brain.
- **NoiseGuard** — median-of-N over a fake benchmark backend: a true improvement survives; a
  noise-only "win" (one lucky-low sample among champion-level samples) is rejected.
- **RoundRunner** — a bounded slice runs exactly N iterations and returns control with the
  round's experiments (scripted adapter, no GPU).
- **Integration (opt-in, real B200, marked slow)** — one short real Director run behind
  `CHI_ALLOW_REMOTE_BENCH=1`, asserting rounds advance, spend is counted, and no ranked submit
  is fired.

---

## 10. Out of scope (explicit)

- Automatic termination / budget caps (KD chose run-until-stopped).
- A dedicated web-search API tool (KD chose CLI-brain research).
- Autonomous ranked leaderboard submission (J1 — stays manual).
- Islands / bandit strategy allocation (later; the Strategist's mutation is the v1 stand-in).
- Changing the deterministic orchestrator/watchdog beyond the merged fix and the bounded-slice
  entry point.

---

## 11. Build order

1. `RoundRunner` + orchestrator bounded-slice entry point (unblocks everything; scripted-adapter tests).
2. `MetaReviewer` digest + state classifier (pure, deterministic, fully tested).
3. `NoiseGuard` (independent; J2).
4. `Strategist` (dead-class + near-miss deterministic parts, then brain-assisted invention).
5. `Researcher` (CLI-brain call; degrade-gracefully).
6. `Director` loop wiring it together + `DIRECTOR_ROUND` events + spend counters.
7. Operator tools (`start_director`/`stop_director`/`director_status`) + clarify-at-kickoff.
8. Hybrid detach/reattach/interject over the existing resume mechanism.
9. Opt-in real-B200 integration test.
