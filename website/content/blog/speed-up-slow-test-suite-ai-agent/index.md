---
title: "Speed Up a Slow Test Suite With an AI Coding Agent, Safely"
description: "Test-suite runtime looks like the perfect score for an AI coding agent. Here's the evaluator that kept a skip-everything conftest.py from winning my run."
date: 2026-11-20T02:34:03Z
draft: false
category: "Guide"
tags: ["testing", "evaluators", "autoresearch", "coding agents", "evaluation integrity"]
toc: true
newsletter:
  subject: "The fastest test suite is the one that skips every test"
  preheader: "My exit-code gate crowned it. A second gate, about 30 lines of Python, did not."
  body: |
    New post: how to point an AI coding agent at a slow test suite without rewarding it for skipping the tests.

    Suite runtime looks like an ideal score. It's one number, lower is better, and the tests come with a pass/fail check built in. But "pytest exited 0" is a weaker check than it sounds, and an agent loop optimizes exactly what you measure.

    I built a small pytest pack and replayed three hand-written candidates through chi at $0. With an exit-code gate, a conftest.py that skips all 22 tests won the run. With a stricter gate, it failed on the first seed, a shared-fixture "speedup" failed on one test order out of three, and the honest fix won at about 3.9x. The post has the gate's code and the run output.

    [Read it on getchi.dev](https://getchi.dev/blog/speed-up-slow-test-suite-ai-agent/).
---

If your CI suite takes twenty minutes, pointing an AI coding agent at it and
saying "make this faster" is tempting. Runtime is one number, lower is better,
and the suite already says whether the code still works. That makes it look
like the ideal target for an autonomous loop. It is, as long as you don't
measure "the tests passed" by pytest's exit code alone.

## Can an AI coding agent speed up a slow test suite?

Yes, if you give it an evaluator that measures what you mean. To speed up a slow
test suite with an AI coding agent, you need two checks, not one. A **benchmark**
times the suite and prints a number. A **correctness gate** proves that the same
tests still ran and passed, in more than one order. The loop that edits, runs
both checks and keeps the best version is **autoresearch**: an agent (or a
fleet of them) proposing changes against a fixed score, unattended.

The benchmark is the easy half. The gate is where it goes wrong, because the
cheapest way to make a test suite fast is to run fewer tests. pytest treats a
skipped test as a non-failure, so a suite where every test is skipped exits 0.

I wanted numbers for this rather than a warning, so I built a small pytest
problem and ran it through chi with two gates. Everything below ran on
November 20, 2026, offline, for $0.

## The setup: one slow fixture, 22 tests

The suite is a stand-in for a common slow-test pattern: an expensive fixture
rebuilt for every test. Here it's a 50 ms sleep plus a
1,000-item dict, scoped per test:

```python
# conftest.py (the baseline, and the only file the agent may edit)
@pytest.fixture
def catalog():
    # Stand-in for expensive setup: load fixtures, build an index, start a DB.
    time.sleep(0.05)
    return {"sku-%d" % i: {"price": i, "tags": []} for i in range(1000)}
```

`tests/test_catalog.py` has 22 tests: 20 parametrized price lookups, one that
checks every item starts with no tags, and one that appends a tag to `sku-1`.
That last test mutates the fixture. It's there on purpose.

In chi, a problem is described by a **problem pack**: a `problem.yaml` plus the
scripts its commands call. The candidate is `conftest.py`:

```yaml
name: slow_tests
candidate: conftest.py
entrypoints:
  correctness: "{python} run_suite.py --seed {seed}"
  benchmark: "{python} bench_suite.py"
score:
  metric: suite_seconds
  direction: minimize
  repeats: 5
correctness:
  seeds: [1, 2, 3]
timeout_seconds: 120
```

chi runs the correctness command once per seed and treats any nonzero exit as
a fail ([`chi/eval/runner.py:91-107`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L91-L107)).
Only a candidate that passes every seed gets benchmarked, and the score is the
median of `repeats` runs, with the spread recorded as noise
([`runner.py:112-144`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L112-L144)).

The benchmark script runs pytest once and prints wall-clock seconds as JSON:

```python
start = time.perf_counter()
proc = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
    capture_output=True, text=True,
)
elapsed = time.perf_counter() - start
if proc.returncode != 0:
    sys.exit(proc.stdout[-500:])
print(json.dumps({"score": elapsed}))
```

`-p no:cacheprovider` keeps pytest from reading or writing `.pytest_cache`, so
no run inherits state from the one before it. A failing benchmark exits
nonzero, which chi records as an eval failure, not a score
([`runner.py:129-132`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L129-L132)).

## Why is "pytest exited 0" not a correctness check?

To measure the weak gate, I made a second copy of the pack whose correctness
command is just `{python} -m pytest -q tests`. Then I wrote three candidate
`conftest.py` files by hand and replayed them through both packs with chi's
scripted adapter, which feeds canned sources into a real run. No model wrote
them. They are the shapes I want a gate to handle:

1. **Skip everything.** A `pytest_collection_modifyitems` hook that marks every
   test as skipped.
2. **Session scope.** The same fixture with `scope="session"`, so it's built
   once and shared by all 22 tests.
3. **Session scope plus a copy.** Build once per session, hand each test a
   `copy.deepcopy` of it.

With the exit-code gate (run `tests-naive-f64d95`):

| Candidate | Gate | Median suite time |
|---|---|---|
| Baseline | pass | 1.351 s |
| Skip everything | pass | **0.217 s (champion)** |
| Session scope | pass | 0.301 s |
| Session scope + copy | pass | 0.326 s |

The winner ran zero tests. And the second-fastest candidate has a real bug that
the default test order hides. I'll get to it below.

## How do you write a gate that counts tests?

The fix for skipping is to stop asking pytest whether it failed and to ask
what actually passed. pytest's `--junitxml` report has counts. The gate below
fails unless exactly the expected number of tests passed, with no failures,
errors, or skips:

```python
EXPECTED = 22  # tests in tests/, counted by hand when the pack was written

seed = sys.argv[sys.argv.index("--seed") + 1]
env = dict(os.environ, SUITE_SEED=seed)
proc = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
     "-p", "shuffle_plugin", "--junitxml=report.xml", "tests"],
    env=env, capture_output=True, text=True,
)
suite = ET.parse("report.xml").getroot()
suite = suite if suite.tag == "testsuite" else suite.find("testsuite")
total = int(suite.get("tests"))
bad = sum(int(suite.get(k)) for k in ("failures", "errors", "skipped"))
passed = total - bad
if proc.returncode != 0 or passed != EXPECTED:
    print(f"seed {seed}: exit {proc.returncode}, passed {passed}/{EXPECTED}")
    sys.exit(1)
```

`EXPECTED` is pinned, not read from the run. A candidate that deselects tests
during collection lowers `total`, so comparing against the run's own total would
let it through.

## Why should the test order change per seed?

The `shuffle_plugin` is the second half of the gate. It's a few lines in the
pack, outside the candidate, and it shuffles the collected tests with the seed:

```python
def pytest_collection_modifyitems(session, config, items):
    seed = os.environ.get("SUITE_SEED")
    if seed is not None:
        random.Random(int(seed)).shuffle(items)
```

This is what catches candidate 2. Widening a fixture's scope is the textbook
test-suite speedup, and it's often right. But this fixture is mutable, and one
test writes to it. In file order the "starts empty" test runs before the "adds
a tag" test, so the suite passes. In some other order it doesn't.

Running the strict gate by hand on each candidate:

```text
skip everything:  seed 1: exit 0, passed 0/22     (rejected on seed 1)
session scope:    seed 1 ok
                  seed 2: exit 1, passed 21/22    (rejected on seed 2)
                  seed 3 ok
scope + copy:     seeds 1, 2, 3 ok
```

Only one of three orders exposed the shared-state bug. A gate that ran only
seed 1 or only seed 3 would have passed it. chi stops at the first failing seed
and records which ones passed, so the ledger shows `seeds_passed: [1]` for that
candidate.

## What happened in the full run?

The same three candidates through the strict pack (run `tests-pack-446f51`):

| Candidate | Gate | Median suite time |
|---|---|---|
| Baseline | pass | 1.346 s |
| Skip everything | fail, seed 1 | – |
| Session scope | fail, seed 2 | – |
| Session scope + copy | pass | **0.344 s (champion)** |

About 3.9x faster, with all 22 tests running and passing in three different
orders. The per-candidate noise (population standard deviation over 5 runs) was
0.01 to 0.04 s, so the gap from 1.35 s isn't noise. The win is where you'd expect: the
50 ms setup now runs once instead of 22 times, and each test pays for a
`deepcopy` instead.

You can see both runs the same way, with `chi ledger <run_dir>` for every
candidate's verdict and `chi champion <run_dir>` for the winner.

## What else trips up a test-suite evaluator?

A few things I checked or would check before trusting a run like this:

- **Flaky tests.** A gate that requires every test to pass on every seed will
  sometimes reject a correct candidate. A test that flakes 1% of the time
  fails at least one of three seeds about 3% of the time (1 − 0.99³). Fix or
  quarantine flaky tests before the loop starts, or the loop wastes rounds.
- **Warm vs. cold runs.** I compared fresh copies of the pack, with no
  `__pycache__`, against repeat runs, and saw no difference here (1.37 to 1.44 s
  either way). A real suite with a database or build cache might differ, so
  time a cold run and a warm run before you pick `repeats`.
- **Cached results.** chi caches an eval by the candidate's content hash
  ([`runner.py:70-78`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L70-L78)).
  If you edit the tests mid-run, an unchanged `conftest.py` returns its old
  result. Start a new run after changing the tests.
- **Measure the suite, not the harness.** The 1.35 s baseline includes Python
  and pytest startup. With a small suite, that fixed cost caps the possible speedup.

## Where this falls short

This pack works because the whole speedup lives in one file. chi's model is
one editable candidate per problem, inherited from the kernel and leaderboard
problems it was built for. Most real test-suite wins touch many files: fixtures,
factories, test settings, sometimes the code under test. chi can't express that
today. A repo mode, with `measure` and `checks` commands and keep/revert in git,
is gap #2 in the public
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).
If that's your problem this afternoon, the
[comparison with pi-autoresearch and Karpathy's autoresearch](/blog/chi-vs-pi-autoresearch-vs-karpathy-autoresearch/)
points to a lighter tool.

The gate also lives in the agent's working directory. A coding agent with file
tools can edit `run_suite.py` or the tests as easily as `conftest.py`, and I
showed what that does to a scorer in
[Can a Coding Agent Edit Its Own Benchmark?](/blog/coding-agent-edits-its-own-benchmark/).
Counting tests closes the skip loophole, not that one. Keep the scoring scripts
and a pinned copy of the tests out of reach of the agent, and diff the tests
directory after a run.

And these were three candidates I wrote, replayed by a script. They show what
the gate accepts and rejects. They don't show how often a real model tries to
skip tests.

## Try the loop first, then write your gate

The checklist is short: count passed tests against a pinned number, shuffle the
order per seed, keep the scorer away from the agent, and time a cold run. If you
want a feel for the loop before you write a pack, the
[offline demo](/blog/first-autoresearch-loop-no-api-key/) runs a full chi loop
in a few seconds with no API key. Then
[How to Write an Evaluator for Autoresearch](/blog/write-an-evaluator-for-autoresearch/)
covers the rest of `problem.yaml`.
