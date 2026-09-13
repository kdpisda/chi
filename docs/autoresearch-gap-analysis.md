# What chi doesn't do that it should

**Date:** 2026-09-13
**Method:** read `pi-autoresearch` (the pi.dev extension), the `oh-my-pi`/`ohmypi`
orchestration layers, `karpathy/autoresearch` itself, two curated ecosystem
indexes (~90 descendant projects), and the community's own post-mortems —
practitioner write-ups, the Cerebras and Amazon Science analyses, and press
coverage of the pattern's most-cited result. Then compared against chi's source.

This is a **competitive gap analysis, not a feature copy**. Several items below
are things chi should do *because* the ecosystem has proven they matter; a few
are things nobody does yet, which is where chi's leverage is.

## Where chi already leads

Worth stating first, because it decides what's worth building. Neither pi nor
pi-autoresearch nor ohmypi has: a parallel multi-agent fleet with per-agent
worktrees, a SQLite blackboard with cross-agent dedup, a first-class
negative-results ledger, a deterministic watchdog, gated/rationed leaderboard
submission, or an autonomous director that meta-reviews and re-steers. pi is a
single-agent coding substrate; `pi-autoresearch` is a single-agent experiment
loop over it; ohmypi is a role-routing layer. chi's orchestration is genuinely
differentiated (see `docs/pi-substrate-adoption.md` for the substrate-layer
mechanisms already adopted from pi).

## The gaps, ranked

### 1. No held-out validation of the *score* — **✅ SHIPPED (this change)**

**The gap.** chi held out correctness (candidates never see reference outputs)
but scored every candidate on the same benchmark the fleet optimises against.
Nothing detected a champion that moved the benchmark without getting faster.

**Why it's #1.** This is the ecosystem's most-repeated failure and the one thing
essentially no harness gates on. The most-cited autoresearch result — a 53%
speedup from ~120 experiments — carries its own author's *"probably somewhat
overfit"* and went unmerged. A widely shared test-suite win measured 163s → 100s
locally and 14min → 13min in CI. Independent surveys of ~90 descendant projects
conclude the real gap "isn't generating research artifacts (most systems can)
but catching weak ones before they ship." Cerebras documented a loop that
abandoned its intended experiment entirely; Karpathy's repo keeps an uneditable
`prepare.py` specifically so the evaluator can't be gamed.

**What shipped.** `docs/holdout.md` — an opt-in `holdout:` block scoring a
different workload whose files never enter an agent workdir, a
claimed-vs-realised generalization ratio, `overfit`/`regressed`/`generalizes`
verdicts, and a director that won't let an overfit champion satisfy a target
score. This is also the gap best matched to chi's existing shape: it already had
two-tier eval, a NoiseGuard, and rails around irreversible actions.

### 2. No zero-config mode for an existing repo — **highest adoption cost**

**The gap.** chi requires authoring a problem pack: a directory with
`problem.yaml`, a single `candidate.py`, a `check.py`, and a `bench.py`.
pi-autoresearch runs on any repo after writing two shell scripts in `.auto/`:
`measure.sh` (prints `METRIC name=number`) and an optional `checks.sh`. That is
the difference between "optimise my test suite this afternoon" and "port my
project into a harness first."

**Why it matters.** chi's single-editable-file model is inherited from the
kernel/leaderboard problems it was built for. Most reported wins in the wild are
repo-shaped: test-suite time, build time, bundle size, Lighthouse scores. chi
cannot express any of them today.

**Shape of the work.** A repo-mode problem backend: `measure` + `checks`
commands, keep/revert against git rather than a candidate file, and a scope
allowlist of editable paths. This is a real change to chi's core model — the
candidate stops being one file — so it wants its own design pass. **Biggest
single lever on adoption; deliberately not attempted here.**

### 3. No git-backed experiment history, no reviewable output

**The gap.** chi archives champion bytes by content hash and exports one file.
pi-autoresearch commits every experiment and ships an `autoresearch-finalize`
skill that regroups successful experiments into independent, reviewer-friendly
branches off the merge-base.

