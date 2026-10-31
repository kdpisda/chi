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
- **popcornparse** (2026-10-21) — the leaderboard benchmark path scores the wrong number. `PopcornBackend.benchmark` (`chi/eval/popcorn.py`) parses with `_parse_score`: JSON keys first (popcorn's `--mode benchmark` leaves `score` null, so that fails), then a regex for the first `<n> us/µs` in the text. On the real 15-shape codex_v652 output kept in `tests/test_popcorn_score.py` it returns 0.7, the first shape's `± 0.70 µs` error bar; the ranked score was 637 µs and `parse_geomean_us` (commit 4ccf6e6) gets 636.3 but has no non-test caller. The director's NoiseGuard for leaderboard problems re-benchmarks through this path. Distinct from `shimoverblock` (the shim) and `noiseguard` (the guard's idea).
- **barepython** (2026-10-22) — the shipped example problem's entrypoints used a bare `python`, absent under `uv tool install` and on a Mac with only `python3`; fixed in 23dac9e with a `{python}` placeholder resolved to `sys.executable` (`python3` inside a sandbox). Replayed the old spec on current code: `chi run` crashed on the baseline eval with FileNotFoundError (`_run` uses shlex + no shell, so it raises instead of exiting 127), exit 1, 0 experiments, run row left `status running`, `ended_at` null; `chi validate` on the same spec prints OK. A user problem written with bare `python` still fails this way today. Distinct from `firstrun` (missing examples in the wheel).
- **operatorguards** (2026-10-23, drafted, not yet posted) — the TUI operator's two guards from commit 0107797. `fetch_url` resolves the host with getaddrinfo and refuses private/loopback/link-local/reserved/multicast addresses (169.254.169.254, 127.1, 2130706433, [::ffff:127.0.0.1] all refused live), and never follows redirects. `explore_path` refuses secrets by file name only: `credentials` refused, but id_ecdsa, .npmrc, .pypirc, .env.local, service-account.json are read and returned to the model. Distinct from `sandbox`/`sandboxtiers` (coder containers, not the operator).

- brainprompts: until commit 22308c9 the director's Researcher/Strategist prompts were hardcoded for a CUDA Cholesky kernel on a B200 (cuSOLVER, µs units) regardless of problem; now templated from the problem spec. Used 2026-10-25.
- **heartbeat** — agents heartbeat from a background thread, but nothing
  outside tests reads `last_heartbeat_at`. When the thread died on a
  cross-thread sqlite error (fixed b1e234e), the run carried on unaware.
- **autosubmitseed** (2026-10-27) — the auto-submit regression guard has no floor unless you give it one. `AutoSubmitter._improves` returns True when there is no last submission, and `chi run` seeds it with `problem.current_best` (default None), not the baseline score it already measured. Fake-backend replay, margin 0.5%: unseeded, 999 / 990 / 18 all submitted; seeded at 18.27, 999 held. Distinct from `submitbeforegate` (timing vs the holdout gate).
- **maximizesign** (2026-10-28) — the clock-cheat guard (179aa90) rejects any benchmark score <= 0 or non-finite regardless of `score.direction`. On a `maximize` problem with a signed metric (percent faster than reference) every slower candidate is correct but unscored, and reference-equal code scored in only 3 of 8 evals by noise. Distinct from `clockgaming` and `staleresult`.
- **exitgate** (2026-10-29) — the correctness gate trusts the checker's exit code, and the shipped checker imports the candidate in-process. A `solve()` that calls `os._exit(0)` when argv has `--seed` and otherwise returns its input untouched passed 3/3 seeds and scored 0.0067 ms vs the 17.2 ms baseline. Distinct from clockgaming (frozen perf_counter).
- **fastspin** (2026-10-30) — a control loop with no idle path turns an empty round into a brain call. Commit 10fa142: an end-to-end smoke on a fast/cached problem spun the director 26+ rounds in 6s, calling the strategist (and the researcher when stuck) every round with nothing new. Replayed the pre-fix `Director.run` against current modules with an instant stub strategist/researcher and slices that benchmark nothing (flat digest at 636): old loop 76,513 rounds in 6s, strategist 76,513 calls, researcher 76,512; current loop 2 rounds, strategist 1, researcher 0. It now replans only when benchmarks ran or (best score, dead classes) changed, and waits `idle_sleep_seconds` (5s) after a zero-benchmark round. Distinct from `deadeval` (halting on no scored champion).
- **targetstop** (2026-10-31) — the director's target-score self-stop (f13ec3c) checks `digest.best_score`, the raw ledger champion, not the NoiseGuard verdict; only a holdout verdict can veto it. Live replay: target 500, baseline 636, one lucky 480 row, guard samples 640/636/650 -> round demoted to plateaued, director still halts "target reached: 480.0 meets 500.0". Distinct from `noiseguard` (the guard idea).
