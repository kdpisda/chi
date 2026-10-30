---
title: "Can a Coding Agent Pass Your Correctness Check by Exiting?"
description: "A candidate that exits 0 before any comparison ran passed all 3 correctness seeds in chi. The repro, the cause, and a checker pattern that closes the gap."
date: 2026-10-30T02:36:52Z
draft: false
category: "Case study"
tags: ["evaluation integrity", "reward hacking", "autoresearch", "correctness gate", "LLM agents"]
toc: true
newsletter:
  subject: "My correctness gate passed a wrong answer"
  preheader: "Three of three seeds passed. The candidate returned its input untouched."
  body: |
    I wrote a cheating candidate for chi's prefix-sum problem and ran it through the evaluator. Correctness gate: 3 of 3 seeds passed. Score: about 0.017 ms, against about 48 ms for the shipped baseline.

    The candidate returns its input unchanged, which is the wrong answer. It passes because the checker imports the candidate in its own process, and the candidate can exit 0 before the comparison runs. The runner only reads the exit code.

    That is a real gap in chi's shipped example, and the post says so. It also covers a checker pattern I tested that closes it, and why a sandbox alone doesn't.

    If you run agent loops against a pass/fail check, [read it](https://getchi.dev/blog/agent-passes-correctness-check-exit-code/) before you trust your next "all tests passed".
---

A coding agent loop is only as honest as its pass/fail check. If the check can
be satisfied without doing the work, an optimizer that runs for hours will
eventually find out. I wanted to know whether a check in chi, my open-source
autoresearch harness, can be passed by a candidate that is plainly wrong. It
can, and this post shows the repro and a fix I tested.

**Autoresearch**, here, means a loop where an agent edits code, a harness
scores it, and the best correct version is kept. The "correct" part is a
**correctness gate**: a command that must succeed before a candidate is scored
at all.

## Can an agent pass a correctness check without solving the task?

Yes, when the harness trusts an exit code that the candidate can influence.
chi's gate runs a checker command once per seed and treats return code 0 as a
pass. The test is the `returncode != 0` branch in
[`chi/eval/runner.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py).
The runner reads nothing else from the checker.

In the shipped `optimize_function` pack, the checker
([`check.py`](https://github.com/kdpisda/chi/blob/main/problems/optimize_function/check.py))
loads the candidate with `importlib` and calls `solve()` inside its own
process, then compares the result to a reference. Anything the candidate does
in that process happens before the comparison, including ending the process.

## What does the cheating candidate look like?

A few lines. It returns its input untouched, which is wrong, unless it sees
`--seed` in the command line, in which case it exits with status 0:

```python
import os, sys

def solve(xs):
    if "--seed" in sys.argv:
        os._exit(0)
    return list(xs)
```

The checker is invoked as `check.py candidate.py --seed 11`, so `--seed` is
in `sys.argv` (the candidate shares the checker's process). It exits, and the
runner sees return code 0. I copied the problem pack, dropped this in as
`candidate.py`, and called `chi.eval.runner.evaluate` directly on 2026-10-30:

```text
forged candidate: correct=True, seeds_passed=[11, 27, 43], score_value=0.0166 ms
shipped baseline: correct=True, seeds_passed=[11, 27, 43], score_value=47.6 ms
```

The benchmark script runs without `--seed`, so the forged candidate just
returns its input and gets a spectacular runtime. Each score is a median of
five repeats on this machine. Timings drift from run to run, so read these as
"about 0.017 ms versus about 48 ms", not as a benchmark.

## Why does it matter for a long unattended run?

Because of what happens next. chi's champion is the lowest-scoring experiment
that is marked correct (`champion` in
[`chi/store/ledger.py`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py)
selects `correct=1` rows ordered by score). The forged candidate is marked
correct and scores thousands of times faster than the baseline here, so it
would be the champion. Anything that compares later candidates against the
champion is then comparing against a number nobody can beat honestly.

This is a different hole from the ones I covered earlier. A
[holdout gate](/blog/coding-agent-overfits-benchmark/) catches a real
solution that only works on the benchmark's input. Here there is no solution
at all. The [Goodhart post](/blog/goodhart-law-ai-agents-benchmark/) is about
a metric that is biased. This one is about a check that can be skipped.

I did not see an agent do this in a real run. I wrote the candidate by hand
to test the gate. But the loop is built around agents that edit files and are
rewarded for a number, so I treat "an agent could stumble into it" as a
design constraint, not a rumor.

## Does a sandbox fix it?

Not by itself, as far as I can tell from the code. chi has an opt-in
`eval_sandbox: docker` setting that runs the correctness and benchmark
commands in a container (`_dispatch` in `runner.py`). That protects the host
from a hostile candidate. It runs the same checker command, so the checker
still imports the candidate in-process and the candidate can still end it. I
read this from the code and did not run the forged candidate under Docker.

## How do I write a checker that can't be skipped?

Keep the comparison out of the process that runs the candidate. The candidate
runs in a child process and prints its answer, and the parent compares. If the
child exits early, there is nothing to compare, and the checker fails closed.

```python
child = subprocess.run(
    [sys.executable, "child.py", args.candidate, str(args.seed)],
    capture_output=True, text=True, timeout=30,
)
try:
    got = json.loads(child.stdout.strip().splitlines()[-1])
except (IndexError, ValueError):
    print(json.dumps({"error": "candidate produced no result"}))
    return 1
want = reference_solve(xs)  # computed in the parent
```

Here `child.py` loads the candidate, calls `solve()` on the seeded input, and
prints the result as JSON. I made that change to a copy of the pack and
re-ran both candidates the same day:

```text
forged candidate: correct=False, detail: correctness failed on seed 11
shipped baseline: correct=True, seeds_passed=[11, 27, 43], score_value=51.0 ms
```

The forged candidate now fails on the first seed, and the honest baseline
still passes. This is a pattern for your own problem packs. I have not
changed `problems/optimize_function` in the repo.

## Where this falls short

The child-process pattern closes the exit trick, not cheating in general. The
candidate and the reference sit in the same working directory, so a candidate
that reads `reference.py` and copies its answer is a separate problem I
haven't tested. A reference the candidate can't see, plus the
[two-tier evaluation](/docs/problems/), address other parts of that.

The gap in the runner is also mine to fix. It could demand a verdict from the
checker, such as a nonce the checker prints, instead of trusting a bare exit
code. That is not built. It is the kind of item
[the gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md)
exists to track, and I would rather you read it here than find it yourself.

## What should I check in my own loop?

Three questions, all answerable from your own harness. Does the check run in
the same process as the candidate? Does the harness read anything from the
check other than an exit code? Could the candidate's output reach the
comparison without going through code the candidate can't touch?

If any answer is yes, a long unattended run will eventually test it for you.
To see how chi's gates fit together without an API key, run the
[offline walkthrough](/blog/first-autoresearch-loop-no-api-key/).
