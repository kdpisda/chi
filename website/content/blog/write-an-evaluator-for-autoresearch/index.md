---
title: "How to Write an Evaluator for Autoresearch"
description: "An evaluator is three commands: a correctness gate on held-out seeds, a benchmark that prints one number, and a direction. Walk through a real one."
date: 2026-09-28T02:33:04Z
draft: false
category: "Guide"
tags: ["autoresearch", "evaluators", "problem packs", "evaluation integrity", "getting started"]
toc: true
newsletter:
  subject: "The whole harness is downstream of one YAML file"
  preheader: "Three commands and a scoring rule is all an autoresearch loop needs to run unattended."
  body: |
    An autoresearch loop — an LLM fleet that edits code, checks it, scores it, and keeps the best version, on repeat — is only as good as the thing that checks and scores. Get that part wrong and a fleet will find the exact hole in it, on iteration one, for free.

    This post walks through `problems/optimize_function`, chi's bundled reference problem, entrypoint by entrypoint: the correctness gate that runs on seeds a candidate never sees, the benchmark command that prints one JSON number, and what happens when I hand it both a correct fast candidate and a broken one.

    Numbers in the post are from runs in this session: a 500x+ speedup on a real rewrite, and a broken candidate caught cold by the correctness gate before it ever reaches a benchmark.
---

If you already run an LLM coding agent against some kind of check, you've
written half an evaluator without calling it that. An **evaluator** is the
part of an [autoresearch](/blog/what-is-chi-autoresearch-harness/) loop that
turns a candidate file into two facts: is it correct, and how good is it. Get
those two facts right and a fleet of agents can iterate on almost anything —
a slow function, a kernel, a heuristic — unattended. Get them wrong and the
fleet will happily optimize the wrong thing, because a fleet only sees what
the evaluator measures.

