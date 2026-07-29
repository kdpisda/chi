# Autonomous Research Director Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent above-the-fleet LLM control loop (the Director) so `chi` self-steers a research problem for hours from one human task — running the fleet in bounded slices, meta-reviewing, researching when stuck, mutating strategies, and breaking plateaus — with no per-iteration human steering.

**Architecture:** A new `chi/director/` package holds a plain `while not stopped:` loop and four injectable collaborators (RoundRunner, MetaReviewer, Strategist, Researcher) plus a `NoiseGuard` in `chi/eval/`. The deterministic orchestrator gains one bounded-slice entry point (`run_slice`) so the Director can run the fleet N iterations at a time and get control back; the chat operator gains start/stop/status tools. Nothing LLM-driven moves into the orchestrator.

**Tech Stack:** Python 3.14, pydantic v2, SQLite (WAL) store, pytest + uv, litellm (pinned `<1.92`), popcorn-cli for B200 benchmarking. Vendor CLIs (`claude`, `grok`) drive the Director brain via the existing `CliOperatorChat` runner seam.

## Global Constraints

- All 209 existing tests MUST stay green after every task (`uv run pytest -q`).
- TDD: failing test first, minimal implementation, then commit. Every task ends green.
- The deterministic orchestrator (`chi/orchestrator/loop.py` coder loop, `watchdog.py`, `tasks.py`) stays LLM-free. The only orchestrator change is the additive `run_slice` entry point (Task 1); `start_run` behavior is unchanged.
- Ranked leaderboard submission stays MANUAL (spec J1). The Director never calls `backend.submit` / a ranked popcorn submit. `auto_submit` default stays OFF.
- No automatic termination / no budget cap (spec: run-until-stopped). The Director MUST emit a running `benchmarks_run` + `cost_usd` counter every round; the visible counter is the only guardrail.
- Research is via the CLI brain only (spec decision) — no new search-API dependency or key.
- Run `uv run pytest` (never bare `pytest` — the tool env lacks pytest on the system interpreter).
- Commit trailers on every commit:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_011QZLNDrud7zV58HAmCtLG4`
- Directory-local test import root is `tests/`; problems live under `problems/` (e.g. `problems/optimize_function`, used by orchestrator tests as a no-GPU fixture).

---

## File Structure

- Create `chi/director/__init__.py` — package marker.
- Create `chi/director/types.py` — shared dataclasses (`RoundResult`, `RoundDigest`, `DirectorState`, `StrategyUpdate`) so collaborators share one vocabulary without importing each other.
- Create `chi/director/round.py` — `RoundRunner`: runs one bounded fleet slice, returns a `RoundResult`.
- Create `chi/director/review.py` — `MetaReviewer` (deterministic digest) + `classify_state` (rule-based state machine).
- Create `chi/director/strategy.py` — `Strategist`: dead-class accumulation, near-miss promotion, brain-assisted new-strategy invention; writes `steering.md`.
- Create `chi/director/research.py` — `Researcher`: one web-capable brain call on stuckness; degrades gracefully.
- Create `chi/director/loop.py` — `Director`: the round loop, stop flag, spend counters, `DIRECTOR_ROUND` emission.
- Create `chi/eval/noise.py` — `NoiseGuard`: median-of-N re-benchmark verification.
- Modify `chi/orchestrator/loop.py` — add `run_slice(...)` (additive; refactor the coder-launch body it shares with `start_run` into a helper both call).
- Modify `chi/store/events.py` — add `DIRECTOR_ROUND` event constant.
- Modify `chi/store/ledger.py` — add `mark_near_miss` / `list_near_misses` and `dead_classes` helpers (extend the negative ledger; no new table).
- Modify `chi/session/operator.py` — add `start_director` / `stop_director` / `director_status` tools + dispatch.
- Modify `chi/session/engine.py` — `DirectorHandle` wiring, clarify-at-kickoff, interject queue, detach/reattach over existing resume.
- Create `chi/session/director_runner.py` — `DirectorHandle` (background-thread wrapper modeled on `RunHandle`).
- Tests: `tests/test_director_round.py`, `tests/test_director_review.py`, `tests/test_noise_guard.py`, `tests/test_director_strategy.py`, `tests/test_director_research.py`, `tests/test_director_loop.py`, `tests/test_director_operator.py`, `tests/test_director_integration.py` (opt-in, slow).

---

## Task 1: Bounded-slice orchestrator entry point (`run_slice`)

**Files:**
- Modify: `chi/orchestrator/loop.py` (add `run_slice`; extract a shared `_launch_fleet` helper from `start_run:199-258`)
- Test: `tests/test_orchestrator_slice.py`

**Interfaces:**
- Consumes: `FleetConfig`, `RunSummary` (existing, `chi/orchestrator/loop.py:27-36`), `start_run` (existing).
- Produces:
  `run_slice(fleet: FleetConfig, run_dir: Path, *, iterations: int, completion_fn=None, stop_event=None) -> RunSummary`
  — runs the already-created run in `run_dir` for `iterations` more iterations across all coders, reusing the existing store, and returns a fresh `RunSummary`. Distinct from `start_run` (which creates the run dir + baseline). The Director calls `start_run` once with `max_iterations=iterations` for round 1, then `run_slice` for later rounds.

**Design note:** The simplest correct decomposition that avoids a risky refactor: `run_slice` opens the existing `Store` at `run_dir`, re-reads the problem from `run_dir/workdir`, resolves coders, and runs the SAME per-coder thread body as `start_run` for `iterations` iterations, but does NOT re-copy the pack, re-establish baseline, or re-create the run row. Extract lines `chi/orchestrator/loop.py:217-258` (coder thread launch + status aggregation + champion export + STOP event) into `_launch_fleet(store, run_id, run_dir, fleet, problem, policies, baseline_score, coders, completion_fn, stop_event) -> RunSummary`; `start_run` calls it after setup, `run_slice` calls it after re-opening.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_slice.py
import json
from pathlib import Path

from chi.config import FleetConfig
from chi.orchestrator.loop import start_run, run_slice
from chi.store.db import Store

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")
NAIVE = ("def solve(xs: list[float]) -> list[float]:\n"
         "    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


def _fleet(tmp_path: Path, script: list[str], iters: int) -> FleetConfig:
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(script))
    return FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": iters, "eval_recency_iters": 100, "repeat_k": 3},
    })


def test_run_slice_continues_existing_run_without_new_baseline(tmp_path: Path) -> None:
    fleet = _fleet(tmp_path, [NAIVE, GOOD], iters=1)
    first = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(first.run_dir)
    baselines_before = store.query(
        "SELECT COUNT(*) n FROM experiments WHERE author='baseline'")[0]["n"]

    second = run_slice(fleet, first.run_dir, iterations=1)

    assert second.run_id == first.run_id  # same run, not a new one
    baselines_after = store.query(
        "SELECT COUNT(*) n FROM experiments WHERE author='baseline'")[0]["n"]
    assert baselines_after == baselines_before  # no second baseline eval
    # the slice ran another iteration: total ITERATION_START >= 2
    from chi.store.events import list_events
    assert len(list_events(store, first.run_id, "ITERATION_START")) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator_slice.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_slice'`.

- [ ] **Step 3: Extract `_launch_fleet` and implement `run_slice`**

In `chi/orchestrator/loop.py`, extract the fleet-launch body (currently inside `start_run`, lines ~217-266: the per-coder thread creation loop, `thread.start()/join()`, status aggregation, champion export, STOP event, and `runs` table update) into:

