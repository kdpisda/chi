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
