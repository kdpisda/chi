# The Autonomous Research Director

chi's director turns one human task into sustained auto-research: you give a goal
once, and chi runs the fleet in rounds, meta-reviews the results, researches when
it's stuck, re-steers the agents, and repeats — until you stop it. No per-round
human steering.

## Launch it

In a `chi` session, hand chi a task to pursue on its own:

```
› keep improving the cholesky kernel on B200 on its own until I stop you
```

chi's operator recognises this as an autonomous task and calls `start_director`
(it asks one clarifying question only if the task is too thin to act on — e.g. it
can't find a problem directory or a goal). You can also drive it directly; the
operator exposes `start_director` / `stop_director` / `director_status`.

## What a round does

1. **Run** — the fleet runs a bounded slice (a few iterations), then hands control back.
2. **Review** — a deterministic digest is built from the store (champion, dead-end
   classes, near-parity candidates, per-coder progress).
3. **Classify** — explicit rules decide `improving | plateaued | stuck` (the brain's
   read is advisory; the rule decides, so the loop can't talk itself in circles).
4. **Research** — only when *stuck*: one web-capable brain call (CUDA / Blackwell /
   cuSOLVER / papers) returns concrete, genuinely different techniques.
5. **Steer** — the Strategist rewrites `steering.md`: a hard "do not retry" block for
   repeated dead classes, a "promising bases" block for near-misses, researched ideas,
   and a new strategy for the weakest coder on a plateau. Coders hot-reload it.

## Supervising it

- **Watch** — every round emits a line: `round N: <state> · best <µs> · Σ <benches> $<cost>`.
  Because the director runs until you stop it (no auto budget cap), that running
  benchmark/$ counter is your guardrail.
- **Interject** — just type. Plain text while the director runs is folded into the
  next round as a priority directive; it doesn't interrupt the in-flight slice.
- **Detach / reattach** — Ctrl-D detaches (the loop keeps running); reattach later
  with `/resume`. The loop is store-backed, so a reattached session replays its rounds.
- **Stop** — `/stop` (or "stop"). The director halts at the next round boundary.

## Leaderboard submission stays manual

Even under full autonomy the director **never** fires a ranked leaderboard submit.
When a candidate looks like it beats the champion, the NoiseGuard re-benchmarks it
N times and only a median that clears the champion by the promote margin counts as
real — the director surfaces that verified improvement and you fire the ranked
submission yourself. This is deliberate: a ranked submit changes your public rank
irreversibly, and a single benchmark can't be trusted inside the ~8% B200 noise.