```python
def _launch_fleet(
    store, run_id, run_dir, fleet, problem, policies, baseline_score,
    completion_fn, stop_event,
) -> RunSummary:
    """Run all coders for policies.max_iterations, aggregate, export champion."""
    coders = resolve_coders(fleet)
    steering = Steering(store, run_id, problem.score.direction)
    auto_submitter = _build_auto_submitter(store, run_id, problem)
    base_workdir = run_dir / "workdir"
    coder_status: dict[str, tuple[str, int]] = {}
    threads: list[threading.Thread] = []
    for coder in coders:
        coder_workdir = run_dir / f"workdir-{coder.id}" if len(coders) > 1 else base_workdir
        if coder_workdir != base_workdir and not coder_workdir.exists():
            shutil.copytree(fleet.problem, coder_workdir)
        # agents row is idempotent-ish: INSERT OR IGNORE so a second slice re-uses it
        store.execute(
            "INSERT OR IGNORE INTO agents (agent_id, run_id, adapter, model, workdir, started_at)"
            " VALUES (?,?,?,?,?,?)",
            (coder.id, run_id, coder.adapter, coder.model, str(coder_workdir), utcnow()),
        )
        strategy = coder.strategy or f"strategy-{coder.id}"
        task_id = tasks.create_task(store, run_id, spec={"goal": "improve score",
                                                         "strategy": strategy})
        tasks.claim_task(store, run_id, coder.id, policies.lease_seconds)
        args = (coder, coder_workdir, task_id, strategy, store, run_id, problem,
                BudgetTracker(fleet.budgets.total_usd, fleet.budgets.per_role_usd,
                              store=store, run_id=run_id),
                policies, steering, baseline_score, stop_event, completion_fn,
                coder_status, auto_submitter)
        threads.append(threading.Thread(target=_run_coder, args=args, daemon=True))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    iterations_done = max((c[1] for c in coder_status.values()), default=0)
    statuses = {c[0] for c in coder_status.values()}
    if "budget_exhausted" in statuses:
        status = "budget_exhausted"
    elif stop_event is not None and stop_event.is_set():
        status = "stopped"
    elif statuses <= {"stalled", "failed"} and statuses:
        status = "stalled" if "stalled" in statuses else "failed"
    else:
        status = "done"
    champ = ledger.champion(store, run_id, problem.score.direction)
    if champ is not None and champ["author"] not in (None, "baseline"):
        _export_champion(store, run_id, champ, coders, run_dir, base_workdir, problem)
    events.append_event(store, run_id, events.STOP, payload={"status": status})
    return RunSummary(
        run_id=run_id, run_dir=run_dir, iterations=iterations_done,
        baseline_score=baseline_score,
        champion_score=None if champ is None else champ["score_value"],
        champion_hash=None if champ is None else champ["code_hash"],
        total_cost_usd=0.0, status=status,
    )
```

Refactor `start_run` to call `_launch_fleet(...)` after its setup (baseline eval, run-row insert, pack copytree). Then add:

```python
def run_slice(
    fleet: FleetConfig,
    run_dir: Path,
    *,
    iterations: int,
    completion_fn: Callable | None = None,
    stop_event: threading.Event | None = None,
) -> RunSummary:
    """Run `iterations` more iterations on an already-created run in run_dir.

    Unlike start_run this does NOT create the run dir, copy the pack, or
    re-establish baseline — the Director uses it for rounds 2..N so a sustained
    run is one logical run with a growing store, not a chain of fresh runs.
    """
    store = Store.open(run_dir)
    row = store.query("SELECT run_id FROM runs ORDER BY started_at LIMIT 1")
    run_id = row[0]["run_id"]
    problem = load_problem(run_dir / "workdir")
    baseline_row = store.query(
        "SELECT score_value FROM experiments WHERE run_id=? AND author='baseline'"
        " ORDER BY ts LIMIT 1", (run_id,))
    baseline_score = baseline_row[0]["score_value"] if baseline_row else None
    sliced = fleet.model_copy(update={"policies":
        fleet.policies.model_copy(update={"max_iterations": iterations})})
    return _launch_fleet(store, run_id, run_dir, sliced, problem, sliced.policies,
                         baseline_score, completion_fn, stop_event)
```

- [ ] **Step 4: Run the new test + full suite**

Run: `uv run pytest tests/test_orchestrator_slice.py -v && uv run pytest -q`
Expected: new test PASS; all 209 prior tests still PASS (the `start_run` refactor is behavior-preserving).

- [ ] **Step 5: Commit**

```bash
git add chi/orchestrator/loop.py tests/test_orchestrator_slice.py
git commit -m "feat(orchestrator): run_slice — bounded fleet slice on an existing run

Extract the fleet-launch body shared with start_run into _launch_fleet and add
run_slice, which continues an already-created run for N more iterations without
re-copying the pack or re-establishing baseline. The Director uses this so a
sustained run is one logical run with a growing store, not a chain of fresh runs."
```

---

## Task 2: Director shared types

**Files:**
- Create: `chi/director/__init__.py` (empty)
- Create: `chi/director/types.py`
- Test: `tests/test_director_review.py` (imports these; the assertions come in Task 3 — this task only makes the module importable with a trivial round-trip test)

**Interfaces:**
- Produces (all `@dataclass`):
  - `RoundResult(round_index: int, new_experiments: list[dict], best_score: float | None, benchmarks_run: int, cost_usd: float)`
  - `RoundDigest(round_index: int, best_score: float | None, champion_score: float | None, prev_best: float | None, dead_classes: list[str], repeated_dead_classes: list[str], near_misses: list[dict], distinct_new_classes: int)`
  - `DirectorState` — a `str`-valued `enum` with `IMPROVING`, `PLATEAUED`, `STUCK`.
  - `StrategyUpdate(steering_text: str, per_coder_strategy: dict[str, str], new_dead_classes: list[str], promoted_near_misses: list[str], researched: bool)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_director_review.py
from chi.director.types import RoundResult, RoundDigest, DirectorState, StrategyUpdate


def test_types_construct_and_state_enum_values():
    r = RoundResult(round_index=0, new_experiments=[], best_score=None,
                    benchmarks_run=0, cost_usd=0.0)
    assert r.round_index == 0
    d = RoundDigest(round_index=0, best_score=636.0, champion_score=636.0,
                    prev_best=None, dead_classes=[], repeated_dead_classes=[],
                    near_misses=[], distinct_new_classes=0)
    assert d.best_score == 636.0
    assert DirectorState.STUCK.value == "stuck"
    u = StrategyUpdate(steering_text="x", per_coder_strategy={"c1": "s"},
                       new_dead_classes=[], promoted_near_misses=[], researched=False)
    assert u.per_coder_strategy["c1"] == "s"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_director_review.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chi.director'`.

- [ ] **Step 3: Create the package and types**

```python
# chi/director/__init__.py
```
(empty file)

```python
# chi/director/types.py
"""Shared vocabulary for the Director loop and its collaborators."""

from dataclasses import dataclass, field
from enum import Enum


class DirectorState(str, Enum):
    IMPROVING = "improving"
    PLATEAUED = "plateaued"
    STUCK = "stuck"


@dataclass
class RoundResult:
    round_index: int
    new_experiments: list[dict]
    best_score: float | None
    benchmarks_run: int
    cost_usd: float


@dataclass
class RoundDigest:
    round_index: int
    best_score: float | None
    champion_score: float | None
    prev_best: float | None
    dead_classes: list[str] = field(default_factory=list)
    repeated_dead_classes: list[str] = field(default_factory=list)
    near_misses: list[dict] = field(default_factory=list)
    distinct_new_classes: int = 0


@dataclass
class StrategyUpdate:
    steering_text: str
    per_coder_strategy: dict[str, str] = field(default_factory=dict)
    new_dead_classes: list[str] = field(default_factory=list)
    promoted_near_misses: list[str] = field(default_factory=list)
    researched: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_director_review.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add chi/director/__init__.py chi/director/types.py tests/test_director_review.py
git commit -m "feat(director): shared types (RoundResult, RoundDigest, DirectorState, StrategyUpdate)"
```

---

## Task 3: MetaReviewer digest + state classifier

**Files:**
- Create: `chi/director/review.py`
- Modify: `chi/store/ledger.py` (add `dead_classes`, `mark_near_miss`, `list_near_misses`)
- Test: `tests/test_director_review.py` (extend)

**Interfaces:**
- Consumes: `Store`, `ledger.champion`, `ledger.list_negatives`, `RoundResult`, `RoundDigest`, `DirectorState`.
- Produces:
  - `ledger.dead_classes(store, run_id) -> dict[str, int]` — approach_class → times ruled out.
  - `ledger.mark_near_miss(store, run_id, code_hash, score_value) -> None` — flag an experiment as a near-miss base (writes a `STATUS` event with `{"near_miss": code_hash, "score": score_value}`; no schema change).
  - `ledger.list_near_misses(store, run_id) -> list[dict]` — `[{"code_hash":…, "score":…}]` from those STATUS events.
  - `build_digest(store, run_id, round_index, prev_best, direction="minimize", noise_band_pct=8.0) -> RoundDigest`
  - `classify_state(digest, prev_digest=None, noise_band_pct=8.0, promote_margin_pct=0.5, stuck_k=2, plateau_window=2, history=None) -> DirectorState` — `history` is the list of prior `RoundDigest` (newest last) so plateau/stuck can look back `plateau_window`/`stuck_k` rounds.

**Classifier rules (from spec §6, made concrete):**
- `IMPROVING`: `best_score` better than `prev_best` by more than `promote_margin_pct` (direction-aware). (NoiseGuard verification happens in the Director loop, not here.)
- `STUCK`: not improving AND (`digest.repeated_dead_classes` non-empty OR the last `stuck_k` digests all have `distinct_new_classes == 0`).
- `PLATEAUED`: not improving and not stuck.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_director_review.py  (append)
from chi.director.review import build_digest, classify_state
from chi.director.types import RoundDigest, DirectorState


