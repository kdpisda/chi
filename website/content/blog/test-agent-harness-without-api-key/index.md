---
title: "Test an Agent Harness Without an API Key: What's Real"
description: "You can test an agent harness with no API key if the model is the only fake part. What chi's offline demo really runs, what it fakes, and one stall it exposed."
date: 2026-10-05T02:31:57Z
draft: false
category: "Guide"
tags: ["getting started", "testing", "autoresearch", "watchdog", "coding agents"]
toc: true
newsletter:
  subject: "The offline demo I ran for 8 rounds instead of 3"
  preheader: "Only the model is fake. Here is what the harness did when I let the script run dry."
  body: |
    You can test an agent harness without an API key, as long as the model is the only fake part. chi's offline demo does that: a scripted coder replays three canned candidates, and everything around it is real.

    I wanted to know exactly where "real" stops, so I ran the demo for 8 iterations instead of 3. The script ran out after 3, the harness kept going, and I got to watch the watchdog stop a coder that was only repeating itself.

    The post lists what the demo checks, what it cannot check, and the one number in the run that looks like a speedup but is noise. [Read the walkthrough](https://getchi.dev/blog/test-agent-harness-without-api-key/).
---

To test an agent harness without an API key, replace only the model. Keep the
evaluator, the run store, champion selection and the stall detection real, and
feed the loop a fixed sequence of candidates. Then the run exercises everything
that decides whether a result is trustworthy, for $0 and in a couple of seconds.

chi ships exactly that: a `scripted` coder adapter and `examples/offline.yaml`.
My [first-loop post](/blog/first-autoresearch-loop-no-api-key/) shows how to run
it. This one asks a harder question: what does the run actually prove, and where
does the fake leak through? I ran it to find out.

## What does the scripted coder do?

Very little, on purpose. This is the whole adapter,
[`chi/agents/scripted.py`](https://github.com/kdpisda/chi/blob/main/chi/agents/scripted.py):

```python
def run_iteration(self, seed: SeedContext) -> IterationOutcome:
    self.ack_steering(seed.steering_hash)
    self.heartbeat()
    if self.script is not None:
        sources: list[str] = json.loads(Path(self.script).read_text())
        source = sources[min(seed.iteration, len(sources) - 1)]
        (self.workdir / self.problem.candidate).write_text(source)
    evaluate(
        self.problem, self.workdir, store=self.store, run_id=self.run_id,
        agent_id=self.agent_id, strategy=seed.strategy,
    )
    return IterationOutcome(evals_run=1)
```

It writes the i-th canned source into the candidate file, calls the same
`evaluate()` a real coder calls, and reports one eval. From the seed it uses only the
iteration number, the strategy name and the steering hash. It never reads the
steering text, only acknowledges its version (`scripted.py:15`). The three candidates in
[`examples/demo_candidates.json`](https://github.com/kdpisda/chi/blob/main/examples/demo_candidates.json)
are an O(n²) prefix sum, an O(n) loop, and `itertools.accumulate`.

So the model's judgment is fake. Everything it feeds into is not.

## What is real in an offline run?

I ran `chi run examples/offline.yaml` on 2026-10-05. It finished in about 2
seconds, with `total_cost_usd` at 0 and these lines in the summary:

```json
{
  "iterations": 3,
  "baseline_score": 34.97978300000426,
  "champion_score": 0.0674169999967944,
  "total_cost_usd": 0,
  "status": "done"
}
```

Scores are `runtime_ms`, so yours will differ. What this exercised:

- **The correctness gate.** Each candidate runs `check.py` on three held-out
  seeds (11, 27, 43) before it is timed
  ([`runner.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py)).
- **The benchmark and its noise estimate.** Five repeats per candidate. The
  median is the score and the population standard deviation is stored as
  `noise_std`.
- **The run store.** SQLite file plus `events.jsonl` and `experiments.jsonl`
  mirrors, all under the run directory the summary prints (by default under
  `~/.local/share/chi/runs/`).
- **Champion selection and archiving.** Every correct, scored candidate's exact
  bytes land in `champions/<hash>.py`. The champion is the lowest correct score
  in the store (`chi/store/ledger.py:58`).
- **Steering and heartbeats.** The steering file is created, versioned by hash
  and acknowledged.
- **The watchdog.** More on that below, because it is the part I got to watch
  fire.

What it cannot exercise is anything a model does: prompt construction, tool
calls, cost accounting, context limits, a model that edits the wrong file. A
green offline run says the harness around the model works. It says nothing about
whether a particular model is any good in it.

## Is the first "improvement" real?

Not quite, and this is the detail I would want to know before trusting the
demo. The first scripted candidate has the same algorithm as the baseline, with
a different comment. A different comment is a different code hash, so chi
evaluates it again as a new experiment. I ran the fleet for 8 iterations and
read the ledger back:

```text
baseline 5dcafb15  correct  34.7563 ms  noise_std 1.8857
replay   650927fe  correct  35.8204 ms  noise_std 9.0958   (same O(n^2) algorithm)
replay   410b8023  correct   0.0961 ms  noise_std 0.0084   (O(n) loop)
replay   3b005691  correct   0.0699 ms  noise_std 0.0056   (itertools.accumulate)
```

The O(n²) rewrite scored 35.8 ms against a 34.8 ms baseline, with a standard
deviation of 9.1 ms on that second run. Same code, a roughly 1 ms gap, well
inside the noise. If a harness promoted on a single run it could call that a
regression or a win depending on the draw. That is the reason chi stores
`noise_std` next to every score, and the reason the
[noise post](/blog/agent-benchmark-improvement-is-noise/) says to demand a
margin. The demo shows it with no setup.

The two real gains, about 360 times and about 500 times faster than baseline, are
far outside any noise band, which is why they make a good demo. I would not
quote those multiples as a benchmark. They come from one prefix-sum function on
a 4,000-element input, on a shared sandbox.

## What happens when the script runs out?

The script has three entries. `sources[min(seed.iteration, len(sources) - 1)]`
means every iteration past the third replays the last one. The shipped config
sets `max_iterations: 3`, so you never see this. I raised it to 8 and ran it
again.

Iterations 3 through 7 each wrote the same source, hash `3b005691`. The run
stopped with `"status": "stalled"` and this event:

```text
32 WATCHDOG_KILL {'reason': 'candidate unchanged 6 consecutive iterations'}
```

The rule is in
[`watchdog.py`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/watchdog.py):
with `repeat_k` at its default of 3, the same hash three times in a row earns a
"try a different approach" mutation note, and six times in a row kills the
coder. The run then released its task and logged `STOP` with status `stalled`.
This is the loop detection I described in
[how to stop a coding agent from looping](/blog/stop-coding-agent-looping/),
triggered by a script instead of a model, with no model call involved.

Two things in that event log are worth knowing:

1. **Repeats are free.** Iterations 3 to 7 produced no new `RESULT` events. The
   evaluator found the hash in the store and returned the cached score
   (`runner.py:71-77`), so the experiments table holds 4 rows, not 9. A real
   coder resubmitting identical code costs the harness a lookup, not a benchmark
   (the coder's model would still have been paid to produce it).
2. **The adapter still reports `evals_run=1` for those cached iterations.** The
   eval-recency rule counts iterations with zero evals, so it never fires here.
   Only the repeated-hash rule caught this stall. Both rules exist for a
   reason, and the demo happens to isolate one of them. I did not check whether
   real adapters count a cached result the same way.

## How do I use this for my own harness?

Two ways, both cheap.

**Smoke-test an evaluator.** Copy `examples/offline.yaml`, point `problem:` at
your own pack, and write a script of candidates you already know the right
answer for: a correct fast one, a correct slow one, a wrong one. If the wrong
one lands in the ledger as `correct: 0`, or the slow one wins, your evaluator
has a bug you found for $0. The
[evaluator guide](/blog/write-an-evaluator-for-autoresearch/) covers the pack
format.

**Test a stall rule.** Make a script that repeats one candidate, or one that
never produces a scoreable result, and set `max_iterations` past the
threshold. You are checking that the run ends as `stalled` and not as a bill.

## Where this falls short

An offline run cannot tell you your model will find anything. It also cannot
catch a failure that needs a model to trigger, like a coder editing the
benchmark instead of the candidate. For that, run a real fleet with a small
budget. chi's known gaps are listed in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Run it yourself

```sh
uv tool install getchi
git clone https://github.com/kdpisda/chi && cd chi
chi run examples/offline.yaml
```

Then raise `max_iterations` to 8 in a copy of the config and watch it stall.
If the harness is useful to you, a star on the
[repo](https://github.com/kdpisda/chi) helps other people find it.
