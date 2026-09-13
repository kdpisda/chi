# The holdout gate: did the win actually transfer?

Every autoresearch loop optimises against one frozen benchmark. That benchmark
is also the loop's *selection pressure*, so over enough iterations candidates
drift toward its particulars — its input size, its RNG seed, its shape — rather
than toward being faster. The result is a number that moved and a program that
didn't improve.

This is not a hypothetical. It is the single most common criticism of the
pattern:

- The most-cited autoresearch result to date — a 53% parse/render speedup on
  Shopify's Liquid engine, from ~120 automated experiments — shipped with its
  own author's caveat: *"This is probably somewhat overfit."* The PR was still
  unmerged months later.
- A widely shared test-suite speedup measured 163s → ~100s locally and
  14min → 13min in CI. The loop optimised the local measurement, not the work.
- Karpathy's own repo keeps a fixed `prepare.py` the agent may not edit,
  precisely so the evaluation can't be gamed — and a documented case exists of
  an agent satisfying a probe hook by calling the network once, discarding the
  result, and continuing with its own search engine.

Surveys of the ecosystem land on the same conclusion: the gap isn't *generating*
candidate improvements — every harness does that — it's **catching weak ones
before they ship**.

chi's correctness gate was already held out: candidates never see reference
outputs. Its *score* was not. The holdout gate closes that.

## What it does

A problem may declare a second scoring command over a **different workload**.
Its files never enter an agent workdir. When a champion is crowned, chi
re-scores it on that held-out workload and compares the gain it *claimed* on the
optimised benchmark with the gain it actually *realised*:

    generalization = holdout gain % / benchmark gain %

| Verdict | Meaning |
|---|---|
| `generalizes` | the held-out workload realised enough of the claim |
| `overfit` | a real benchmark gain that did **not** transfer |
| `regressed` | the held-out workload got materially **worse** |
| `unavailable` | the holdout couldn't be measured (never blocks — see below) |

## Declaring one

```yaml
# problems/<name>/problem.yaml
holdout:
  benchmark: "{python} holdout_bench.py {candidate}"
  files: [holdout_bench.py]     # chi's: excluded from every agent workdir
  repeats: 3                    # median of N, like the NoiseGuard
  min_generalization: 0.5       # realise at least half the claimed gain
  max_regression_pct: 5.0       # ...and don't get materially slower
```

`problems/overfit_demo` ships one, and `examples/holdout.yaml` runs it offline
with no key. `bench.py` times a single fixed 4000-element input;
`holdout_bench.py` scores three other sizes under a different seed. A genuine
algorithmic win shows up in both:

```console
$ chi run examples/holdout.yaml
overfit  champion 0.081 ms (baseline 50.3) -> overfit: claims +99.8% on the
         benchmark but realises only -1.2% held out
honest   champion 0.079 ms (baseline 51.3) -> generalizes: claims +99.8%,
         realises +99.9% held out (100% of the claim)
```

Two candidates, near-identical benchmark scores, opposite verdicts.

**The shared `problems/optimize_function` deliberately has no holdout.** A
holdout costs a baseline measurement on every run of that problem, and a pack
most runs use shouldn't be taxed for a gate those runs never reach. Declare one
where the question is worth asking.

**A good holdout differs in the dimension a candidate could exploit.** If the
benchmark is one input size, vary the size. If it is one seed, vary the seed. If
it is one template, use a different template. A holdout that merely re-runs the
same workload measures noise, not generalization — that job belongs to the
NoiseGuard.

**Set `max_regression_pct` above the holdout's own run-to-run noise.** A margin
tighter than the measurement spread turns a flat held-out result — the signature
of an overfit win — into a spurious `regressed` verdict. `overfit_demo` uses
5.0 because its O(n²) baseline wobbles ~2.5%.

## How the workload stays held out

`holdout.files` are copied into `runs/<run_id>/holdout/`, chi's own directory,
and are excluded from `runs/<run_id>/workdir*` — every agent worktree. An agent
with a shell cannot read the held-out workload, run it, or tune to it, because
it does not have it. To evaluate, the candidate travels to the evaluator, never
the other way round.

The `holdout:` block in `problem.yaml` itself stays visible. That is deliberate:
it tells an agent a holdout exists without telling it what the workload is.

## When it runs, and what it costs

The gate is bought where it is worth the most and spent the least:

- **Once at baseline**, on the untouched candidate — every later verdict is
  relative to this number, so it is taken before any agent has edited anything.
- **At the end of a run**, on the exported champion (`RunSummary.holdout`).
- **Under the director**, only on a win that already survived the NoiseGuard.
  A noise-verified win is real *on the benchmark*; the holdout answers the
  second question, and the expensive answer is only worth buying for a win that
  already passed the cheap one. Holdout samples count toward the round's visible
  benchmark tally.

## What a verdict does

Under the director, an `overfit` or `regressed` champion **cannot satisfy a
target score**. Telling chi "get it under 500µs then stop" and having it stop on
a champion that only reaches 500µs on the benchmark it was tuned against is
exactly how an overfit result ships; the director keeps researching instead.

`chi status` and `chi champion` print the verdict, and `chi champion --export`
warns loudly on a bad one — and still exports. The verdict is evidence for your
decision, not a veto over it. That mirrors the rule for ranked submissions: chi
surfaces the verified result, you fire the irreversible action.

An `unavailable` verdict never blocks anything. If chi's own holdout run breaks,
that is chi's failure, and it does not get to invent a finding out of it.

## Reading the record

Every measurement is an event:

```console
$ chi status runs/<run_id>
{"holdout": {"phase": "champion", "verdict": "overfit",
             "claimed_gain_pct": 91.2, "holdout_gain_pct": 0.4,
             "generalization": 0.004, ...}}

$ chi champion runs/<run_id> --export best.py
{"code_hash": "...", "score_value": 4.3, "holdout": {"verdict": "overfit", ...}}
⚠ holdout overfit: claims +91.2% on the benchmark but realises only +0.4% held
  out (0% of the claim, floor 50%)
```

## Opting out

The gate is opt-in. A problem with no `holdout:` block runs exactly as before,
produces `holdout = None`, and creates no holdout directory.
