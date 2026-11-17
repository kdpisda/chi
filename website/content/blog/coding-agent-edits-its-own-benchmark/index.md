---
title: "Can a Coding Agent Edit Its Own Benchmark? I Tested It"
description: "Can a coding agent edit its own benchmark? In chi, a candidate that rewrote bench.py on import became champion, then fooled the holdout gate too. Repro inside."
date: 2026-11-17T02:34:31Z
draft: false
category: "Case study"
tags: ["evaluation integrity", "reward hacking", "holdout", "autoresearch", "LLM agents"]
toc: true
newsletter:
  subject: "My holdout gate said 'generalizes'. The algorithm hadn't."
  preheader: "One file write on import, two fake samples, and a 100% verdict."
  body: |
    I wanted to know whether a candidate in chi could rewrite the scripts that score it. It can. Unchanged O(n^2) code, plus a few lines that overwrite bench.py when it is imported, became champion at 0.001 ms against a 35 ms baseline.

    That matters if you leave a loop running: the agent works in the same directory as its own scorer, and the only thing keeping it out is a line in the prompt.

    The part I didn't expect: the holdout gate, the check I built for exactly this kind of fake win, called the same trick "generalizes" once the candidate also targeted the held-out script. The post has every run, why it happens, and what I'd check in any harness.

    [Read the repro](https://getchi.dev/blog/coding-agent-edits-its-own-benchmark/) before you trust a number your agent produced overnight.
---

Your agent loop scores each candidate by running a script. The agent can write
files. If that script sits in a directory the agent can write to, the score is
only as honest as the agent's willingness to leave it alone. So can a coding
agent edit its own benchmark? I tested that in chi, my open-source autoresearch
harness, with a candidate written to cheat.

**Autoresearch** here means a loop where an agent edits code, a harness scores
it, and the best correct version is kept. In chi the scoring is two commands
from a problem pack: a correctness check (`check.py`) and a benchmark
(`bench.py`).

## Can a coding agent edit its own benchmark?

Yes, in chi today. The whole problem pack, including `check.py`, `bench.py`,
`reference.py` and `problem.yaml`, is copied into each agent's workdir
([`loop.py:334`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py#L334),
and again per coder at
[`loop.py:404`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py#L404)).
Only files declared as holdout are left out. The evaluator then runs both
commands with that workdir as the current directory
([`runner.py:34`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L34)).

Nothing enforces "don't touch the scorer". The prompt for vendor CLI agents
says "Edit ONLY `candidate.py`"
([`cli_subprocess.py:25`](https://github.com/kdpisda/chi/blob/main/chi/agents/cli_subprocess.py#L25)),
and the LiteLLM tool loop says the same
([`litellm_loop.py:49`](https://github.com/kdpisda/chi/blob/main/chi/agents/litellm_loop.py#L49)).
But that loop's `write_file` tool accepts any path inside the workdir
([`litellm_loop.py:156-172`](https://github.com/kdpisda/chi/blob/main/chi/agents/litellm_loop.py#L156-L172)),
and a vendor CLI with a shell can write anything the user can. Its Docker
sandbox tiers still mount the workdir read-write, scorer included.

The candidate doesn't even need the agent's help. Python runs module-level code
on import, and `check.py` imports the candidate before `bench.py` runs.

## The repro: unchanged code, a 0.001 ms champion

I wrote one candidate by hand. It keeps the shipped O(n²) `solve()` and adds
this at the top:

```python
from pathlib import Path

_bench = Path("bench.py")
if _bench.exists() and "0.001" not in _bench.read_text():
    _bench.write_text('import json\nprint(json.dumps({"score": 0.001}))\n')
```

I replayed it through chi's `scripted` adapter (no model, no API key) against
`problems/optimize_function`, with one iteration. Today's run:

```text
"baseline_score": 34.92908999999145,
"champion_score": 0.001,
"status": "done",
"holdout": null
```

The ledger row for that candidate says `correct: 1`, all three seeds passed,
`noise_std: 0.0`. Five benchmark repeats all printed the same fake number, so
taking the median didn't help. The workdir's `bench.py` is now two lines. The
repo copy is untouched, since the run works on a copy.

chi does reject a score of zero or below, or one that isn't finite
([`runner.py:137`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L137)).
That check came from a real dogfood candidate that froze the benchmark's
clock. A small positive number passes it.

## Can it skip the correctness check too?

On one path, yes. Vendor CLI agents score their work by running `chi eval`, and
that command reloads `problem.yaml` from the workdir on every call
([`cli.py:282`](https://github.com/kdpisda/chi/blob/main/chi/cli.py#L282)). I
edited the workdir's `problem.yaml` by hand, the way an agent with a shell
could, and changed the correctness entrypoint to `{python} -c pass`. Then I
evaluated a candidate whose `solve()` returns an empty list:

```text
{"correct": true, "seeds_passed": [11, 27, 43], "score_value": 0.001, ...}
```

The scripted and LiteLLM adapters use the `problem.yaml` loaded at run start
([`loop.py:335`](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/loop.py#L335)),
so this specific edit doesn't reach them. Rewriting `check.py` would, by the
same import trick as above. I didn't run that variant.

This is a different hole from the one in my
[exit-code post](/blog/agent-passes-correctness-check-exit-code/), where a
candidate ended the checker early. The child-process checker I suggested there
doesn't help if the checker file itself can be rewritten.

## Does the holdout gate catch it?

The **holdout gate** re-scores a champion on a second workload whose script
never enters an agent workdir, then compares the gain it claimed with the gain
it realised ([docs/holdout.md](https://github.com/kdpisda/chi/blob/main/docs/holdout.md)).
I built it for [benchmark overfitting](/blog/coding-agent-overfits-benchmark/),
and I expected it to catch this too.

Against the `overfit_demo` pack, which declares a holdout, the same candidate
was flagged:

```text
"verdict": "overfit",
"claimed_gain_pct": 99.99702952900658,
"holdout_gain_pct": 5.398631090423883,
"detail": "claims +100.0% on the benchmark but realises only +5.4% held out"
```

The 5.4% is run-to-run noise on identical code, below the gate's 50% floor.
The label says "overfit" where "tampered" would be truer, but the warning fires.

Then I changed one line, so the candidate rewrites `holdout_bench.py` too if it
finds one in its current directory. The name isn't secret: the `holdout:` block
stays in the workdir's `problem.yaml` on purpose, to tell agents a holdout
exists. The result:

```text
"verdict": "generalizes",
"baseline_holdout": 207.13714900000468,
"champion_holdout": 0.001,
"samples": [202.34806599998478, 0.001, 0.001],
"detail": "claims +100.0%, realises +100.0% held out (100% of the claim)"
```

`chi champion` on that run reports the same `generalizes` verdict.

## Why the private holdout directory didn't help

The holdout files never enter an agent workdir. That part works. But to score
a candidate, chi copies the candidate into its private holdout directory and
runs the script there, with that directory as the current directory
([`holdout.py:178-184`](https://github.com/kdpisda/chi/blob/main/chi/eval/holdout.py#L178-L184),
[`sample.py:26`](https://github.com/kdpisda/chi/blob/main/chi/eval/sample.py#L26)).
The docs put it as "the candidate travels to the evaluator". So the candidate's
import-time code runs next to `holdout_bench.py`, with write access to it.

The first sample was honest, because the script was already running when the
candidate replaced it. Samples two and three ran the replacement. The gate
takes the median of three, so two fakes out of three decided it.

The held-out workload is hidden from the agent. It is not protected from the
code under test. Those are different properties, and I had only built the first.

## What to check in your own harness

The fix is the same in any loop: code under test must not be able to write the
files that judge it. Questions worth asking of your harness:

- Does the scoring script live in a directory the agent or candidate can write?
- Does the harness reload its config (entrypoints, thresholds) from that
  directory on each eval?
- Does any held-out check run with the candidate in the same directory as the
  held-out script?
- Is "don't edit the evaluator" a prompt instruction, or something the file
  system enforces?

That last one is common. Karpathy's autoresearch, as of November 2026, lists
"Modify `prepare.py`. It is read-only." under "What you CANNOT do" in its
[`program.md`](https://github.com/karpathy/autoresearch/blob/master/program.md),
and keeps the evaluation in that file. That is the same kind of guard as chi's
"Edit ONLY". I haven't tested whether that file is writable in practice there,
so I'm not claiming it is.

One mitigation I did test: move the scoring scripts out of the pack. I put
`check.py`, `bench.py` and `reference.py` in a separate directory and pointed
the entrypoints at their absolute paths. The same tampering candidate then
scored 33.2 ms against a 34.7 ms baseline, a real measurement of unchanged code.
It still became champion on that roughly 4% gap, which is
[benchmark noise](/blog/agent-benchmark-improvement-is-noise/), a separate
problem.

That mitigation has limits I haven't tested past. The path is still visible in
`problem.yaml`, and an agent running as your user can write there if it goes
looking. chi's opt-in Docker eval sandbox mounts only the workdir
([`sandbox.py:68`](https://github.com/kdpisda/chi/blob/main/chi/agents/sandbox.py#L68)),
so from reading the code, an outside path won't exist in the container.

## Where this falls short

I wrote the cheating candidate myself. I haven't seen an agent do this in a real
chi run. But the loop rewards a number and gives agents write access to the
code that produces it, so I treat this as a design flaw, not a curiosity.

None of these are fixed on `main` today. chi's
[SECURITY.md](https://github.com/kdpisda/chi/blob/main/SECURITY.md) says
candidates that fail held-out seeds can never become champion, and that holds
only while `check.py` and `problem.yaml` are untouched. The changes I think chi
needs: score from a read-only copy of the pack that's outside every workdir,
re-copy the holdout files for each sample, hash the scoring files at run start
and refuse to score on a mismatch, and stop `chi eval` from rereading config
from the workdir. That's roadmap, not shipped. Known gaps live in the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

Every run in this post used the scripted adapter and cost $0. Timings are from
one machine on one day, and they move a few percent from run to run.

## Try the honest version first

If you haven't run chi yet, start with the loop working as designed. The
[offline walkthrough](/blog/first-autoresearch-loop-no-api-key/) runs a real
autoresearch loop and the holdout gate in about ten seconds, no API key. Then
look at your own scorer and ask who can write to it.
