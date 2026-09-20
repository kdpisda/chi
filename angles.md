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