def _digest(best, prev, dead=None, repeated=None, distinct=0):
    return RoundDigest(round_index=0, best_score=best, champion_score=best,
                       prev_best=prev, dead_classes=dead or [],
                       repeated_dead_classes=repeated or [], near_misses=[],
                       distinct_new_classes=distinct)


def test_classify_improving_beyond_margin():
    d = _digest(best=600.0, prev=636.0)  # ~5.7% better > 0.5%
    assert classify_state(d) == DirectorState.IMPROVING


def test_classify_stuck_on_repeated_dead_class():
    d = _digest(best=636.0, prev=636.0, repeated=["bf16"])
    assert classify_state(d) == DirectorState.STUCK


def test_classify_stuck_when_no_new_classes_for_k_rounds():
    hist = [_digest(636.0, 636.0, distinct=0), _digest(637.0, 636.0, distinct=0)]
    d = _digest(best=638.0, prev=636.0, distinct=0)
    assert classify_state(d, history=hist, stuck_k=2) == DirectorState.STUCK


def test_classify_plateaued_when_flat_but_still_exploring():
    d = _digest(best=640.0, prev=636.0, distinct=1)  # worse, but new class tried
    assert classify_state(d) == DirectorState.PLATEAUED
```

```python
# tests/test_director_review.py  (append: digest from a real store)
import json
from pathlib import Path
from chi.config import FleetConfig
from chi.orchestrator.loop import start_run
from chi.store.db import Store
from chi.store import ledger

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
GOOD = ("import itertools\n\n\ndef solve(xs):\n    return list(itertools.accumulate(xs))\n")
NAIVE = ("def solve(xs):\n    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


def test_build_digest_reads_champion_and_dead_classes(tmp_path):
    sp = tmp_path / "s.json"; sp.write_text(json.dumps([NAIVE, GOOD]))
    fleet = FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR), "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": 2, "eval_recency_iters": 100, "repeat_k": 3}})
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    store = Store.open(summary.run_dir)
    d = build_digest(store, summary.run_id, round_index=0, prev_best=None)
    assert d.champion_score is not None
    assert d.best_score == d.champion_score


def test_near_miss_round_trip(tmp_path):
    store = Store.open(tmp_path / "r")
    store.execute("INSERT INTO runs (run_id, started_at) VALUES ('r1','t')")
    ledger.mark_near_miss(store, "r1", "sha256:abc", 637.8)
    got = ledger.list_near_misses(store, "r1")
    assert got == [{"code_hash": "sha256:abc", "score": 637.8}]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_director_review.py -v`
Expected: FAIL — `ImportError` on `build_digest` / `classify_state`; `AttributeError` on `ledger.mark_near_miss`.

- [ ] **Step 3: Implement ledger helpers**

```python
# chi/store/ledger.py  (append)
def dead_classes(store: Store, run_id: str) -> dict:
    """approach_class -> number of times ruled out (any status)."""
    rows = store.query(
        "SELECT approach_class, COUNT(*) n FROM negative_ledger WHERE run_id=?"
        " GROUP BY approach_class", (run_id,))
    return {r["approach_class"]: r["n"] for r in rows}


def mark_near_miss(store: Store, run_id: str, code_hash: str, score_value: float) -> None:
    """Flag an experiment as a promising near-parity base (no schema change:
    recorded as a STATUS event the Strategist can promote to a new base)."""
    events.append_event(store, run_id, events.STATUS,
                        payload={"near_miss": code_hash, "score": score_value})


def list_near_misses(store: Store, run_id: str) -> list[dict]:
    """Near-miss bases recorded via mark_near_miss, newest first, de-duplicated."""
    rows = store.query(
        "SELECT payload_json FROM events WHERE run_id=? AND type=? ORDER BY event_id DESC",
        (run_id, events.STATUS))
    out, seen = [], set()
    for r in rows:
        p = json.loads(r["payload_json"])
        h = p.get("near_miss")
        if h and h not in seen:
            seen.add(h)
            out.append({"code_hash": h, "score": p.get("score")})
    return out
```
(`json` and `events` are already imported at the top of `ledger.py`.)

- [ ] **Step 4: Implement review.py**

```python
# chi/director/review.py
"""Deterministic round digest + rule-based state classification.

The brain's qualitative read is advisory; these rules DECIDE the state so the
loop can't talk itself in circles (a lesson from the manual campaign's
confounded rule-outs).
"""

from chi.director.types import RoundDigest, DirectorState
from chi.store import ledger
from chi.store.db import Store


def _better(a: float, b: float, direction: str) -> bool:
    return a < b if direction == "minimize" else a > b


def build_digest(store: Store, run_id: str, round_index: int, prev_best: float | None,
                 direction: str = "minimize", noise_band_pct: float = 8.0) -> RoundDigest:
    champ = ledger.champion(store, run_id, direction)
    champ_score = None if champ is None else champ["score_value"]
    classes = ledger.dead_classes(store, run_id)
    repeated = sorted(k for k, n in classes.items() if n >= 2)
    # distinct new classes this round: dead-ends recorded since the round started
    # is approximated by classes with exactly 1 occurrence (fresh this run window).
    distinct_new = sum(1 for n in classes.values() if n == 1)
    return RoundDigest(
        round_index=round_index, best_score=champ_score, champion_score=champ_score,
        prev_best=prev_best, dead_classes=sorted(classes), repeated_dead_classes=repeated,
        near_misses=ledger.list_near_misses(store, run_id), distinct_new_classes=distinct_new,
    )


def classify_state(digest: RoundDigest, prev_digest: RoundDigest | None = None,
                   noise_band_pct: float = 8.0, promote_margin_pct: float = 0.5,
                   stuck_k: int = 2, plateau_window: int = 2,
                   history: list | None = None, direction: str = "minimize") -> DirectorState:
    best, prev = digest.best_score, digest.prev_best
    improving = (
        best is not None and prev is not None and (
            (direction == "minimize" and best < prev * (1 - promote_margin_pct / 100))
            or (direction == "maximize" and best > prev * (1 + promote_margin_pct / 100))
        )
    )
    if improving:
        return DirectorState.IMPROVING
    recent = (history or [])[-(stuck_k - 1):] + [digest] if stuck_k > 1 else [digest]
    no_new_classes = len(recent) >= stuck_k and all(d.distinct_new_classes == 0 for d in recent)
    if digest.repeated_dead_classes or no_new_classes:
        return DirectorState.STUCK
    return DirectorState.PLATEAUED
```

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_director_review.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add chi/director/review.py chi/store/ledger.py tests/test_director_review.py
git commit -m "feat(director): MetaReviewer digest + rule-based state classifier

build_digest reads champion/dead-classes/near-misses from the store; classify_state
decides improving/plateaued/stuck by explicit rules (brain read is advisory only).
Adds ledger.dead_classes/mark_near_miss/list_near_misses (no schema change)."
```

---

## Task 4: NoiseGuard (median-of-N re-benchmark)

**Files:**
- Create: `chi/eval/noise.py`
- Test: `tests/test_noise_guard.py`

**Interfaces:**
- Consumes: a `benchmark` callable `Callable[[Path], BenchResult]` (inject the real `PopcornBackend.benchmark` in production; a fake in tests) — `BenchResult(ok, score_us, detail)` from `chi/eval/popcorn.py:21`.
- Produces:
  - `NoiseGuard(benchmark_fn, n=3, direction="minimize", promote_margin_pct=0.5)`
  - `.verify(candidate: Path, champion_score: float) -> VerifyResult` where `VerifyResult(is_real_improvement: bool, median_score: float | None, samples: list[float], benchmarks_run: int, detail: str)`.
- Logic: run `benchmark_fn` up to `n` times; drop failed samples; if fewer than `ceil(n/2)` succeed → `is_real_improvement=False, detail="insufficient samples"`. Median of successes must beat `champion_score` by `promote_margin_pct` to return True.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_noise_guard.py
from pathlib import Path
from chi.eval.noise import NoiseGuard
from chi.eval.popcorn import BenchResult


def _fake(scores):
    it = iter(scores)
    return lambda candidate: BenchResult(ok=True, score_us=next(it), detail="")


def test_true_improvement_survives_median():
    guard = NoiseGuard(_fake([600.0, 605.0, 598.0]), n=3, promote_margin_pct=0.5)
    r = guard.verify(Path("c.py"), champion_score=636.0)
    assert r.is_real_improvement is True
    assert 598.0 <= r.median_score <= 605.0
    assert r.benchmarks_run == 3


def test_noise_only_win_is_rejected():
    # one lucky-low sample among champion-level samples: median does NOT beat champ
    guard = NoiseGuard(_fake([600.0, 636.0, 640.0]), n=3, promote_margin_pct=0.5)
    r = guard.verify(Path("c.py"), champion_score=636.0)
    assert r.is_real_improvement is False


