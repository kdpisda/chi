---
title: "Dry-Run Your Agent Benchmark Before It Spends Tokens"
description: "chi validate said OK to a problem with zero correctness seeds, and a wrong answer won the run. A $0 scripted dry run catches it before a real agent starts."
date: 2026-11-27T02:33:46Z
draft: false
category: "Guide"
tags: ["evaluators", "problem packs", "getting started", "autoresearch", "testing"]
toc: true
newsletter:
  subject: "chi validate said OK. A wrong answer won anyway."
  preheader: "Five config mistakes that pass the schema check, and the $0 run that catches them."
  body: |
    Before you point a fleet of coding agents at your own benchmark, you want to know the benchmark works. `chi validate` looks like that check. It isn't. It confirms the YAML parses into the right types, and stops there.

    I broke a copy of chi's example problem five ways. All five printed OK. In one of them, a `solve()` that returns its input unchanged became the run's champion, more than 4,000x "faster" than the baseline.

    The new post shows each broken config, what `chi run` did with it, and a three-candidate scripted dry run that costs nothing and catches the worst of them in a few seconds.

    [Read how to dry-run your agent benchmark](https://getchi.dev/blog/dry-run-agent-benchmark-before-spending-tokens/)
---

You wrote a benchmark for your own code, wrapped it as a problem, and you're
about to let coding agents loose on it overnight. If the benchmark is broken,
the agents will find out before you do, and the loop rewards them for
exploiting it, not for reporting it. The cheapest time to find the bug is before the first token.

## How do I dry-run an agent benchmark before spending tokens?

Replay a few candidates whose verdicts you already know through the real
evaluator, with no model in the loop. In chi that's the `scripted` adapter: it
writes each candidate from a JSON list into the workdir and evaluates it
exactly as an agent's edit would be evaluated
([`chi/agents/scripted.py`](https://github.com/kdpisda/chi/blob/main/chi/agents/scripted.py)).
Three candidates are enough:

1. **A copy of the baseline.** It must pass correctness and get a score.
2. **A deliberately wrong answer.** It must fail the correctness gate.
3. **A known-good improvement**, if you have one. It must pass and score better.

If any of those three verdicts comes out different, your problem is broken.
It costs $0. On the example problem it took about two seconds, the same as the offline demo.

The reason to bother is that `chi validate`, the obvious pre-flight check,
does less than its name suggests.

## What does chi validate actually check?

On a problem directory, `chi validate` calls `load_problem`, which parses
`problem.yaml` into a pydantic model and prints the seed count
([`chi/cli.py:69-80`](https://github.com/kdpisda/chi/blob/main/chi/cli.py#L69-L80),
[`chi/config.py:145-153`](https://github.com/kdpisda/chi/blob/main/chi/config.py#L145-L153)).
That catches wrong types. `repeats: five` and `direction: lower` both fail with
a clear pydantic error and exit 1.

It doesn't run anything, look for files, or check that a value makes sense. I
copied `problems/optimize_function` five times and broke each copy (chi 0.2.0,
2026-11-27):

| Change to the copy | `chi validate` |
|---|---|
| `seeds: []` | `OK problem 'optimize_function' (0 seeds)` |
| `timeout_seconds` misspelled `timout_seconds: 5` | `OK ... (3 seeds)` |
| `repeats: 0` | `OK ... (3 seeds)` |
| `tolerance: -1` | `OK ... (3 seeds)` |
| `check.py`, `bench.py`, `candidate.py` deleted | `OK ... (3 seeds)` |

The misspelled key is silently ignored: loading that copy gives
`timeout_seconds` 60, the default, not the 5 I meant. Pydantic models drop
unknown keys unless they're told not to, and `ProblemConfig` isn't
([`chi/config.py:97`](https://github.com/kdpisda/chi/blob/main/chi/config.py#L97)).
On `fleet.yaml`, the check is the same kind. A fleet pointing at any of these
broken problems also printed `OK fleet`.

None of this is hidden. The command's help says "Validate a fleet.yaml or a
problem directory", and the problem it validates is the schema. The trap is
treating "OK" as "this benchmark is ready". My own evaluator post nudged
readers that way: "Once `chi validate` passes on your own `problem.yaml`, you
have everything a fleet needs to start iterating." You have everything it
needs to start. Whether the results mean anything is a separate question.

## What happens when you run a broken problem?

I pointed the scripted dry run at four of the copies. This is the fleet file,
the same shape as `examples/offline.yaml`:

```yaml
run_name: dryrun-noseeds
problem: noseeds          # the copy with seeds: []
budgets:
  total_usd: 0.0
coders:
  - id: replay
    model: scripted       # not a model; the adapter ignores it
    adapter: scripted
    script: dryrun.json   # baseline copy, wrong answer, accumulate rewrite
policies:
  max_iterations: 3
```

The wrong answer is two lines:

```python
def solve(xs: list[float]) -> list[float]:
    # Wrong on purpose: returns the input, not prefix sums.
    return list(xs)
```

Here's what each run did, read back from `chi ledger` and `chi champion`:

| Copy | Wrong answer | Run result |
|---|---|---|
| unbroken | `correct 0`, seeds `[]`, no score | champion 0.106 ms (the accumulate rewrite), exit 0 |
| `seeds: []` | `correct 1`, scored | **champion 0.0128 ms: the wrong answer**, exit 0 |
| `repeats: 0` | `correct 0` | every score null, champion null, status `done`, exit 0 |
| scripts deleted | n/a | traceback, `FileNotFoundError` on `candidate.py`, exit 1 |

The `seeds: []` row is the one that matters. With no seeds, the correctness
loop in the evaluator has nothing to iterate over, so `correct` stays at its
initial `True` for every candidate
([`chi/eval/runner.py:90-108`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L90-L108)).
Every candidate then goes straight to the benchmark, and the fastest wins.
Returning the input untouched is very fast. `chi champion --export` wrote out
the two-line wrong answer as the winner, more than 4,000x under the 56.7 ms
baseline that run measured.

With a real agent, nothing in this run looks wrong. The score drops, the
champion is exported, and the summary says `done`. You'd find out when you
read the diff, if you read the diff.

`repeats: 0` fails the other way. The benchmark loop runs zero times, so there
are no samples and no score for anyone, the baseline included
([`chi/eval/runner.py:122-143`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L122-L143)).
The run still exits 0 with `"champion_score": null`. Unattended, that's a whole
night of agent iterations recorded as unscored.

The deleted-scripts run at least fails loudly, but it left the run row behind
with `status running` and no `ended_at`. Killed runs do the same, as I found
when I killed a two-coder fleet 28 times last week.

The negative tolerance and the misspelled timeout won't show up in a dry run
with a good checker. Tolerance is only used if your `check.py` reads it, and a
60-second timeout instead of 5 only matters when a candidate hangs. Those you
catch by reading your own `problem.yaml` once more.

## A pre-flight checklist for your own problem

This is what I run now before a real coder touches a new problem. Nothing
on it calls a model.

1. **`chi validate problems/my_problem`.** Catches type errors. Then read the
   seed count it prints. If it says `(0 seeds)`, stop.
2. **Write `dryrun.json`.** A copy of your baseline, one deliberately wrong
   candidate, and one improvement you know is correct, if you have one. Make
   the wrong one plausible and fast (return the input, skip the last element),
   because a fast wrong answer is exactly what a score-driven agent drifts
   toward.
3. **`chi run fleet-dryrun.yaml`** with `adapter: scripted` and
   `max_iterations` equal to the number of candidates.
4. **Read the verdicts, not the summary.** `chi ledger <run_dir>` prints one
   JSON line per candidate. The wrong one needs `"correct": 0`. Baseline and
   improvement need `"correct": 1` and a non-null `score_value`. Then
   `chi champion <run_dir> --export champion.py` and look at what won.
5. **Run it three to five times and compare baselines.** My six unbroken
   dry runs on a 4-vCPU box measured the same O(n²) baseline at 34.6 to
   42.0 ms. That spread is the floor under any "win" an agent reports on this
   machine. [Verify a coding agent's speedup is real](/blog/verify-coding-agent-speedup-is-real/)
   covers what to do when it's wide.

Step 4 is where the dry run earns its keep. The `seeds: []` run's summary
looked healthier than the unbroken one: a lower champion score, same `done`,
same exit 0.

For writing the checker and benchmark in the first place,
[How to write an evaluator for autoresearch](/blog/write-an-evaluator-for-autoresearch/)
walks through a real one, and the [problem pack docs](/docs/problems/) list
every field.

## Where this falls short

- **A dry run tests your candidates, not every candidate.** It proves the gate
  rejects the wrong answer you thought of. An agent will try ones you didn't.
  I've had candidates [exit 0 before the comparison](/blog/agent-passes-correctness-check-exit-code/)
  and [rewrite the benchmark file](/blog/coding-agent-edits-its-own-benchmark/).
  A dry run won't catch those unless you script them too.
- **chi doesn't do this for you yet.** `chi validate` could refuse zero seeds,
  zero repeats, unknown keys and missing entrypoint files, and `chi run` could
  stop when the baseline has no score. Today it doesn't. The docs say the
  session operator's `scaffold_problem` tool runs a baseline eval before
  handing a pack back, which should catch `repeats: 0` and missing scripts
  (no baseline score) but can't catch `seeds: []`, where the baseline scores
  fine. I haven't tested that path here because it needs a model.
- **The timings are from one shared 4-vCPU container.** The verdicts
  (`correct` 0 or 1, null or not) are deterministic. The milliseconds aren't.

Known gaps in the harness are tracked in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Try it on the shipped example first

If you have chi installed, run the example problem's scripted demo from a repo
checkout. It's the same machinery as the dry run above, with candidates that
are all correct:

```sh
chi run examples/offline.yaml
```

Then copy `examples/offline.yaml`, point `problem:` at your own directory, and
swap the script for your three candidates. If chi helps, a star on
[the GitHub repo](https://github.com/kdpisda/chi) helps other people find it.
