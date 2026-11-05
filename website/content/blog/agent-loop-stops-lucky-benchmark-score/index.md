---
title: "Agent Loop Stops at Target Score on One Lucky Benchmark Run"
description: "An agent loop with a target score can stop on one lucky benchmark sample. I reproduced it in chi's director, with the code path and what to do about it."
date: 2026-11-05T02:31:24Z
draft: false
category: "Deep dive"
tags: ["director", "noise", "loop reliability", "evaluation", "coding agents"]
toc: true
newsletter:
  subject: "The noise guard said no. The loop stopped anyway."
  preheader: "How a 'stop at this score' goal can end a run on one lucky sample."
  body: |
    New deep dive: why an agent loop with a target score can stop on one lucky benchmark run.

    I gave chi's director a target of 500 and a baseline of 636. One candidate scored a lucky 480. The noise guard re-ran it three times, got a median near 640 and refuted the win. The director logged the round as plateaued, then stopped with "target reached".

    If you set a goal and walk away, that is the stop you will wake up to. The post has the 30-line repro, the exact lines of code that cause it, and what I would do until it is fixed.

    [Read the repro](https://getchi.dev/blog/agent-loop-stops-lucky-benchmark-score/)
---

You told your agent loop "get it under 500 microseconds, then stop" and went to bed. In the morning it says the target was reached. Was it? If your benchmark has any run-to-run spread, one lucky sample can end the run, even when you have a noise check in front of the stop condition.

I reproduced this in chi's director, the loop that runs a fleet of coding agents in rounds. This post shows the repro, the lines of code behind it, and what I would do about it today.

## Can an agent loop stop on a lucky benchmark sample?

Yes, in chi today. The director's target check reads the raw best score in the run's ledger. It does not read the noise guard's verdict. (The noise guard, in one sentence: when a round looks like an improvement, it re-runs the benchmark a few times and only believes the win if the median of those runs beats the old champion by a margin. I covered the idea in [Is Your LLM Agent's Benchmark Improvement Just Noise?](/blog/agent-benchmark-improvement-is-noise/).)

The two checks sit side by side in `Director.run`. The noise guard can demote a round to `plateaued` ([`loop.py:102-110`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L102-L110)). The target check then runs on `digest.best_score` ([`loop.py:176`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L176)). That value comes from `build_digest`, which takes the best correct row in the ledger ([`review.py:16-17`](https://github.com/kdpisda/chi/blob/main/chi/director/review.py#L16-L17), [`ledger.py` `champion`](https://github.com/kdpisda/chi/blob/main/chi/store/ledger.py)). A refuted lucky row is still the best correct row. Demoting the round changes the label, not the ledger.

## What does it look like when it happens?

I drove the real `Director`, `build_digest`, ledger and `NoiseGuard` with a scripted runner and a fake benchmark, so it costs $0 and takes a second. Two ledger rows arrive, 636 and then 480, with a target of 500 (lower is better). The noise guard's three re-runs return 640, 636 and 650. I ran it on `main` on 2026-11-05:

```text
--- lucky: rows 636 then 480, guard samples 640/636/650
round 0: plateaued · best 636.0 · Σ 1 benches $0.00
round 1: plateaued · best 480.0 · noise_verified false · Σ 5 benches $0.00
✓ director stopped: target reached: 480.0 meets 500.0
```

Read round 1 carefully. The guard's median is 640, which is no better than the 636 baseline, and the round says `noise_verified false` and `plateaued`. The director stops on the same round anyway, with a champion of 480 that three re-runs said it could not reproduce.

The control is the same script with guard samples of 481, 479 and 485:

```text
--- genuine: rows 636 then 480, guard samples 481/479/485
round 0: plateaued · best 636.0 · Σ 1 benches $0.00
round 1: improving · best 480.0 · noise_verified true · Σ 5 benches $0.00
✓ director stopped: target reached: 480.0 meets 500.0
```

Same stop, as it should be. The two runs end identically. The only difference is the state label and `noise_verified`, and nothing downstream reads them. The repro is a short script: it inserts the rows with `ledger.record_experiment`, wraps a list of numbers in a `NoiseGuard`, and passes `target_score=500.0` to `Director`. I'm not shipping it as a test yet, because what the stop should do here is a design decision (see below).

## Doesn't the holdout gate catch this?

Not for this case. The holdout check is the other veto on a target stop: if the latest holdout verdict says the champion is not shippable, `_target_met` returns false ([`loop.py:72-80`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L72-L80)). But the holdout only runs on a round the noise guard confirmed ([`loop.py:115`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L115): `if noise_verified and self._holdout_check is not None`). A refuted round never reaches it, so `last_holdout` stays empty and the veto has nothing to veto with.

The holdout answers a different question anyway. It asks whether a real speedup carries over to inputs the fleet never optimized against, which is the overfitting case from [How to catch a coding agent that overfits its benchmark](/blog/coding-agent-overfits-benchmark/). A lucky sample is not an overfit. It is a measurement that does not repeat.

## Where this falls short

Two limits on what I showed:

- **Setup.** This is a replay with scripted scores, not a field incident. I have not seen a real overnight run end this way. What I verified is the code path: the stop reads a value the guard cannot change. How often it fires depends on your benchmark's spread and on how close your target sits to your baseline, and I haven't measured either on a real fleet.
- **The guard has gaps of its own.** It only runs on rounds classified `improving` and only when there is a previous best to compare to ([`loop.py:103-104`](https://github.com/kdpisda/chi/blob/main/chi/director/loop.py#L103-L104)). So a lucky sample in the very first scored round is never re-checked at all. I ran that too (one ledger row of 480, target 500): `round 0: plateaued · best 480.0`, then `target reached`, with no guard run and no `noise_verified` field.

The known gaps in chi live in the public [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md). This one is not in it yet.

## What should I do until the stop is fixed?

I haven't changed the stop condition, so these are workarounds, not fixes:

1. **Put the target outside the noise.** If your benchmark moves about 8% between identical runs (the spread chi's own code comments cite for a B200 kernel benchmark; yours will differ, so measure it), a target within 8% of the baseline is reachable by luck. Pick one that a lucky sample is unlikely to cross.
2. **Re-measure the champion yourself.** When the director says "target reached", run the benchmark on the exported candidate a few times before you believe it. It is the same re-run the guard would have done, with a verdict you can see.
3. **Read the last round's line.** `noise_verified false` next to a "target reached" stop is the signature of this case. The round lines and the stop reason are in the run's event log; [what chi's store records](/blog/inspect-coding-agent-run-history/) shows how to read them.

The fix I would make is small: have the target check require the same verified score the guard produces, or skip the stop on a round the guard refuted. That changes what "reached" means for a user, so I'd rather say it out loud than slip it in.

## Next step

If you run goal-driven loops, the thing to take away is that a stop condition and a verification step are two separate pieces of code, and nothing forces them to agree. Check yours. To see chi's director and noise guard behave on a $0 run before you trust either, follow [the offline demo](/blog/first-autoresearch-loop-no-api-key/).