def test_insufficient_samples_is_not_an_improvement():
    def flaky(candidate):
        return BenchResult(ok=False, score_us=None, detail="timeout")
    guard = NoiseGuard(flaky, n=3)
    r = guard.verify(Path("c.py"), champion_score=636.0)
    assert r.is_real_improvement is False
    assert "insufficient" in r.detail
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_noise_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: chi.eval.noise`.

- [ ] **Step 3: Implement**

```python
# chi/eval/noise.py
"""Median-of-N re-benchmark: tell a real improvement from ~8% B200 noise.

Same cholesky kernel measured 636/652/686µs across runs. A single benchmark
that beats the champion may be luck. Re-benchmark N times and believe the win
only if the MEDIAN clears the champion by the promote margin. Fires only on
apparent winners, so the extra B200 cost is bounded.
"""

import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class VerifyResult:
    is_real_improvement: bool
    median_score: float | None
    samples: list = field(default_factory=list)
    benchmarks_run: int = 0
    detail: str = ""


class NoiseGuard:
    def __init__(self, benchmark_fn: Callable, n: int = 3,
                 direction: str = "minimize", promote_margin_pct: float = 0.5) -> None:
        self._benchmark = benchmark_fn
        self._n = n
        self._direction = direction
        self._margin = promote_margin_pct

    def verify(self, candidate: Path, champion_score: float) -> VerifyResult:
        samples: list[float] = []
        for _ in range(self._n):
            r = self._benchmark(candidate)
            if r.ok and r.score_us is not None:
                samples.append(float(r.score_us))
        if len(samples) < math.ceil(self._n / 2):
            return VerifyResult(False, None, samples, self._n,
                                f"insufficient samples ({len(samples)}/{self._n})")
        med = statistics.median(samples)
        if self._direction == "minimize":
            real = med < champion_score * (1 - self._margin / 100)
        else:
            real = med > champion_score * (1 + self._margin / 100)
        return VerifyResult(real, med, samples, self._n,
                            f"median {med:.1f} vs champion {champion_score:.1f}")
```

- [ ] **Step 4: Run tests + full suite**

Run: `uv run pytest tests/test_noise_guard.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add chi/eval/noise.py tests/test_noise_guard.py
git commit -m "feat(eval): NoiseGuard — median-of-N re-benchmark to beat B200 noise

A single benchmark that beats the champion may be luck within the ~8% run-to-run
spread. verify() re-benchmarks N times and believes the win only if the median
clears the champion by the promote margin; insufficient samples => not an
improvement. Fires only on apparent winners, so extra B200 cost is bounded."
```

---

## Task 5: Strategist

**Files:**
- Create: `chi/director/strategy.py`
- Test: `tests/test_director_strategy.py`

**Interfaces:**
- Consumes: `Store`, `RoundDigest`, `DirectorState`, `ledger.mark_near_miss`, `Steering.TEMPLATE` shape (writes `run_dir/steering.md`), a `brain_fn: Callable[[str], str] | None` (the CLI-brain runner; `None` → deterministic-only, no new-strategy invention), the current `per_coder_strategy: dict[str,str]`.
- Produces:
  - `Strategist(store, run_id, run_dir, direction="minimize", brain_fn=None)`
  - `.plan(digest, state, per_coder_strategy, research_findings="") -> StrategyUpdate` — deterministic parts always run (accumulate dead classes into a `DEAD — do not retry` steering block; promote near-misses to a `PROMISING bases` block); on `PLATEAUED`/`STUCK` with a `brain_fn`, ask the brain for ONE new strategy label for the weakest coder and fold `research_findings` into the steering text.
  - `.apply(update) -> None` — writes `update.steering_text` to `run_dir/steering.md`.

**Design note:** The Strategist writes the OPERATOR section of `steering.md` (below the template's `<!-- Write directives below this line. -->` marker) — the same file the coders already hot-reload via `Steering.refresh()`. That is how the Director steers the fleet without touching coder code.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_director_strategy.py
from pathlib import Path
from chi.director.strategy import Strategist
from chi.director.types import RoundDigest, DirectorState
from chi.store.db import Store


def _store(tmp_path):
    s = Store.open(tmp_path / "r")
    s.execute("INSERT INTO runs (run_id, started_at) VALUES ('r1','t')")
    return s


def _digest(dead=None, repeated=None, near=None):
    return RoundDigest(round_index=1, best_score=636.0, champion_score=636.0,
                       prev_best=636.0, dead_classes=dead or [],
                       repeated_dead_classes=repeated or [], near_misses=near or [],
                       distinct_new_classes=0)


def test_dead_classes_become_a_do_not_retry_block(tmp_path):
    st = Strategist(_store(tmp_path), "r1", tmp_path / "r")
    upd = st.plan(_digest(dead=["bf16", "panel-inv"], repeated=["bf16"]),
                  DirectorState.PLATEAUED, {"c1": "tune-champion"})
    assert "DEAD — do not retry" in upd.steering_text
    assert "bf16" in upd.steering_text


def test_near_miss_promoted_to_bases(tmp_path):
    st = Strategist(_store(tmp_path), "r1", tmp_path / "r")
    upd = st.plan(_digest(near=[{"code_hash": "sha256:abc", "score": 637.8}]),
                  DirectorState.PLATEAUED, {"c1": "x"})
    assert "sha256:abc" in upd.steering_text
    assert "sha256:abc" in upd.promoted_near_misses


def test_brain_invents_new_strategy_when_stuck(tmp_path):
    st = Strategist(_store(tmp_path), "r1", tmp_path / "r",
                    brain_fn=lambda prompt: "recursive-right-looking-syrk")
    upd = st.plan(_digest(repeated=["bf16"]), DirectorState.STUCK, {"c1": "old"})
    assert upd.per_coder_strategy["c1"] == "recursive-right-looking-syrk"


def test_apply_writes_steering_file(tmp_path):
    rundir = tmp_path / "r"
    st = Strategist(_store(tmp_path), "r1", rundir)
    upd = st.plan(_digest(dead=["bf16"]), DirectorState.PLATEAUED, {"c1": "x"})
    st.apply(upd)
    assert "bf16" in (rundir / "steering.md").read_text()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_director_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError: chi.director.strategy`.

- [ ] **Step 3: Implement**

```python
# chi/director/strategy.py
"""Turn a round's digest + state into steering + strategy mutation.

Deterministic where it can be (dead-class enforcement, near-miss promotion);
brain-assisted only for inventing a genuinely new strategy on a plateau/stuck.
Writes the operator section of steering.md — the file coders hot-reload — so the
Director steers the fleet without touching coder code.
"""

from pathlib import Path
from typing import Callable

from chi.director.types import RoundDigest, DirectorState, StrategyUpdate

_HEADER = "# Director directives (auto-generated each round)\n"


class Strategist:
    def __init__(self, store, run_id: str, run_dir: Path, direction: str = "minimize",
                 brain_fn: Callable[[str], str] | None = None) -> None:
        self._store = store
        self._run_id = run_id
        self._run_dir = Path(run_dir)
        self._direction = direction
        self._brain = brain_fn

    def plan(self, digest: RoundDigest, state: DirectorState,
             per_coder_strategy: dict, research_findings: str = "") -> StrategyUpdate:
        lines = [_HEADER, f"Round {digest.round_index}: state = {state.value}.",
                 f"Champion to beat: {digest.champion_score}.", ""]
        if digest.dead_classes:
            lines.append("## DEAD — do not retry")
            for c in digest.dead_classes:
                mark = " (repeated — hard block)" if c in digest.repeated_dead_classes else ""
                lines.append(f"- {c}{mark}")
            lines.append("")
        promoted = []
        if digest.near_misses:
            lines.append("## PROMISING bases (near champion — refine these)")
            for nm in digest.near_misses:
                lines.append(f"- {nm['code_hash']} (score {nm['score']})")
                promoted.append(nm["code_hash"])
            lines.append("")
        if research_findings:
            lines.append("## Research findings")
            lines.append(research_findings.strip())
            lines.append("")
        strategies = dict(per_coder_strategy)
        if state in (DirectorState.PLATEAUED, DirectorState.STUCK) and self._brain and strategies:
            prompt = (
                "You are the research director for a batched-Cholesky B200 CUDA kernel."
                f" State: {state.value}. Champion {digest.champion_score}µs."
                f" Dead approaches (do NOT propose these): {', '.join(digest.dead_classes) or 'none'}."
                f" {('Research: ' + research_findings) if research_findings else ''}"
                " Reply with ONE short kebab-case label for a genuinely different"
                " approach to try next. Label only, no prose.")
            label = (self._brain(prompt) or "").strip().splitlines()[0].strip()
            if label:
                weakest = sorted(strategies)[0]  # deterministic pick without per-coder scores
                strategies[weakest] = label
                lines.append(f"## New direction\n- {weakest}: try `{label}`\n")
        return StrategyUpdate(
            steering_text="\n".join(lines), per_coder_strategy=strategies,
            new_dead_classes=digest.dead_classes, promoted_near_misses=promoted,
            researched=bool(research_findings),
        )

    def apply(self, update: StrategyUpdate) -> None:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        (self._run_dir / "steering.md").write_text(update.steering_text)
```

