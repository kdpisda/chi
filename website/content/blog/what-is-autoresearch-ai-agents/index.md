---
title: "What Is Autoresearch? The AI Agent Loop, Where It Breaks"
description: "Autoresearch is an AI agent that edits code, scores it, keeps the best and repeats overnight. How the loop works, where it breaks, and what a harness adds."
date: 2026-10-07T02:31:51Z
draft: false
category: "Guide"
tags: ["autoresearch", "coding agents", "karpathy autoresearch", "agent loops", "evaluation integrity"]
toc: true
newsletter:
  subject: "The agent loop that fits in one Markdown file"
  preheader: "What autoresearch is, and the three spots where an overnight loop quietly goes wrong."
  body: |
    Autoresearch is a loop: an AI agent edits code, a script scores the result, the better version is kept, and it repeats while you sleep. Karpathy's reference version fits in three files and one Markdown prompt.

    If you run agent loops against a benchmark, this one is a plain explainer of what the loop is, and where it breaks once you leave it alone for a night: noisy scores, a score that stops meaning anything, and an agent that stops making progress.

    I measured the spread on chi's own baseline benchmark, six runs of identical code. The gap between the best and the worst run is bigger than a lot of "improvements" people celebrate. [Read the post](https://getchi.dev/blog/what-is-autoresearch-ai-agents/) for the numbers and the checklist.
---

You have a benchmark, a coding agent and a night to spare. You want to wake up to a faster kernel, a smaller bundle or a better score. "Autoresearch" is the name that stuck for that setup: let an agent run experiments on its own and keep what works.

This post explains what autoresearch is, using the reference implementation, then shows where the loop breaks when nobody is watching.

## What is autoresearch?

**Autoresearch is a loop in which an AI agent proposes a code change, an evaluator scores it, the change is kept only if the score improved, and the cycle repeats without a human in it.** The human's job moves from editing code to defining the score and writing the instructions the agent follows.

