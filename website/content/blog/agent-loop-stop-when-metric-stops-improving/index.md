---
title: "Agent Loop Keeps Running After the Metric Stops Improving"
description: "Your agent loop keeps running after the metric stops improving. I tested four stop rules on real benchmark noise. The obvious ones quit while it still improved."
date: 2026-11-22T02:34:36Z
draft: false
category: "Deep dive"
tags: ["autoresearch", "loop reliability", "benchmark noise", "director", "coding agents"]
toc: true
newsletter:
  subject: "My stop rule quit while the loop was still improving"
  preheader: "Four ways to end a saturated agent loop, tested against 200 real evaluations."
  body: |
    Autoresearch loops are often built never to stop. Karpathy's reference prompt says so in capital letters. So when the metric goes flat, the loop keeps paying for runs until a human notices.

    The obvious fix is a patience rule: stop after N tries with no new best. I tested four versions of that rule against 200 real evaluations of the same code, so every score difference was pure noise, and then against loops that were still improving.

    With a short window, every rule quit early in over 90% of the runs where the loop was still improving. A two-sigma noise band almost never counted a win. The version that held up re-measures each apparent win before it resets the clock. The post has the numbers, the rule in 20 lines, and where my test is weak. [Read the post](https://getchi.dev/blog/agent-loop-stop-when-metric-stops-improving/).
---

Your agent loop found a good result at 1am. At 9am it's still running, still
paying for model calls, and the best score hasn't moved in seven hours. When an
agent loop keeps running after the metric stops improving, the loop isn't
broken. It was built that way. Karpathy's
[autoresearch prompt](https://github.com/karpathy/autoresearch/blob/master/program.md)
(fetched November 2026) ends with a section titled **NEVER STOP**: "The loop
runs until the human interrupts you, period."

The fix sounds easy: stop when the score stops getting better. I tried four
versions of that rule against real benchmark noise from chi, my autoresearch
harness. The two obvious ones stopped too early, usually while the loop was
still making real progress. This post shows the numbers and the rule that held
up better.

## Why does an agent loop keep running after the metric stops improving?

Because "keep going" is the safe default when you're asleep. An autoresearch
loop is an agent that edits code, scores it with a fixed evaluator, keeps the
best version and repeats. Nobody wants to wake up to a loop that quit after two
bad ideas, so loops are told to run until stopped.

chi has the same shape. Its director (the part that runs a fleet of coding
agents in rounds) stops on a target score, a cost ceiling, a dead evaluator, or
your `/stop` command
([`chi/director/loop.py:89,164-190`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py)).
It does not stop because the metric went flat. That's gap #6 in
[chi's public gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md#6-no-saturation-aware-stop),
which cites a practitioner's loop that ran 11 days on a benchmark it had
already solved. I showed the chi version of this in
[what stops a coding agent left running overnight](/blog/run-coding-agent-unattended-overnight/):
12 `stuck` rounds in a row, and nothing ended the loop but me.

So the question is what a saturation stop should look like. "Saturation" here
means the loop's best score has stopped moving for reasons other than bad luck.
The hard part is the "other than bad luck."

## What does benchmark noise do to a "no new best" rule?

To get real noise, I took one candidate from chi's offline demo (an O(n)
prefix sum, `examples/demo_candidates.json`) and made 200 copies that differ
only in a trailing comment. Each copy has a different code hash, so chi treats
it as a new candidate and evaluates it from scratch. I scored all 200 with
chi's real evaluator, `chi.eval.runner.evaluate`, on the `optimize_function`
pack. Each evaluation runs the correctness checks, then takes the median of 5
benchmark runs (`chi/eval/runner.py:112-144`). The machine was a 4-vCPU cloud
container, on 2026-11-22.

Same code, 200 scores:

| | runtime (ms) |
|---|---|
| fastest | 0.0745 |
| 10th percentile | 0.0935 |
| median | 0.105 |
| 90th percentile | 0.154 |
| slowest | 0.194 |

The typical spread of the 5 runs inside one evaluation was 0.028 ms, about a
quarter of the median. That is very noisy. This benchmark times one call that
takes a tenth of a millisecond, on a shared machine. A GPU kernel benchmark is
usually tighter. chi's NoiseGuard docstring quotes about 8% for a B200 kernel
(`chi/eval/noise.py:1-6`). Keep that in mind when you read the numbers below.

Now picture a loop that has truly run out of ideas, where every new candidate
is the same speed as the champion. Under a "stop after N tries with no new
best" rule, a lucky low score still counts as a new best and resets the
counter. Worse, that lucky score becomes the champion. At a patience of 10, by
the time the rule stopped, the champion sat a median 16% below the code's true
median, and later candidates had to beat a number the code can't reliably hit.

## Four stop rules, tested on real noise

I replayed the 200 real scores through four stop rules. Each trial draws 200
candidates at random from the pool, 2,000 trials per setting. A rule stops after
P candidates in a row that it doesn't count as progress.

1. **Any new best.** Progress is any score below the champion.
2. **Margin.** Progress must beat the champion by 0.5%. That's chi's default
   promote margin (`chi/director/review.py:45`).
3. **Noise band.** Progress must beat the champion by two times its measured
   spread, using the per-evaluation spread chi records as `noise_std`.
4. **Confirm.** A score that beats the champion by 0.5% gets 3 more
   measurements. It counts only if their median also beats the champion by
   0.5%, and then that *median* becomes the new champion score.

To see whether a rule quits too soon, I also needed loops that were still
improving. I made those by multiplying the same real scores by a fixed speedup
schedule. The noise is real, but the schedule is mine. For example: "5% faster
every 5 candidates until candidate 90, then flat." A rule that stops before
candidate 90 has quit on a loop that still had gains left.

## Which stop rule quit too early?

With patience P = 10, all four rules stopped before candidate 90 in over 90% of
improving trials, even at 5% every 5 candidates. Ten candidates is only two real
wins at that rate, and one lucky champion can hide both. So the window has to be
sized against both the noise and how often real wins arrive, and a small fixed
number is a guess.

At P = 20 the rules start to separate:

| Rule | Flat loop: median stop | 5% every 5: stopped early | 3% every 5: stopped early | 2% every 3: stopped early |
|---|---|---|---|---|
| Any new best | 32 | 24.9% | 60.2% | 54.1% |
| Margin 0.5% | 31 | 28.9% | 65.5% | 60.1% |
| Noise band (2σ) | 20 | 100% | 100% | 100% |
| Confirm (median of 3) | 26 | 9.2% | 31.4% | 27.1% |

Three things stand out.

**Every rule stops a flat loop.** Not one of the 2,000 flat trials ran to the
end, under any rule or window. Having a stop at all is the big win over NEVER
STOP. The plain rules took longer to stop because lucky draws kept resetting
the counter. "Any new best" logged a median of 3 wins on code that never
changed.

**The noise band is too strict.** With spread around a quarter of the score,
two sigma means a candidate's score must come in at roughly half the
champion's before it counts. It almost never counted a win (a median of zero per
trial), so it stopped at or just past P, even on loops that were improving.

**Confirmation helps most.** Re-measuring each apparent win and moving the
baseline to the confirmed median cut early stops by roughly half to two-thirds
compared with "any new best." It costs 3 extra evaluations per apparent win. I
didn't count how often that fired, but it only fires on a candidate that
already beat the champion.

One more variant matters for chi. I re-ran the confirm rule but kept the *raw*
lucky score as the new baseline, not the confirmed median. That's the shape of
chi's director today: once NoiseGuard confirms a win, the baseline becomes the
ledger's best score (`chi/director/loop.py:155-157`). At P = 20 that variant
stopped early in 35.8%, 72.9% and 65.2% of the three improving setups. That's
worse than no confirmation at all. The director doesn't stop on a flat metric,
so this doesn't end chi runs early. But it feeds the same baseline into the
round classifier that labels rounds `improving` or `stuck`
([how that classifier works](/blog/detect-agent-loop-plateau-rules/)). I
haven't measured that effect in a live director run.

## A saturation stop in about 20 lines

Here is the confirm rule, rewritten from my simulation as a plain function,
for a minimize metric. `remeasure`
re-benchmarks the candidate and returns one score.

```python
import statistics

def should_stop(scores, remeasure, patience=20, margin=0.005, n=3):
    """Yield (index, stopped) as scores arrive; stop after `patience`
    candidates with no confirmed improvement."""
    champ = statistics.median(remeasure(0) for _ in range(n))
    last_win = 0
    for i, s in enumerate(scores):
        if i == 0:
            continue
        if s < champ * (1 - margin):            # looks like a win
            m = statistics.median(remeasure(i) for _ in range(n))
            if m < champ * (1 - margin):        # still a win after re-measure
                champ, last_win = m, i          # baseline = confirmed median
        if i - last_win >= patience:
            yield i, True
            return
        yield i, False
```

Two more things I'd put around it in a real loop:

- **Halt loudly.** Write the reason to the run log and tell the operator,
  the way chi's dead-evaluator rule does (`loop.py:164-173`). A loop that
  stops quietly looks the same as a loop that crashed.
- **Count candidates, not wall-clock time.** Patience in hours depends on how
  fast the agents are. Patience in scored candidates is tied to the evidence
  the rule actually sees.

## Where this falls short

- **The improvement schedules are synthetic.** The noise comes from 200 real
  evaluations. The "5% every 5 candidates" gains are multipliers I picked. Real
  fleets improve in bursts, and I haven't tested a rule against a live LLM
  fleet's score history.
- **One noisy benchmark.** A spread of about a quarter of the score on a sub-millisecond Python timer
  is the hard end. At 8%, every rule would do better, and the noise band might
  stop being useless. I didn't measure that.
- **My re-measures differ from chi's.** In the simulation, a re-measure is
  another median-of-5 evaluation. chi's NoiseGuard for a local pack takes the
  median of 3 single benchmark runs (`chi/session/director_runner.py:27-57`).
  The raw-baseline result above shows the shape of the problem, not chi's exact
  numbers.
- **chi doesn't ship this.** Saturation stop is still an open gap. Until it
  lands, set a cost ceiling and check the round log in the morning. A long run
  of `stuck` with a flat best means it's time to stop by hand.

## Try the noise yourself

The noise is easy to see on your own machine, and you don't need an API key.
[Run your first autoresearch loop offline](/blog/first-autoresearch-loop-no-api-key/),
then re-run the same pack a few times and compare the baseline scores between
runs. If they spread more than your promote margin, your loop's "new bests"
are partly luck, and any stop rule you build has to plan for that.