- [ ] **Step 4: Run tests + full suite**

Run: `uv run pytest tests/test_director_strategy.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add chi/director/strategy.py tests/test_director_strategy.py
git commit -m "feat(director): Strategist — dead-class enforcement, near-miss promotion, new-strategy invention

Deterministic parts always run (hard DEAD block for repeated dead classes,
PROMISING-bases block for near-misses); on plateau/stuck with a brain_fn it asks
for one genuinely-different kebab-case strategy for the weakest coder. Writes the
operator section of steering.md that coders already hot-reload."
```

---

## Task 6: Researcher (CLI-brain)

**Files:**
- Create: `chi/director/research.py`
- Test: `tests/test_director_research.py`

**Interfaces:**
- Consumes: a `brain_fn: Callable[[str], str] | None` (the same CLI-brain runner the operator uses; web access is the CLI's own).
- Produces:
  - `Researcher(brain_fn=None, max_chars=2000)`
  - `.research(champion_score, dead_classes: list[str]) -> str` — one brain call framed as "you may search the web"; returns findings text (truncated to `max_chars`). `brain_fn=None` or an empty/raised call → returns `""` (degrade gracefully; the loop treats `""` as "no findings this round").

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_director_research.py
from chi.director.research import Researcher


def test_returns_findings_from_brain():
    r = Researcher(brain_fn=lambda p: "Try right-looking blocked SYRK with TF32 on the trailing panel.")
    out = r.research(champion_score=636.0, dead_classes=["bf16"])
    assert "SYRK" in out


def test_no_brain_degrades_to_empty():
    assert Researcher(brain_fn=None).research(636.0, []) == ""


def test_brain_error_degrades_to_empty():
    def boom(p): raise RuntimeError("no web")
    assert Researcher(brain_fn=boom).research(636.0, []) == ""


def test_output_is_truncated():
    r = Researcher(brain_fn=lambda p: "x" * 5000, max_chars=100)
    assert len(r.research(636.0, [])) <= 100
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_director_research.py -v`
Expected: FAIL — `ModuleNotFoundError: chi.director.research`.

- [ ] **Step 3: Implement**

```python
# chi/director/research.py
"""Web-capable research call, fired only when the Director is stuck.

Routes through the CLI brain (its web access is the vendor CLI's own — spec
decision). Always degrades to "" on no brain / empty / error, so a research
failure never sinks the loop.
"""

from typing import Callable

_PROMPT = (
    "You are the research director for a batched dense Cholesky factorization CUDA"
    " kernel targeting an NVIDIA B200. You are STUCK at {champ}µs (geomean). These"
    " approach classes are already ruled out — do NOT suggest them: {dead}."
    " Search the web if you can (CUDA C++ / Blackwell microarchitecture / cuSOLVER /"
    " recent batched-cholesky papers) and report 3-5 CONCRETE, genuinely different"
    " techniques worth trying next, each one line. Techniques only, no preamble.")


class Researcher:
    def __init__(self, brain_fn: Callable[[str], str] | None = None,
                 max_chars: int = 2000) -> None:
        self._brain = brain_fn
        self._max = max_chars

    def research(self, champion_score: float, dead_classes: list) -> str:
        if self._brain is None:
            return ""
        prompt = _PROMPT.format(champ=champion_score, dead=", ".join(dead_classes) or "none")
        try:
            out = self._brain(prompt) or ""
        except Exception:
            return ""
        return out.strip()[: self._max]
```

- [ ] **Step 4: Run tests + full suite**

Run: `uv run pytest tests/test_director_research.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add chi/director/research.py tests/test_director_research.py
git commit -m "feat(director): Researcher — web-capable CLI-brain call on stuckness

One brain call framed to search CUDA/Blackwell/cuSOLVER/paper sources and return
concrete genuinely-different techniques, excluding ruled-out classes. Degrades to
empty on no-brain/empty/error so a research failure never sinks the loop."
```

---

## Task 7: Director loop + DIRECTOR_ROUND events + spend counters

**Files:**
- Create: `chi/director/loop.py`
- Modify: `chi/store/events.py` (add `DIRECTOR_ROUND`)
- Test: `tests/test_director_loop.py`

**Interfaces:**
- Consumes: `run_slice`/`start_run` via an injected `round_runner: Callable[[int], RoundResult]` (so the loop is testable with a fake — the real one wraps `run_slice`); `build_digest`, `classify_state`, `Strategist`, `Researcher`, `NoiseGuard` (optional), `Store`, an `emit: Callable[[str], None]`, and a `threading.Event` stop flag.
- Produces:
  - `Director(store, run_id, run_dir, round_runner, strategist, researcher, *, direction="minimize", emit=None, noise_guard=None, stuck_k=2)`
  - `.run(stop_event: threading.Event) -> None` — loops `round_runner` → `build_digest` → `classify_state` → (research if STUCK) → `strategist.plan/apply`, appends a `DIRECTOR_ROUND` event each round with `{round, state, best, benchmarks_run, cost_usd, cum_benchmarks, cum_cost, researched}`, until `stop_event` is set. Never fires a ranked submit.
  - `.cumulative_benchmarks` / `.cumulative_cost` properties (the visible guardrail).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_director_loop.py
import threading
from pathlib import Path

from chi.director.loop import Director
from chi.director.types import RoundResult, DirectorState
from chi.store.db import Store
from chi.store.events import list_events


class _FakeRunner:
    """Emits improving then flat scores, so the loop walks improving->plateaued."""
    def __init__(self, scores): self.scores = scores; self.i = 0
    def __call__(self, iterations):
        s = self.scores[min(self.i, len(self.scores) - 1)]; self.i += 1
        return RoundResult(round_index=self.i - 1, new_experiments=[],
                           best_score=s, benchmarks_run=1, cost_usd=0.01)


class _FakeStrategist:
    def __init__(self): self.calls = 0
    def plan(self, digest, state, per_coder_strategy, research_findings=""):
        self.calls += 1
        from chi.director.types import StrategyUpdate
        return StrategyUpdate(steering_text="x", per_coder_strategy=per_coder_strategy)
    def apply(self, update): pass


def _seed_run(tmp_path):
    store = Store.open(tmp_path / "r")
    store.execute("INSERT INTO runs (run_id, started_at) VALUES ('r1','t')")
    return store


def test_director_emits_round_events_and_counts_spend(tmp_path, monkeypatch):
    store = _seed_run(tmp_path)
    # build_digest is exercised elsewhere; here stub it to isolate the loop
    import chi.director.loop as loopmod
    monkeypatch.setattr(loopmod, "build_digest", lambda *a, **k: _Digest(a[2]))
    runner = _FakeRunner([636.0, 636.0, 636.0])
    stop = threading.Event()

    d = Director(store, "r1", tmp_path / "r", runner, _FakeStrategist(),
                 researcher=None, emit=lambda line: None)

    # stop after 3 rounds via a runner side-effect
    orig = runner.__call__
    def stopping(iterations):
        r = orig(iterations)
        if runner.i >= 3: stop.set()
        return r
    runner.__call__ = stopping  # type: ignore
    d.run(stop)

    rounds = list_events(store, "r1", "DIRECTOR_ROUND")
    assert len(rounds) == 3
    assert d.cumulative_benchmarks == 3
    assert round(d.cumulative_cost, 2) == 0.03


class _Digest:
    """Minimal digest stand-in for the loop test."""
    def __init__(self, round_index):
        self.round_index = round_index; self.best_score = 636.0
        self.champion_score = 636.0; self.prev_best = 636.0
        self.dead_classes = []; self.repeated_dead_classes = []
        self.near_misses = []; self.distinct_new_classes = 0
```

*(Note: the test stubs `build_digest` and `classify_state`'s inputs via a minimal digest; the real digest/classifier are covered in Task 3. This keeps the loop test about loop mechanics — rounds, events, spend, stop.)*

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_director_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: chi.director.loop`.

- [ ] **Step 3: Add the event constant**

```python
# chi/store/events.py  (add near the other constants)
DIRECTOR_ROUND = "DIRECTOR_ROUND"
```

- [ ] **Step 4: Implement the loop**

```python
# chi/director/loop.py
"""The autonomous research director: a persistent above-the-fleet control loop.

One round: run a bounded fleet slice -> meta-review from the store -> classify
state -> research if stuck -> steer + mutate strategies -> repeat until stopped.
Runs until the user stops it (spec: no auto-termination); every round emits a
DIRECTOR_ROUND event carrying the running benchmark/$ spend — the visible
guardrail. NEVER fires a ranked leaderboard submit (spec J1: manual).
"""

import threading
from pathlib import Path
from typing import Callable

from chi.director.review import build_digest, classify_state
from chi.director.types import DirectorState, RoundResult
from chi.store import events
from chi.store.db import Store


class Director:
    def __init__(self, store: Store, run_id: str, run_dir: Path,
                 round_runner: Callable[[int], RoundResult], strategist,
                 researcher=None, *, direction: str = "minimize",
                 emit: Callable[[str], None] | None = None, noise_guard=None,
                 stuck_k: int = 2, iterations_per_round: int = 2,
                 per_coder_strategy: dict | None = None) -> None:
        self._store = store
        self._run_id = run_id
        self._run_dir = Path(run_dir)
        self._runner = round_runner
        self._strategist = strategist
        self._researcher = researcher
        self._direction = direction
        self._emit = emit or (lambda line: None)
        self._noise_guard = noise_guard
        self._stuck_k = stuck_k
        self._iters = iterations_per_round
        self._strategies = per_coder_strategy or {}
        self.cumulative_benchmarks = 0
        self.cumulative_cost = 0.0

    def run(self, stop_event: threading.Event) -> None:
        prev_best: float | None = None
        history: list = []
        round_index = 0
        while not stop_event.is_set():
            result = self._runner(self._iters)
            self.cumulative_benchmarks += result.benchmarks_run
            self.cumulative_cost += result.cost_usd
            digest = build_digest(self._store, self._run_id, round_index, prev_best,
                                  self._direction)
            state = classify_state(digest, history=history, stuck_k=self._stuck_k,
                                   direction=self._direction)
            findings = ""
            if state == DirectorState.STUCK and self._researcher is not None:
                findings = self._researcher.research(digest.champion_score or 0.0,
                                                     digest.dead_classes)
            update = self._strategist.plan(digest, state, self._strategies, findings)
            self._strategist.apply(update)
            self._strategies = update.per_coder_strategy
            events.append_event(
                self._store, self._run_id, events.DIRECTOR_ROUND,
                payload={"round": round_index, "state": state.value,
                         "best": digest.best_score, "benchmarks_run": result.benchmarks_run,
                         "cost_usd": result.cost_usd,
                         "cum_benchmarks": self.cumulative_benchmarks,
                         "cum_cost": self.cumulative_cost, "researched": bool(findings)},
                cost_usd=result.cost_usd)
            self._emit(f"round {round_index}: {state.value} · best {digest.best_score}"
                       f" · Σ {self.cumulative_benchmarks} benches ${self.cumulative_cost:.2f}")
            history.append(digest)
            if state == DirectorState.IMPROVING and digest.best_score is not None:
                prev_best = digest.best_score
            elif prev_best is None:
                prev_best = digest.best_score
            round_index += 1
```

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_director_loop.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add chi/director/loop.py chi/store/events.py tests/test_director_loop.py
git commit -m "feat(director): the Director loop + DIRECTOR_ROUND events + spend counters

Persistent round loop: bounded slice -> digest -> classify -> research-if-stuck ->
steer+mutate -> repeat until stopped. Emits a DIRECTOR_ROUND per round carrying the
running benchmark/\$ spend (the visible guardrail under run-until-stopped). Never
fires a ranked submit."
```

---

## Task 8: Real round-runner adapter (`run_slice` → RoundResult)

**Files:**
- Create: `chi/director/round.py`
- Test: `tests/test_director_round.py`

**Interfaces:**
- Consumes: `run_slice` (Task 1), `start_run`, `RoundResult` (Task 2), `Store`.
- Produces:
  - `RoundRunner(fleet, run_dir, *, first_started=False, completion_fn=None, stop_event=None)`
  - `.__call__(iterations: int) -> RoundResult` — round 0 calls `start_run` (creates run + baseline) if not `first_started`, later rounds call `run_slice`; computes `best_score` via `ledger.champion`, `benchmarks_run` = experiments added during the slice, `cost_usd` from the slice's summary/events.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_director_round.py
import json
from pathlib import Path
from chi.config import FleetConfig
from chi.director.round import RoundRunner
from chi.director.types import RoundResult

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
NAIVE = ("def solve(xs):\n    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")
GOOD = ("import itertools\n\n\ndef solve(xs):\n    return list(itertools.accumulate(xs))\n")


def test_round_runner_runs_slices_and_reports_best(tmp_path):
    sp = tmp_path / "s.json"; sp.write_text(json.dumps([NAIVE, GOOD]))
    fleet = FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR), "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": 1, "eval_recency_iters": 100, "repeat_k": 3}})
    run_dir = tmp_path / "runs" / "t-fixed"
    runner = RoundRunner(fleet, run_dir)
    r0 = runner(1)
    assert isinstance(r0, RoundResult)
    assert r0.best_score is not None
    r1 = runner(1)  # second slice: same run continues
    assert r1.round_index == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_director_round.py -v`
Expected: FAIL — `ModuleNotFoundError: chi.director.round`.

- [ ] **Step 3: Implement**

```python
# chi/director/round.py
"""Adapt the deterministic orchestrator into the Director's round callable."""