The name comes from [karpathy/autoresearch](https://github.com/karpathy/autoresearch) (README fetched October 7, 2026). Its README describes giving an agent "a small but real LLM training setup" and letting it experiment overnight: it modifies the code, trains for 5 minutes, checks if the result improved, keeps or discards, and repeats. In that repo, the three files that matter are:

- `prepare.py`: fixed constants, data prep and the evaluation. The agent may not modify it.
- `train.py`: the one file the agent edits.
- `program.md`: the instructions for the agent. The human edits this one.

The metric is `val_bpb` (validation bits per byte), lower is better. The README gives the arithmetic: a fixed 5-minute budget means about 12 experiments an hour, so about 100 while you sleep.

## What does one pass of the loop do?

The instructions live in [`program.md`](https://github.com/karpathy/autoresearch/blob/master/program.md). Paraphrasing its "experiment loop" section, each pass is:

1. Edit `train.py` with an idea, and `git commit`.
2. Run the experiment and read the metric out of the log.
3. Log the result as `keep`, `discard` or `crash` in an untracked `results.tsv`.
4. If `val_bpb` is lower, keep the commit. If it is equal or worse, `git reset` back.

Two details do a lot of work. The evaluator is read-only, so the agent can't "improve" the score by editing the thing that measures it. And the instructions say never to stop to ask the human. The loop runs until you interrupt it.

That is a good design for its job, which is a single GPU, a single file and a single metric. The rest of this post is about what changes when you point the same idea at your own code for a whole night.

## Where does an autoresearch loop break?

I see three failure points, in the order they usually bite.

### Is the improvement real, or noise?

The reference loop, as written in `program.md`, decides keep-or-revert from one run's number. That is fine when the metric is stable. Many are not.

Here is the benchmark for chi's bundled `optimize_function` problem, run six times on the unchanged baseline (`python3 bench.py candidate.py` in `problems/optimize_function`, October 7, 2026, in a cloud container):

```
45.38  42.68  42.73  44.48  43.39  43.04    (ms)
```

The best and worst run differ by about 6%, with identical code. A change that "wins" by 3% on a single run can't be told apart from noise. I didn't measure `val_bpb` noise on a GPU, and a fixed-seed training run may be much quieter. The point is to measure your own metric before you trust a keep-if-lower rule on it.

chi's answer is a `NoiseGuard` that re-benchmarks an apparent winner `n` times and requires the median to beat the champion by a margin
([`chi/eval/noise.py:25-60`](https://github.com/kdpisda/chi/blob/main/chi/eval/noise.py#L25-L60)). One catch I documented earlier: it runs under the director, not under a plain `chi run`. The details are in [Is your agent's benchmark improvement just noise?](/blog/agent-benchmark-improvement-is-noise/)

### Is the score still measuring what you want?

Run a loop long enough against one benchmark and the agent optimizes that benchmark, including its quirks. A candidate can get faster on the exact input the benchmark uses and gain nothing elsewhere. The reference setup limits this by making the evaluator untouchable, but that protects the *measurement*, not the *choice of what is measured*.

chi has an opt-in holdout check: score the champion again on a different workload that agents never see, then report whether the gain generalizes. It is on `main`, and the 0.2.0 wheel on PyPI doesn't contain it. The walkthrough is in [Why your coding agent overfits the benchmark](/blog/coding-agent-overfits-benchmark/).

### Does the loop stall without telling you?

An agent can run for hours and make no measurable progress: the same edit again, or long stretches with no evaluation at all. A model's own "still working on it" is not evidence. In chi, a deterministic watchdog counts repeated candidate hashes. With the default `repeat_k` of 3, it tells the agent to change approach at the third identical candidate and kills the iteration at the sixth
([`chi/orchestrator/watchdog.py:85-91`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/watchdog.py#L85-L91),
[`chi/config.py:30`](https://github.com/kdpisda/chi/blob/main/chi/config.py#L30)). No model call is involved. More in [How to stop an AI coding agent from looping](/blog/stop-coding-agent-looping/).

## What does a harness add to the loop?

A harness is the code around the loop that makes it safe to leave alone. From the failures above, and from what I hit running a multi-model fleet by hand, that comes to:

| Problem | What the harness does |
|---|---|
| Noisy scores | Re-benchmark a winner, compare medians, require a margin |
| Overfit to the benchmark | Score the champion on a held-out workload |
| Silent stalls | Count repeated candidates and no-eval stretches in code |
| Repeated dead ends | Record ruled-out ideas, with evidence, for the next round |
| Wrong answers promoted | A correctness gate: only candidates that pass can become champion (`chi/store/ledger.py:58-66`) |
| Surprise bills | Budget tracking per run (see the [caveats on caps](/blog/limit-llm-api-cost-coding-agents/)) |

None of this is needed for a one-hour experiment with a stable metric. It starts to matter when the loop runs unattended, when several agents share it, or when you plan to act on the result.

## Where this falls short

chi is a young project (0.2.0 is the current release) and it has real gaps. It wants a problem pack with `problem.yaml`, a single editable candidate file, a correctness script and a benchmark script. Wrapping an existing repo is the biggest adoption cost, and the gap list says so openly:
[`docs/autoresearch-gap-analysis.md`](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md). If your problem is "make my test suite faster this afternoon", the [comparison post](/blog/chi-vs-pi-autoresearch-vs-karpathy-autoresearch/) will point you somewhere lighter.

## Can I see one without an API key?

Yes. chi ships an offline demo in which only the model is replaced by a script. I ran it today (`chi run examples/offline.yaml`, chi from `main`, October 7, 2026):

```
"iterations": 3,
"baseline_score": 43.52589099998738,
"champion_score": 0.07822099999543752,
"total_cost_usd": 0,
"status": "done"
```

The evaluator, the correctness gate, the store and the champion selection are all real, and the spend is $0. The full walkthrough, including what the script fakes, is in [Run your first autoresearch loop with no API key](/blog/first-autoresearch-loop-no-api-key/). Install with `uv tool install getchi` and try it on your own machine.
