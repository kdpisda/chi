# chi (χ) content bank

Angle ids are referenced from posts.jsonl. Add angles by hand at the bottom.

- **ledger** — dead ends are first-class data. Ruled-out approach classes get
  hard-blocked so the fleet stops re-exploring the same precision trick for the
  fifth time.
- **watchdog** — a stalled agent is detected and killed by code, not by asking a
  model whether it feels stuck. Zero LLM cost.
- **classify** — the round is classified improving/plateaued/stuck by explicit
  rules. The LLM's read is advisory. The rule decides, so the loop can't talk
  itself in circles.
- **blackboard** — agents never chat directly. Everything lands in a store keyed
  by code hash. Dedup for free, any agent reconstructible from scratch.
- **noiseguard** — apparent wins get re-benchmarked before they count. Most
  "improvements" in agent loops are measurement noise.
- **sandbox** — leaderboard credentials are never mounted. The agents physically
  cannot submit. Ranked submits stay manual.
- **heterogeneous** — claude + codex + grok in one fleet, because different models
  explore different regions of the solution space.
- **postmortem** — every mechanism exists because a manually-run fleet failed
  without it: re-explored dead ends, wasted authoritative evals, silent stalls,
  no way to steer.
- **numbers** — a real run: 812µs → 636µs over four rounds, 55 benches, $1.71
  total. Concrete cost and delta.
- **naming** — it's `chi`, pronounced kai. Not the Go HTTP router. Not the hair
  straightener.

## Added by hand

- **wiring** — a mechanism in the repo is not a mechanism on the path you ship.
  The NoiseGuard only runs under the director; `chi run` promotes the best
  recorded score with no re-benchmark.

- **proxymetric** — the score is a proxy, and the proxy has its own bias. The
  noisy_bench pack prices executed Python line events, so it ranks an O(n)
  rewrite below the O(n^2) baseline and moves 300 points when two statements
  are joined onto one line. A fleet optimizes the metric, not the goal.