**Why it matters.** The consistent complaint about autoresearch output is
reviewability, not correctness: practitioners report "the code changes were ugly
and needed significant cleanup," an independent reviewer called the Liquid PR's
code quality "just bad," and an MSR 2026 study of 403 AI-agent commits found the
Maintainability Index fell in 56.1% of cases and cyclomatic complexity rose in
42.7%. A harness whose output can't be reviewed produces unmerged PRs — which is
exactly what happened to the pattern's flagship result.

**Shape of the work.** Per-experiment commits on a run branch, plus a `chi
finalize` that partitions the champion's diff into independently-reviewable
commits with the ledger evidence for each in the message.

### 4. Thin structured reasoning per experiment

**The gap.** chi's negative ledger records ruled-out *classes* with evidence —
genuinely more than most tools have. But individual experiments store no
`hypothesis` / `learned` / `next_focus`, which pi-autoresearch logs per run. The
Strategist therefore reasons over scores and dead classes, not over what each
agent believed it was testing.

**Why it matters.** It's cheap, it improves the director's digest, and it makes a
run auditable after the fact ("why did it try this?").

### 5. Confidence is binary and only computed on apparent winners

**The gap.** The NoiseGuard is median-of-N with a pass/fail verdict, fired only
when a candidate looks like a win. pi-autoresearch shows a continuous confidence
score (improvement vs. noise, via median absolute deviation) on every run, with
traffic-light thresholds.

**Why it matters.** A standing noise estimate tells you whether the *problem* is
measurable at all before you burn a night on it. chi computes `noise_std` per
eval already and throws it away at the reporting layer.

### 6. No saturation-aware stop

**The gap.** chi self-stops on a target score or a cost ceiling. It does not stop
because the metric has stopped moving. A practitioner report describes a loop
that "stayed mechanically healthy yet kept burning compute after metric
saturation" — 11 days on an already-solved benchmark.

**Why it matters.** chi's director is explicitly run-until-stopped, which makes
this chi's failure mode too. The dead-eval detector already proves the pattern
(halt loudly on a structural dead end); saturation is the same idea one level up.

### 7. No dashboard or exportable run report

**The gap.** pi-autoresearch has an always-visible results table, a fullscreen
overlay, and `/autoresearch export` for a browser dashboard with charts. chi has
a live transcript and `chi status` / `chi ledger` JSON.

**Why it matters.** Lower value than 1–3 — but a run report is how an overnight
run gets shared with the people who decide whether to merge it, and chi's store
already holds strictly more than pi's JSONL.

### 8. No iteration hooks

**The gap.** pi-autoresearch runs optional `before.sh` / `after.sh` per
iteration (notifications, learnings journals, idea rotation). chi has steering
and the operator, but no user-supplied code at iteration boundaries.

## Where this leaves chi

The ecosystem has converged on the loop itself; chi's differentiation was never
the loop, it was the rails around it. Gap 1 was the rail nobody had built, it is
the most-cited weakness of every competing tool, and it is now shipped. Gap 2 is
the biggest adoption lever and the clearest next design pass. Gaps 3–8 are
ordinary product work, ranked above.

## Sources

- [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch) · [pi.dev packages](https://pi.dev/packages/pi-autoresearch)
- [ohmypi](https://github.com/ajjucoder/ohmypi) · [oh-my-pi](https://github.com/can1357/oh-my-pi)
- [karpathy/autoresearch](https://github.com/karpathy/autoresearch)
- [awesome-autoresearch](https://github.com/webfuse-com/awesome-autoresearch) · [yibie/awesome-autoresearch](https://github.com/yibie/awesome-autoresearch)
- [Tech Times: the loop is spreading fast; Shopify's 53% claim flagged as overfit](https://www.techtimes.com/articles/316804/20260519/karpathys-autoresearch-loop-spreading-fast-shopifys-53-speed-claim-still-unmerged-flagged.htm)
- [Cerebras: how to stop your autoresearch loop from cheating](https://www.cerebras.ai/blog/how-to-stop-your-autoresearch-loop-from-cheating)
- [Amazon Science: why don't ML research agents overfit?](https://www.amazon.science/blog/why-dont-machine-learning-research-agents-overfit)
- [quanttype: speed up code with pi-autoresearch](https://quanttype.net/p/speed-up-code-with-pi-autoresearch/)
- Horikawa et al., *Maintainability of AI agent commits*, MSR 2026 (arXiv:2603.13723)