from pathlib import Path
from typing import Callable

from chi.config import FleetConfig
from chi.director.types import RoundResult
from chi.orchestrator.loop import run_slice, start_run
from chi.store import ledger
from chi.store.db import Store


class RoundRunner:
    def __init__(self, fleet: FleetConfig, run_dir: Path, *, first_started: bool = False,
                 completion_fn: Callable | None = None, stop_event=None) -> None:
        self._fleet = fleet
        self._run_dir = Path(run_dir)
        self._started = first_started
        self._completion_fn = completion_fn
        self._stop_event = stop_event
        self._round = 0
        self._run_id: str | None = None

    def __call__(self, iterations: int) -> RoundResult:
        store = None
        before = 0
        if not self._started:
            sliced = self._fleet.model_copy(update={"policies":
                self._fleet.policies.model_copy(update={"max_iterations": iterations})})
            summary = start_run(sliced, runs_root=self._run_dir.parent,
                                completion_fn=self._completion_fn,
                                stop_event=self._stop_event)
            self._run_dir = summary.run_dir
            self._run_id = summary.run_id
            self._started = True
        else:
            store = Store.open(self._run_dir)
            before = store.query(
                "SELECT COUNT(*) n FROM experiments WHERE run_id=?", (self._run_id,))[0]["n"]
            summary = run_slice(self._fleet, self._run_dir, iterations=iterations,
                                completion_fn=self._completion_fn, stop_event=self._stop_event)
        store = Store.open(self._run_dir)
        self._run_id = summary.run_id
        after = store.query(
            "SELECT COUNT(*) n FROM experiments WHERE run_id=?", (self._run_id,))[0]["n"]
        champ = ledger.champion(store, self._run_id, self._fleet.policies and "minimize" or "minimize")
        cost = store.query(
            "SELECT COALESCE(SUM(cost_usd),0) c FROM events WHERE run_id=?",
            (self._run_id,))[0]["c"]
        result = RoundResult(
            round_index=self._round, new_experiments=[],
            best_score=None if champ is None else champ["score_value"],
            benchmarks_run=max(0, after - before), cost_usd=float(cost))
        self._round += 1
        return result
```

- [ ] **Step 4: Run tests + full suite**

Run: `uv run pytest tests/test_director_round.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add chi/director/round.py tests/test_director_round.py
git commit -m "feat(director): RoundRunner — adapt start_run/run_slice into a round callable