- **holdout** — a benchmark the fleet optimizes against becomes the selection
  pressure, so candidates overfit its exact input. chi re-scores the champion on
  a held-out workload agents never see and flags overfit/generalizes (PR #2).

- **substrate** — installed is not working. `shutil.which` finds a vendor CLI,
  not the account that rejects every model. A dogfood run was wasted on that
  mid-run; `chi providers --probe` now sends each installed CLI one prompt first.

- **correctness** — wrong code gets no score. Correctness seeds run before the
  benchmark and stop at the first failure, so a fast wrong candidate is logged
  correct=0 with a null score and can never become champion.

- **budget** — a cap only binds what charges it. In `chi run`, only LiteLLM-loop
  calls go through `BudgetTracker`; a json_stream CLI coder's reported cost is
  logged on the event and never recorded, so a $0.50 cap let a fake coder log
  $2.00 and the summary reported $0.

- **clockgaming** — a candidate runs in the benchmark's process, so it can lie
  about time. Dogfood crowned a coder that froze `perf_counter` (0.0ms, all
  correctness seeds passed). The fix rejects scores <= 0 or non-finite
  (`179aa90`), but a fake clock ticking 1us per read still scores the O(n^2)
  baseline at 0.001ms and is accepted. Only isolation of the timer fixes it.

- **championexport** — the file on disk is not the winner. Coders overwrite and
  revert `candidate.py`, so exporting the live file could ship a slower kernel
  under the champion's score. chi archives every correct, scored candidate by
  code hash at eval time and exports from that archive, re-verifying the hash
  (`80a0725`).

- **steering** — redirect a running fleet without restarting it. `chi steer`
  appends a `§op` block to the run's steering file; every coder re-reads it at
  the top of each iteration and a hash change logs STEER_UPDATE. It cannot
  reach an iteration in flight: up to `iteration_timeout_seconds` (600) late.
- **costceiling** (2026-10-06) — the director's cost ceiling double-counts: RoundRunner returns the run's cumulative event-log spend, the Director adds it to its own total and logs that total back into the same events table. A $25 ceiling stops at $2.40 real spend in the $0.40/iteration repro. Distinct from `budget` (CLI-coder cost never charged).
- **samecode** (2026-10-07) — what counts as "the same candidate". chi's code hash normalizes CRLF, trailing whitespace and trailing blank lines, so a reformatted copy hits the experiments cache and is never re-benchmarked; a comment-only edit is a new hash and pays a full eval. Distinct from `blackboard` (same hash across runs).
- **staleresult** (2026-10-08) — a failed measurement is cached like a real one. A benchmark timeout records the candidate as correct with score null, keyed by code hash; resubmitting identical code is a cache hit and is never re-timed, so a one-off stall (noisy host, flaky remote eval) costs that candidate the whole run. Live repro: stall-once candidate, 5s timeout, champion stayed the 18ms baseline while the candidate times 0.07ms by hand. Distinct from `samecode` (what the hash normalizes).
- **firstrun** (2026-10-09) — installed is not runnable. After `uv tool install getchi`, the documented `chi run examples/offline.yaml` fails with FileNotFoundError outside a repo checkout: the wheel force-includes `examples/` and `problems/optimize_function`, but the fleet path and the paths inside it resolve against cwd. Copying both folders out of site-packages makes the $0 scripted demo run. README comments "run from a checkout"; the getting-started docs don't. Distinct from `substrate` (vendor CLI installed but unusable).
- **sandboxtiers** (2026-10-10) — chi's opt-in Docker sandbox has two tiers. `docker`: `--network none`, only the workdir mounted, no host home. `docker-cli`: forced bridge network plus read-only mounts of whichever of `~/.claude`, `~/.codex`, `~/.grok` exist, because CLI coders need their vendor API. Neither mounts popcorn/leaderboard auth, so gated submission holds; docker-cli still gives a prompt-injected agent egress and a vendor credential (SECURITY.md "Sandbox tiers"). Verified by printing both argvs from `build_docker_command`. Distinct from `sandbox` (the PATH shim).
- **winthenflat** (2026-10-12) — a win buys no patience. `classify_state`'s no-new-classes rule looks at the last `stuck_k` (2) rounds and counts only approaches ruled out exactly once, ignoring that one of those rounds improved. A win (636 to 600) followed by one flat round where no agent logged a dead end returns `stuck`, which triggers the research call; with one class logged per round the same history is `plateaued`. Verified live on synthetic digests. Distinct from `classify` (the perma-plateau rule for two flat rounds that keep trying new ideas).
- **submitbeforegate** (2026-10-13) — auto-submit fires before the holdout gate. `AutoSubmitter.consider` runs per iteration inside `chi/orchestrator/loop.py` and checks only correctness seeds plus the margin; the holdout gate scores only the final champion after the loop. Live repro: overfit_demo copy with `auto_submit: true` and a fake registered backend, scripted overfit candidate: submitted at 0.06ms (event 10), holdout verdict overfit, -1.2% held out, 0.8s later (event 12). Distinct from `holdout` (the verdict itself).
- **lease** (2026-10-14) — a lease nobody enforces is a lock. `claim_task` writes `lease_expires_at` and `chi run` renews it every iteration, but `expire_stale_leases` (return expired claimed/running tasks to pending, bump attempts) has no caller outside `tests/test_store_tasks.py`. Live repro via the CLI verbs: task claimed by agent a with `--lease-seconds 1`; a minute later `chi task claim --agent b` printed NONE, exit 1; row still claimed, owner a, attempts 0. In `chi run` each coder's task is released on kill/stop and set verified on finish, so this bites agents driving `chi task claim` themselves or a coder thread that dies abnormally. Distinct from `watchdog` (stall detection inside `chi run`).
- **evidencecheck** (2026-10-15) — "evidence is mandatory" is a truthiness test. `chi deadend` rejects only when `json.loads(--evidence-json)` is falsy (`chi/cli.py:308-311`): `''`, `{}`, `[]`, `""`, `0` are rejected; `"trust me"` (a JSON string of prose) and `true` are accepted and stored as active dead ends. Also, `chi challenge` against a nonexistent `--neg-id` succeeds (exit 0, challenge row written, UPDATE hits 0 rows). Verified live on an offline-demo run dir. Distinct from `ledger` (why dead ends need evidence at all).
- **reverthash** (2026-10-16) — a loop detector must hash what was evaluated, not what is on disk. Commit 8c98306: the watchdog hashed the workdir candidate file each iteration, but coders revert it to the champion after a losing benchmark, so a productive agent looked unchanged and was killed (live: grok-B, 7 distinct kernels). Replayed: with `ledger.latest_experiment` stubbed out (pre-fix fallback), the scripted regression run logged WATCHDOG_KILL "candidate unchanged 6 consecutive iterations" after 6 distinct evaluated hashes; current code passes. Distinct from `watchdog` (stall detection).
- **deadeval** (2026-10-17) — the dead-eval halt only fires if nothing ever scored. Commit 5f61793 added it: `chi/director/loop.py` halts after `dead_eval_rounds` (3) consecutive rounds where `digest.best_score is None`, but `build_digest` sets `best_score` from `ledger.champion()`, the run's best ever. Live replay with the real `Director` + `build_digest` and a stub round runner recording a correct, score-null candidate each round: empty run halted after 3 rounds; a run with one scored baseline (18.0) ran 10 rounds (stopped by the harness), every round after the first labeled stuck, `halted_reason` None. So a benchmark backend that dies mid-run (the cholesky case that motivated the fix) never trips it. Distinct from `staleresult` (cache keeps a null score).
- **sliceseed** (2026-10-18) — a kill threshold only binds if its counter survives. Commit 44b4208: under the director the fleet runs 2-iteration slices (`iterations_per_round=2`), and the watchdog was built fresh per slice, so repeat-hash (2 x `repeat_k` = 6) and eval-recency (10) thresholds never accumulated. Live replay: scripted coder returning the same candidate, start_run + 9 slices of 2 with `_seed_watchdog` stubbed to a fresh `Watchdog`: 20 identical iterations, 0 WATCHDOG_KILL; current code (seeded from trailing ITERATION_COMPLETE events) kills after iteration 6. By design the repeat-hash rule stays iteration-driven, so a killed coder still runs one iteration per later slice and is re-killed. Distinct from `watchdog` and `reverthash`.
- **shimoverblock** (2026-10-19) — the first popcorn-cli shim (10f7362) refused every `submit`, but popcorn runs benchmark/test/profile through `submit --mode ...` too, so it blocked the agents' feedback loop; the live cholesky dogfood produced fabricated scores (commit 7da84c7). Replayed old vs current shim: old refuses 5/5 modes, current refuses only `--mode leaderboard` and bare `submit`. Distinct from `sandbox` (the shim exists).
- **validateschema** (2026-10-20) — `chi validate` checks types, not meaning. `load_problem` pydantic-validates problem.yaml and nothing else: seeds [], repeats 0, tolerance -1, a misspelled `timout_seconds` and deleted check/bench/candidate scripts all print `OK problem ... (0 seeds)`, exit 0; a fleet pointing at a nonexistent problem dir also passes. Zero seeds matters: the evaluator's correctness loop never runs, so every candidate counts as correct (`chi/eval/runner.py:88-108`). Distinct from `correctness` (the gate itself, with seeds).
