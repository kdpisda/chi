---
title: "Detect When an Agent Loop Has Plateaued, Without an LLM"
description: "How to detect when an agent loop has plateaued: three states, one short function, no model call. Walk through chi's round classifier and where it misfires."
date: 2026-10-11T02:34:37Z
draft: false
category: "Deep dive"
tags: ["autoresearch", "director", "loop reliability", "coding agents", "negative results"]
toc: true
newsletter:
  subject: "A plateau is two flat rounds, not a feeling"
  preheader: "The rule that decides your agent loop is stuck, and the case where it fires too early."
  body: |
    New deep dive: how to detect when an agent loop has plateaued without asking the model.

    chi's director runs the fleet in rounds, and after each one a short function labels the round improving, plateaued or stuck. The model's opinion is advisory. The rule decides, so the loop can't talk itself into "one more try".

    I ran the function against eight hand-built round histories and against a real run's negative ledger. One result surprised me: "plateaued" lasts exactly one round at the default setting, and a flat round right after a win can read as stuck.

    The post has the table, the code, and the limits.

    [Read how the classifier works](https://getchi.dev/blog/detect-agent-loop-plateau-rules/)
---

You gave an agent loop a goal and walked away. Two hours later the score hasn't moved, and you can't tell whether it is about to break through or going in circles. Asking the model is the obvious move, and it is the wrong one: a model that has been failing for two hours will usually say it's making progress.

chi's answer is to not ask. Its director runs the fleet in rounds and, after each round, a small function labels the loop `improving`, `plateaued` or `stuck` from numbers in the run store. This post walks through that function, shows what it returns on eight round histories I ran, and covers two places where it does something you might not expect.

## What counts as a plateau in an agent loop?

In chi, a plateau is a round that did not beat the best score from the last confirmed improvement by the promote margin, so the metric is flat. "Stuck" is stronger: the loop is flat and also out of ideas, or is repeating ones that were already ruled out. The difference matters because they trigger different things. Only `stuck` fires the research call, one web-capable model call that asks for genuinely different techniques ([director docs](https://github.com/kdpisda/chi/blob/main/docs/director.md); `chi/director/loop.py:132`).

The classification is the director's step 3. The [director section of the concepts page](/docs/concepts/) lists all five steps (run, review, classify, research, steer). This post is about the third.

## What does the classifier read?

A deterministic digest built from the SQLite run store, with no model in the path (`chi/director/review.py:13-28`):

- **The champion score**: the best correct experiment so far (`chi/store/ledger.py:58-66`).
- **Dead classes**: each approach a coder recorded as ruled out in the negative ledger, with a count of how many times (`chi/store/ledger.py:86-92`). A class with a count of 2 or more is a repeated dead class. A class with a count of exactly 1 is counted as "new".
- **The previous best**: the score at the last *confirmed* improvement, which the director carries between rounds.

That last one is subtle. The director only advances the baseline when a round is classified `improving` (`chi/director/loop.py:155-159`). And before an apparent win is believed, [NoiseGuard](/blog/agent-benchmark-improvement-is-noise/) re-benchmarks it, and a refuted win is demoted to `plateaued` (`chi/director/loop.py:104-110`). So "flat" means flat against the last *verified* best, not against last round's lucky number.

The negative ledger is the same one that stops a fleet from [repeating failed approaches](/blog/multi-agent-system-repeats-failed-approaches/). Here it doubles as a plateau signal.

## The rules, in order

`classify_state` is one function of about 25 lines (`chi/director/review.py:44-68`). In order:

1. **Improving.** The round's best beats the baseline by the promote margin, 0.5% by default (`chi/config.py:118`). For a minimize problem that is `best < prev * (1 - 0.5/100)`. Return `improving`.
2. **Repeated dead class.** Some approach class has been ruled out two or more times. Return `stuck`.
3. **No new classes.** The last `stuck_k` rounds, this one included, all had zero classes ruled out exactly once. Return `stuck`.
4. **Perma-plateau.** The last `stuck_k` rounds were all non-improving. Return `stuck`.
5. Otherwise, `plateaued`.

`stuck_k` defaults to 2 (`chi/director/loop.py:28`), and I found nothing in the session code that overrides it.

Rule 4 has a comment in the source worth quoting, because it records a bug that was fixed: a fleet can try a *new* approach every round, each one below the improvement margin, and so never repeat a dead class and never run out of new ones. Rules 2 and 3 never fire, the loop sits at "plateaued" forever, and research never runs. Rule 4 is the escalation.

## What does it return on real round histories?

I fed hand-built round digests to the real `classify_state` with `stuck_k=2`. These are synthetic histories I wrote to probe the rules, not rounds from a live fleet. The scores are made up (636 is the number the code comments use for a kernel timing). The date is 2026-10-11, and the output is identical when run against the `getchi==0.2.0` wheel from PyPI.

```text
A: 5.7% better                 -> improving
B: 0.3% better (<0.5%)         -> plateaued
C: flat, 1 new class           -> plateaued
D: flat, 0 new, 1st round      -> plateaued
E: flat, 0 new, 2nd round      -> stuck
F: flat, new class, 2nd rd     -> stuck
G: repeated dead class         -> stuck
H: win after flat rounds       -> improving
```

Case B is the margin at work: a 0.3% gain is below 0.5%, so it is not a win. Case H is the one I care about most. A genuine win after two flat rounds returns `improving`; the history can't mask it, and there is a regression test for exactly that (`tests/test_director_review.py`).

Then the store-backed path. I started an offline run (`$0`, scripted coder, no API key), recorded dead ends through the same `ledger.add_negative` call the agents use, and built the digest from the store each time:

```text
no dead ends yet   | best 43.14 dead [] repeated []              -> plateaued
bf16 ruled out once  | best 43.14 dead ['bf16'] repeated []      -> plateaued
bf16 ruled out twice | best 43.14 dead ['bf16'] repeated ['bf16'] -> stuck
```

The score did not move across those three lines. Only the ledger changed, and the label went from `plateaued` to `stuck` on the second recording of the same class.

## Where does it do something unexpected?

Two things, both visible in the table and in the code.

**"Plateaued" is short.** At the default `stuck_k=2`, case F says two consecutive non-improving rounds are `stuck` even when the fleet is still trying a new class each round. So `plateaued` really only covers the first flat round. If you expected a long "plateaued, still exploring" phase before anything fires, the default doesn't give you one. Raising `stuck_k` does: with `stuck_k=3`, two flat rounds stay `plateaued` and three are `stuck` (I ran that too). But `stuck_k` is a constructor argument on `Director`, and I didn't find a config key or session flag for it.

**A flat round right after a win can read as stuck.** I ran a win in round N (600 against a 636 baseline) followed by a flat round N+1 where no class was ruled out. The result was `stuck`. Rule 3 doesn't care that a round in its window was an improvement; it only counts classes. If a run's agents don't record dead ends, which is plausible for an agent that doesn't call the ledger, rule 3 can fire one round after a win. With one class recorded in each round, the same history returns `plateaued`.

Neither is a crash. The cost of a false `stuck` is one extra research call, which is cheap next to a fleet spinning for hours. But it is a heuristic with a bias toward acting, and you should know which way it leans.

## Where this falls short

- **It trusts the ledger.** Rules 2 and 3 are only as good as the dead ends the agents record, and `dead_classes` counts every entry regardless of status. An agent that never writes to the ledger leaves rule 3 with nothing to see.
- **Noise lives elsewhere.** `classify_state` and `build_digest` both accept `noise_band_pct`, and `classify_state` also takes a `plateau_window`, but none of them is read in the function body. Noise handling is NoiseGuard's job in the loop, not the classifier's.
- **I did not run a live director.** Everything above is the classifier against synthetic digests and one real offline run's ledger. I did not run a multi-hour LLM fleet to see how often `stuck` fires there, and I'm not going to guess.
- **It decides when, not what.** It tells the director to research and re-steer. Whether the new ideas are any good is a separate question, and the known gaps are listed in the [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

The older watchdog is a different mechanism at a different level: it kills one looping *agent* inside a round, and is a [narrow backstop](/blog/stop-coding-agent-looping/) rather than a general stuck-detector. The classifier works on rounds, across the fleet.

## Try it

You can call the same function yourself, with no API key, from the PyPI package:

```sh
uv run --with getchi python -c "
from chi.director.review import classify_state
from chi.director.types import RoundDigest
d = RoundDigest(round_index=0, best_score=636, champion_score=636, prev_best=636,
                repeated_dead_classes=['bf16'])
print(classify_state(d).value)   # stuck
"
```

The [concepts page](/docs/concepts/) covers the rest of the director loop: what a round does, how to supervise it, and how it stops itself on a target or a budget.