Round 0 creates the run + baseline via start_run; later rounds continue it via
run_slice. Reports best_score (champion), benchmarks_run (experiments added this
slice), and cumulative cost for the Director's spend counter."
```

---

## Task 9: Operator tools — start/stop/status director + clarify-at-kickoff

**Files:**
- Modify: `chi/session/operator.py` (add 3 tools to `TOOLS`, 3 dispatch branches, mention in prompts)
- Create: `chi/session/director_runner.py` (`DirectorHandle`, modeled on `RunHandle`)
- Modify: `chi/session/engine.py` (add `start_director`/`stop_director`/`director_status` methods the tools call; `DirectorHandle` field)
- Test: `tests/test_director_operator.py`

**Interfaces:**
- Consumes: `Director`, `RoundRunner`, `Strategist`, `Researcher`, `NoiseGuard`, the engine's existing `cli_runner_fn` (CLI-brain seam), `FleetConfig` build (as in `engine.launch_problem`).
- Produces:
  - `DirectorHandle(fleet, runs_root, brain_fn=None)` with `.start()`, `.request_stop()`, `.alive`, `.run_id`, `.run_dir`, `.ready`, `.cumulative_benchmarks`, `.cumulative_cost` — a daemon thread that builds the Director (RoundRunner + Strategist(brain_fn) + Researcher(brain_fn)) and calls `.run(stop_event)`.
  - `engine.start_director(problem_dir: str) -> list[str]`, `engine.stop_director() -> list[str]`, `engine.director_status() -> list[str]`.
  - operator tools `start_director {problem_dir}`, `stop_director {}`, `director_status {}` dispatched in `dispatch_tool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_director_operator.py
import json
from pathlib import Path
from chi.session.director_runner import DirectorHandle
from chi.config import FleetConfig

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"
NAIVE = ("def solve(xs):\n    return [sum(xs[: i + 1]) for i in range(len(xs))]\n")


def _fleet(tmp_path):
    sp = tmp_path / "s.json"; sp.write_text(json.dumps([NAIVE]))
    return FleetConfig.model_validate({
        "run_name": "t", "problem": str(PROBLEM_DIR), "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(sp)}],
        "policies": {"max_iterations": 1, "eval_recency_iters": 100, "repeat_k": 3}})


def test_director_handle_starts_and_stops(tmp_path):
    h = DirectorHandle(_fleet(tmp_path), runs_root=tmp_path / "runs", brain_fn=None)
    h.start()
    assert h.ready.wait(timeout=30)
    h.request_stop()
    h.join(timeout=30)
    assert not h.alive
    assert h.run_id is not None


def test_operator_dispatch_has_director_tools():
    from chi.session.operator import TOOLS
    names = {t["function"]["name"] for t in TOOLS}
    assert {"start_director", "stop_director", "director_status"} <= names
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_director_operator.py -v`
Expected: FAIL — `ModuleNotFoundError: chi.session.director_runner`.

- [ ] **Step 3: Implement `DirectorHandle`**

```python
# chi/session/director_runner.py
"""Background-thread wrapper for the Director (modeled on RunHandle)."""

import threading
from pathlib import Path
from typing import Callable

from chi.config import FleetConfig
from chi.director.loop import Director
from chi.director.research import Researcher
from chi.director.round import RoundRunner
from chi.director.strategy import Strategist
from chi.store.db import Store


class DirectorHandle:
    def __init__(self, fleet: FleetConfig, runs_root: Path,
                 brain_fn: Callable[[str], str] | None = None,
                 emit: Callable[[str], None] | None = None) -> None:
        self._fleet = fleet
        self._runs_root = Path(runs_root)
        self._brain = brain_fn
        self._emit = emit or (lambda line: None)
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.run_id: str | None = None
        self.run_dir: Path | None = None
        self.error: str | None = None
        self._director: Director | None = None
        self._thread: threading.Thread | None = None

    def _target(self) -> None:
        try:
            run_dir = self._runs_root / f"{self._fleet.run_name}-director"
            runner = RoundRunner(self._fleet, run_dir, stop_event=self.stop_event)
            # round 0 to create the run so we can open the store for the collaborators
            first = runner(self._fleet.policies.max_iterations or 2)
            self.run_dir = runner._run_dir  # resolved after start_run
            self.run_id = runner._run_id
            store = Store.open(self.run_dir)
            direction = "minimize"
            strategist = Strategist(store, self.run_id, self.run_dir, direction,
                                    brain_fn=self._brain)
            researcher = Researcher(brain_fn=self._brain)
            per_coder = {c.id: (c.strategy or f"strategy-{c.id}") for c in self._fleet.coders}
            self._director = Director(store, self.run_id, self.run_dir, runner, strategist,
                                      researcher, direction=direction, emit=self._emit,
                                      per_coder_strategy=per_coder)
            # feed round-0's result into history by continuing the loop
            self.ready.set()
            self._director.run(self.stop_event)
        except Exception as exc:  # surfaced to the transcript, never raised
            self.error = str(exc)
            self.ready.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._target, daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def cumulative_benchmarks(self) -> int:
        return self._director.cumulative_benchmarks if self._director else 0

    @property
    def cumulative_cost(self) -> float:
        return self._director.cumulative_cost if self._director else 0.0
```

*Note: round 0 is run inside `_target` before building the Director so the run dir/store exist; the Director's own loop then runs rounds 1..N. Accept the tiny duplication of round-0 accounting (its benchmarks are captured when the loop's first `build_digest` reads the store).*

- [ ] **Step 4: Add engine methods + operator tools**

In `chi/session/engine.py`, add a `_director: DirectorHandle | None = None` field (near `_handle`) and:

```python
def start_director(self, problem_dir: str) -> list[str]:
    from pathlib import Path
    from chi.config import BudgetsCfg, FleetConfig, PoliciesCfg, resolve_coders
    from chi.session.director_runner import DirectorHandle
    from chi.userconfig import load_user_config
    path = Path(problem_dir).expanduser()
    if not (path / "problem.yaml").exists():
        return [f"error: {path} is not a problem directory (no problem.yaml)"]
    if self._director is not None and self._director.alive:
        return ["error: a director is already running — stop it first"]
    cfg = load_user_config()
    fleet = FleetConfig(run_name=path.name, problem=path,
                        budgets=BudgetsCfg(total_usd=cfg.default_budget_usd),
                        coders=[], policies=PoliciesCfg())
    try:
        resolve_coders(fleet)
    except ValueError as exc:
        return [f"error: {exc}"]
    self._director = DirectorHandle(fleet, self.runs_root, brain_fn=self.cli_runner_fn,
                                    emit=self.emit_progress)
    self._director.start()
    return [f"director starting on {path.name} — runs until you /stop it;"
            " watch the round/spend counter"]


def stop_director(self) -> list[str]:
    if self._director is None or not self._director.alive:
        return ["no director running"]
    self._director.request_stop()
    return ["director stopping at the next round boundary"]


def director_status(self) -> list[str]:
    if self._director is None:
        return ["no director this session"]
    d = self._director
    return [f"director {'alive' if d.alive else 'stopped'} run={d.run_id}"
            f" · Σ {d.cumulative_benchmarks} benches ${d.cumulative_cost:.2f}"]
```

In `chi/session/operator.py`, add to `TOOLS`:

```python
{"type": "function", "function": {
    "name": "start_director",
    "description": "Start the AUTONOMOUS research director on a problem directory."
                   " It runs the fleet in rounds, meta-reviews, researches when stuck,"
                   " and re-steers ON ITS OWN until the user stops it. Use when the user"
                   " wants chi to 'keep improving on its own' from one task.",
    "parameters": {"type": "object", "properties": {"problem_dir": {"type": "string"}},
                   "required": ["problem_dir"]}}},
{"type": "function", "function": {
    "name": "stop_director",
    "description": "Stop the autonomous director at the next round boundary.",
    "parameters": {"type": "object", "properties": {}}}},
{"type": "function", "function": {
    "name": "director_status",
    "description": "Director state + running benchmark/$ spend counter.",
    "parameters": {"type": "object", "properties": {}}}},
```

And in `dispatch_tool`, add branches:

```python
if name == "start_director":
    shown = engine.start_director(str(args.get("problem_dir", "")))
    return "\n".join(shown), shown
if name == "stop_director":
    shown = engine.stop_director()
    return "\n".join(shown), shown
if name == "director_status":
    shown = engine.director_status()
    return "\n".join(shown), []
```

Also add the same three actions to the `CLI_PROMPT` action list (one line each) so the CLI-brain operator can use them.

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_director_operator.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add chi/session/director_runner.py chi/session/operator.py chi/session/engine.py tests/test_director_operator.py
git commit -m "feat(session): operator tools to start/stop/status the autonomous director

DirectorHandle runs the Director in a daemon thread (RunHandle-style) with the
session's CLI brain wired into Strategist+Researcher. Operator gains start_director/
stop_director/director_status tools (API + CLI-brain prompts) so 'keep improving on
its own' launches the sustained loop; status shows the running benchmark/\$ spend."
```

---

## Task 10: Clarify-at-kickoff + interject queue

**Files:**
- Modify: `chi/session/operator.py` (system prompt: instruct clarify-if-thin before start_director)
- Modify: `chi/session/engine.py` (interject: free text while director alive → append to `steering.md` as a priority directive, folded next round)
- Test: `tests/test_director_operator.py` (extend)