chi treats "anything with a programmatic evaluator" as a **problem pack**: a
directory holding a `problem.yaml` and whatever scripts its commands need
([`website/content/docs/problems.md`](https://github.com/kdpisda/chi/blob/main/website/content/docs/problems.md)).
The bundled reference pack,
[`problems/optimize_function`](https://github.com/kdpisda/chi/blob/main/problems/optimize_function),
is small enough to read end to end, so that's what this post walks through.

## What's actually in a problem pack

`problems/optimize_function/problem.yaml`:

```yaml
name: optimize_function
description: >
  Minimize the runtime of solve(xs) -> prefix sums of xs. Output must match
  reference.py within tolerance on all held-out seeds. Any pure-Python or
  stdlib approach is allowed; the shipped baseline is intentionally O(n^2).
candidate: candidate.py
entrypoints:
  correctness: "{python} check.py {candidate} --seed {seed}"
  benchmark: "{python} bench.py {candidate}"
score:
  metric: runtime_ms
  direction: minimize
  repeats: 5
correctness:
  seeds: [11, 27, 43]
  tolerance: 1.0e-6
timeout_seconds: 60
```

Five fields do all the work:

- **`candidate`** — the one file each coder edits, in its own worktree.
- **`entrypoints.correctness`** — a shell command chi runs once per seed, with
  `{python}`, `{candidate}` and `{seed}` substituted. Exit 0 means pass.
- **`entrypoints.benchmark`** — a shell command that prints one JSON number.
  No exit-code contract beyond "0 and valid JSON."
- **`correctness.seeds`** — the held-out inputs. A candidate is never shown
  the reference output for these; it only gets pass/fail.
- **`score`** — which key to read from the benchmark's JSON, which direction
  is better, and how many times to repeat it.

That's the whole contract. `chi validate` checks it loads before you point a
run at it:

```
$ chi validate problems/optimize_function
OK problem 'optimize_function' (3 seeds)
```

## The correctness gate: a hard, non-negotiable no

`check.py` does one thing — build a candidate module, run it on a seeded
input, and diff the output against a reference the candidate never imports
directly:

```python
# problems/optimize_function/check.py
rng = random.Random(args.seed)
xs = [rng.uniform(-100.0, 100.0) for _ in range(500)]
got = _load(args.candidate, "candidate").solve(list(xs))
want = _load(str(Path(__file__).with_name("reference.py")), "reference").reference_solve(xs)
max_abs_error = max(abs(g - w) for g, w in zip(got, want))
return 0 if max_abs_error <= 1e-6 else 1
```

This is the part worth getting exactly right, because it's the only thing
standing between a genuine speedup and a candidate that just stopped doing
the work. In chi's runner, correctness runs first and is a **hard gate**: a
candidate that fails any seed never gets benchmarked
([`chi/eval/runner.py:100-108`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py))
and, because champion selection filters on `correct=1`, never becomes
champion either
([`chi/store/ledger.py:58-65`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py)).
I confirmed this by handing the pack a candidate that silently drops the
last element of its output:

```
$ python check.py candidate_broken.py --seed 11
{"max_abs_error": 1158.0954625784286}
$ echo $?
1
```

Non-zero exit, and the runner stops there — no benchmark score, no chance to
look fast on a technicality. That asymmetry is the point: three held-out
seeds is a small sample, but it only has to catch *wrong*, not measure
*good*.

## The benchmark: one command, one number

`bench.py` is even shorter — build the candidate, warm it up once, time one
real call, print `{"score": <ms>}`:

```python
# problems/optimize_function/bench.py
candidate.solve(list(xs))  # warmup
start = time.perf_counter()
candidate.solve(list(xs))
elapsed_ms = (time.perf_counter() - start) * 1000.0
print(json.dumps({"score": elapsed_ms}))
```

chi doesn't trust a single timing. `score.repeats: 5` tells the runner to run
this command five times per eval and score the **median**, and it also
records the population spread as `noise_std`
([`chi/eval/runner.py:120-143`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py)).
A candidate that reports a non-finite, zero, or negative time — freezing the
clock instead of getting faster — is rejected as an eval failure, not
crowned as a win.

## Running it end to end

The shipped baseline candidate is a deliberately naive O(n²) prefix sum:

```python
def solve(xs: list[float]) -> list[float]:
    return [sum(xs[: i + 1]) for i in range(len(xs))]
```

Benchmarked on a 4,000-element input, three separate runs in this session
landed between 41.6ms and 48.7ms. Swapping in the one-line stdlib fix
(`itertools.accumulate`, which is what `reference.py` uses internally) passed
correctness on all three held-out seeds and benchmarked at 0.075–0.082ms —
roughly **500 to 650 times faster**, depending on the run, all on the same
machine in the same session. That's the whole loop an autoresearch fleet
runs on repeat: edit `candidate.py`, run `check.py` on each seed, run
`bench.py` five times, keep the median if every seed passed, compare to the
champion.

## Where this falls short

Two honest limits, so you know what you're signing up for before you write
one of these for your own code:

- **One editable file, not a repo.** A problem pack assumes the thing being
  optimized fits in a single `candidate.py`. If what you actually want faster
  is a real repository — your test suite, your build, your bundle size —
  chi doesn't have a zero-config mode for that today; you'd need to shape
  the work as a single-file candidate first. This is the single biggest
  adoption cost chi has right now, tracked honestly in the
  [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md#2-no-zero-config-mode-for-an-existing-repo--highest-adoption-cost).
- **You still have to write the reference.** Nothing stops a badly written
  `check.py` from being too permissive (a tolerance that's too loose) or too
  strict (seeds that don't cover the input space). The gate is only as good
  as the seeds you picked and the tolerance you set — chi enforces the
  *mechanism*, not the quality of your specific numbers.

If your target is a whole repository rather than one function, read
[how chi's holdout gate catches an overfit champion](/blog/coding-agent-overfits-benchmark/)
first — the same "never show the candidate the real test" idea, applied to a
harder case: a candidate that passes the gate you wrote but doesn't
generalize past it.

Once `chi validate` passes on your own `problem.yaml`, you have everything a
fleet needs to start iterating — see the
[problems & evaluators docs](/docs/problems/) for the `build`/`profile`
entrypoints compiled targets need, and the two-tier proxy/authoritative setup
for evaluators that are too expensive to run on every iteration.