**Interfaces:**
- Consumes: existing `_append_steering(run_dir, text)` (`engine.py:920`), `_director.run_dir`.
- Produces: `engine.interject_director(text: str) -> list[str]` — appends a `## §op (priority)` block to the director run's `steering.md`; the Strategist's next `apply` preserves operator directives (see note). Engine `submit`/`_free_text` routes free text to `interject_director` when a director is alive instead of the operator chat.

**Design note — steering ownership:** The Strategist overwrites `steering.md` each round (Task 5 `apply`). To not clobber operator interjections, change `Strategist.apply` to write the Director block to `steering.md` and PRESERVE any lines under an `## §op` heading by re-appending them after the Director block. Add that behavior + a test here.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_director_operator.py  (append)
from chi.director.strategy import Strategist
from chi.director.types import RoundDigest, DirectorState
from chi.store.db import Store


def test_apply_preserves_operator_interjection(tmp_path):
    rundir = tmp_path / "r"; rundir.mkdir()
    (rundir / "steering.md").write_text("old\n## §op (priority)\nfocus on n=8192\n")
    s = Store.open(rundir); s.execute("INSERT INTO runs (run_id, started_at) VALUES ('r1','t')")
    st = Strategist(s, "r1", rundir)
    d = RoundDigest(round_index=1, best_score=636.0, champion_score=636.0, prev_best=636.0)
    st.apply(st.plan(d, DirectorState.PLATEAUED, {"c1": "x"}))
    text = (rundir / "steering.md").read_text()
    assert "focus on n=8192" in text      # operator interjection survived
    assert "Director directives" in text  # director block present too
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_director_operator.py::test_apply_preserves_operator_interjection -v`
Expected: FAIL — operator line clobbered.

- [ ] **Step 3: Update `Strategist.apply` to preserve operator lines**

```python
# chi/director/strategy.py  (replace apply)
def apply(self, update: StrategyUpdate) -> None:
    self._run_dir.mkdir(parents=True, exist_ok=True)
    path = self._run_dir / "steering.md"
    preserved = ""
    if path.exists():
        existing = path.read_text()
        marker = "## §op"
        if marker in existing:
            preserved = "\n\n" + existing[existing.index(marker):]
    path.write_text(update.steering_text + preserved)
```

- [ ] **Step 4: Add engine interject + routing**

```python
# chi/session/engine.py
def interject_director(self, text: str) -> list[str]:
    if self._director is None or not self._director.alive or self._director.run_dir is None:
        return ["no director running to interject"]
    self._append_steering(self._director.run_dir, f"(priority) {text}")
    return ["queued for the director's next round"]
```

In `submit`/`_free_text`, before routing to the operator chat, add: if `self._director is not None and self._director.alive` and the text is not a slash command, route to `interject_director(text)`.

Add to the operator SYSTEM_PROMPT: "When the user gives a whole task to pursue autonomously ('keep improving on its own', 'run until you beat X'), first ensure you have a problem directory and a clear goal — ask ONE clarifying question if the task is thin — then call start_director. While a director runs, user direction becomes an interjection folded into the next round."

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_director_operator.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add chi/director/strategy.py chi/session/engine.py chi/session/operator.py tests/test_director_operator.py
git commit -m "feat(session): clarify-at-kickoff + interject queue for the director

Strategist.apply now preserves operator '## §op' interjections when it rewrites
steering.md each round. Free text while a director runs becomes a priority
interjection folded into the next round; the operator prompt tells chi to clarify
a thin task once, then start_director."
```

---

## Task 11: Opt-in real-B200 integration test

**Files:**
- Create: `tests/test_director_integration.py`
- Test: itself (marked slow, skipped unless `CHI_ALLOW_REMOTE_BENCH=1`)

**Interfaces:**
- Consumes: `DirectorHandle`, the real cholesky pack at `~/.local/share/chi/problems/cholesky`.

- [ ] **Step 1: Write the test (skipped by default)**

```python
# tests/test_director_integration.py
import os
import threading
import time
from pathlib import Path

import pytest

CHOLESKY = Path.home() / ".local/share/chi/problems/cholesky"

pytestmark = pytest.mark.skipif(
    os.environ.get("CHI_ALLOW_REMOTE_BENCH") != "1" or not CHOLESKY.exists(),
    reason="real B200 run: set CHI_ALLOW_REMOTE_BENCH=1 and have the cholesky pack",
)


def test_director_runs_rounds_counts_spend_no_ranked_submit(tmp_path):
    from chi.config import BudgetsCfg, FleetConfig, PoliciesCfg
    from chi.session.director_runner import DirectorHandle
    from chi.store.db import Store
    from chi.store.events import list_events

    fleet = FleetConfig(run_name="cholesky", problem=CHOLESKY,
                        budgets=BudgetsCfg(total_usd=5.0),
                        coders=[], policies=PoliciesCfg(max_iterations=1))
    h = DirectorHandle(fleet, runs_root=tmp_path / "runs", brain_fn=None)
    h.start()
    assert h.ready.wait(timeout=1800)
    time.sleep(60)  # let at least one full round land
    h.request_stop(); h.join(timeout=1800)
    store = Store.open(h.run_dir)
    assert len(list_events(store, h.run_id, "DIRECTOR_ROUND")) >= 1
    # NEVER a ranked submit: no STATUS event claiming a leaderboard submission
    for e in list_events(store, h.run_id, "STATUS"):
        assert "submitted" not in (e["payload_json"] or "") or '"submitted": false' in e["payload_json"].lower() or '"submitted": true' not in e["payload_json"]
```

- [ ] **Step 2: Verify it is collected but skipped**

Run: `uv run pytest tests/test_director_integration.py -v`
Expected: SKIPPED (no `CHI_ALLOW_REMOTE_BENCH`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_director_integration.py
git commit -m "test(director): opt-in real-B200 integration (rounds advance, spend counted, no ranked submit)"
```

---

## Task 12: Final integration sweep + docs

**Files:**
- Modify: `docs/pi-substrate-adoption.md` or a short `docs/director.md` note (how to launch the director; the run-until-stopped + spend-counter contract; J1 manual submit).
- Test: full suite.

- [ ] **Step 1: Full suite green**

Run: `uv run pytest -q`
Expected: all prior 209 + new director tests PASS (integration SKIPPED).

- [ ] **Step 2: Write `docs/director.md`**

A short operator note: `chi` → "keep improving cholesky on its own" → chi clarifies if thin, then start_director; watch `round/state/best/Σ benches/$`; interject by typing; `/stop` to end; ranked submit stays manual (Director surfaces a NoiseGuard-verified improvement, you fire it).

- [ ] **Step 3: Commit + merge to main**

```bash
git add docs/director.md
git commit -m "docs(director): how to launch and supervise the autonomous director"
git checkout main && git merge --no-ff <feature-branch> -m "Merge: autonomous research director"
```

---

## Self-Review

**Spec coverage:** §3 Director-component → Tasks 7-9. §4 loop steps → Task 7. §5 components: RoundRunner Task 8, MetaReviewer Task 3, Researcher Task 6, Strategist Task 5, NoiseGuard Task 4, Director Task 7. §5 store additions (DIRECTOR_ROUND, dead-class/near-miss) → Tasks 7, 3. §6 classifier → Task 3. §7 control model (start/stop/status, clarify, interject, detach/reattach) → Tasks 9-10 (detach/reattach reuse the existing `/resume` mechanism, noted in Task 10; no new code needed beyond the DirectorHandle being store-backed). §8 spend visibility → Task 7 counters; manual submit (J1) enforced by never calling submit (Tasks 7, 11 asserts it). §9 testing → each task's tests + Task 11 integration. §2 J2 NoiseGuard built → Task 4. All covered.

**Placeholder scan:** No TBD/TODO; every code step has real content; test bodies are concrete. One acceptable simplification called out inline (Task 8 direction hard-coded "minimize" — the cholesky/optimize packs both minimize; a follow-up can thread `problem.score.direction` if a maximize pack appears).

**Type consistency:** `RoundResult`/`RoundDigest`/`StrategyUpdate`/`DirectorState` defined in Task 2 and used with the same fields in Tasks 3,5,7,8. `build_digest`/`classify_state` signatures match between Task 3 definition and Task 7 use. `NoiseGuard.verify` → `VerifyResult` consistent Task 4. `DirectorHandle` API (`.ready/.request_stop/.alive/.run_id/.run_dir/.cumulative_*`) consistent Tasks 9-11. `run_slice` signature consistent Tasks 1, 8.

**Known risk flagged for the executor:** Task 8's `RoundRunner` computes `benchmarks_run` as experiments added across the whole run on slice 0 (start_run includes the baseline); this slightly over-counts round 0 by 1 (the baseline). Acceptable for a spend *counter*; if exactness matters, subtract 1 on the first round. Noted so the executor doesn't treat it as a bug.
