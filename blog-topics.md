# chi blog topic backlog

Read by the daily blog routine (`.claude/skills/daily-blog/SKILL.md`). It picks
the first `open` topic that passes its rotation, cannibalization and evidence
checks, then marks it `published`. Add topics by hand anywhere in the list. Keep
new ones in the same shape.

Each topic has an `id` (stable), a `category` (Guide · Deep dive · Comparison ·
Release · Case study), a `funnel` stage, the `query` a real engineer would search
for, the `angle` (the one idea the post argues), and `evidence` (where the facts
come from). A topic without checkable evidence isn't ready.

## Who we write for

- **The agent-loop builder.** Already runs Claude Code, Codex or a homegrown
  loop against a benchmark, often overnight. Pain: loops that stall, "wins" that
  are noise, bills that surprise. Searches for fixes to those failures.
- **The perf/ML engineer with a score.** Has a kernel, a test suite or a model
  with a number to push (GPU kernel leaderboards, Kaggle-style evals, latency
  SLOs). Wants to know whether autoresearch works on *their* problem.
- **The autoresearch-curious.** Saw Karpathy's autoresearch or pi-autoresearch
  and wants to know what's different and what breaks at scale.

## Pillars

1. **Loop reliability**: stalls, loops, stuck detection, steering.
2. **Evaluation integrity**: noise, overfitting, proxy metrics, reward hacking.
3. **Autoresearch in practice**: evaluators, problem packs, budgets, fleets.
4. **Safety**: sandboxing, credential isolation, gated submissions.
5. **Ecosystem**: honest comparisons, where the field is going.
6. **Build in public**: releases, postmortems, real runs.

Funnel mix to aim for over any 7 days: about 3 TOFU (problem-first, chi appears
late), 3 MOFU (how chi solves it), 1 BOFU (install, get started, compare).

## Where new topics come from

- `angles.md` and the X log `posts.jsonl`: every angle can carry a long-form post.
- `docs/autoresearch-gap-analysis.md`: each shipped gap is a Release post, and
  each open gap is an honest "what's missing" post.
- `git log` on `main`: new `feat`/`fix` commits and releases (`website/content/docs/changelog.md`).
- `docs/superpowers/specs/` and `docs/product-spec-v1.md`: the failure modes chi
  was designed against.
- Questions and replies on the X account, and GitHub issues.

## Backlog

- id: stop-agent-looping
  status: open
  category: Deep dive
  funnel: TOFU
  query: "how to stop an AI coding agent from looping"
  angle: An agent's own "I'm done" is not a signal. Detect stalls from eval recency and repeated diff hashes, in code, with no model call.
  evidence: chi/orchestrator/watchdog.py, chi/config.py:31 (eval_recency_iters=10), angles.md `watchdog`, drafts/2026-09-13.md fact check

- id: benchmark-noise-agents
  status: open
  category: Deep dive
  funnel: TOFU
  query: "LLM agent benchmark improvement is noise"
  angle: Most "wins" in an agent loop are inside the benchmark's own spread. Re-run, take the median, and require a margin before promoting.
  evidence: chi/eval/noise.py, angles.md `noiseguard` (636/652/686µs), problems/noisy_bench, drafts/2026-09-16.md

- id: overfit-benchmark-holdout
  status: published 2026-09-25 /blog/coding-agent-overfits-benchmark/
  category: Case study
  funnel: TOFU
  query: "coding agent overfits benchmark"
  angle: Show a candidate that scores 99.7% faster on the benchmark's exact input and gains nothing on a held-out workload, and how a holdout gate flags it.
  evidence: docs/holdout.md, chi/eval/holdout.py, `chi run examples/holdout.yaml` ($0), examples/holdout_overfit.json and holdout_honest.json, drafts/2026-09-24.md

- id: proxy-metric-bias
  status: open
  category: Deep dive
  funnel: TOFU
  query: "Goodhart's law AI agents benchmark proxy metric"
  angle: The score is a proxy with its own bias. The noisy_bench pack ranks an O(n) rewrite below the O(n²) baseline. A fleet optimizes the metric, not the goal.
  evidence: problems/noisy_bench, angles.md `proxymetric`, drafts/2026-09-20.md

- id: write-an-evaluator
  status: open
  category: Guide
  funnel: MOFU
  query: "how to write an evaluator for autoresearch"
  angle: A good evaluator is three commands: a hard correctness gate on held-out seeds, a benchmark that prints one number, and a direction. Walk through problems/optimize_function line by line.
  evidence: website/content/docs/problems.md, problems/optimize_function/{problem.yaml,check.py,bench.py}

- id: negative-results-ledger
  status: open
  category: Deep dive
  funnel: MOFU
  query: "multi-agent system repeats failed approaches"
  angle: Dead ends are first-class data, but an unscoped dead end can kill the winner. So a rule-out needs evidence and a scope, and agents can challenge it.
  evidence: chi/store/ledger.py, website/content/docs/concepts.md (ledger), angles.md `ledger`, drafts/2026-09-17.md

- id: blackboard-vs-agent-chat
  status: open
  category: Deep dive
  funnel: MOFU
  query: "multi-agent coordination shared memory vs message passing"
  angle: Agents that never talk to each other. A store keyed by code hash gives dedup for free and lets any agent be rebuilt from scratch, which answers context rot.
  evidence: chi/store/db.py, chi/eval/hashing.py, angles.md `blackboard`, drafts/2026-09-18.md

- id: rule-based-stuck-detection
  status: open
  category: Deep dive
  funnel: MOFU
  query: "detect when an agent loop has plateaued"
  angle: Classify each round as improving, plateaued or stuck with ~40 lines of explicit rules. The LLM's read is advisory, so the loop can't argue itself in circles.
  evidence: chi/director/review.py, docs/director.md, angles.md `classify`, drafts/2026-09-15.md

- id: autonomous-director-goal
  status: open
  category: Guide
  funnel: MOFU
  query: "run a coding agent unattended overnight with a budget"
  angle: Give the director one sentence ("get it under 500µs, don't spend over $5") and walk away. How rounds, self-stop and resume work.
  evidence: docs/director.md, chi/director/loop.py, website/content/docs/changelog.md v0.2.0 (self-stop)

- id: heterogeneous-fleet
  status: open
  category: Deep dive
  funnel: MOFU
  query: "use multiple LLMs together for coding"
  angle: Why a fleet mixes Claude, Codex, Grok and open models on purpose, and how adapters (vendor CLIs vs a LiteLLM tool loop) make that one config line.
  evidence: chi/agents/{cli_subprocess.py,litellm_loop.py,registry.py}, chi/providers/catalog.py, examples/fleet.yaml, angles.md `heterogeneous`

- id: sandbox-no-credentials
  status: open
  category: Deep dive
  funnel: MOFU
  query: "sandbox AI coding agents safely"
  angle: The safest submit button is one the agent can't reach. Credentials are never mounted, eval can run in a jail, and ranked submissions stay manual.
  evidence: website/content/docs/security.md, chi/agents/sandbox.py, chi/eval/submission.py, angles.md `sandbox`, drafts/2026-09-23.md

- id: budgets-hard-caps
  status: open
  category: Guide
  funnel: MOFU
  query: "limit LLM API cost for agents"
  angle: Dollar caps per run and per role, enforced by the harness rather than by asking the model to be frugal. What a real four-round run cost.
  evidence: chi/providers/budgets.py, website/content/docs/getting-started.md, angles.md `numbers` ($1.71, 812µs→636µs)

- id: steering-mid-run
  status: open
  category: Guide
  funnel: MOFU
  query: "steer an autonomous coding agent while it runs"
  angle: Two-layer steering. Type a directive into a live run, and the coders hot-reload steering.md between iterations. No restart, no lost context.
  evidence: chi/orchestrator/steering.py, website/content/docs/concepts.md (steering), README.md session commands

- id: mechanism-not-wired
  status: open
  category: Case study
  funnel: TOFU
  query: "agent harness bug postmortem"
  angle: A mechanism in the repo isn't a mechanism on the path you ship. How a deliberately noisy pack showed NoiseGuard only ran under the director.
  evidence: angles.md `wiring`, drafts/2026-09-19.md, problems/noisy_bench, git log for the fix

- id: postmortem-hand-run-fleet
  status: open
  category: Case study
  funnel: TOFU
  query: "lessons from running a multi-agent LLM fleet"
  angle: Four ways a hand-run multi-model fleet failed (dead ends, wasted evals, silent stalls, no steering), and the mechanism each failure turned into.
  evidence: docs/superpowers/specs/2026-07-25-chi-v1-design.md, docs/product-spec-v1.md (pain points), angles.md `postmortem`

- id: autoresearch-explained
  status: open
  category: Guide
  funnel: TOFU
  query: "what is autoresearch"
  angle: A plain-language explainer of the autoresearch loop (edit, evaluate, keep the best, repeat), where it works, where it breaks, and what a harness adds. Must not duplicate /blog/what-is-chi-autoresearch-harness/, which is about chi.
  evidence: karpathy/autoresearch README (fetch and date it), docs/autoresearch-gap-analysis.md (sources)

- id: when-not-to-use-chi
  status: open
  category: Comparison
  funnel: BOFU
  query: "pi-autoresearch alternative"
  angle: An honest decision guide. Which tool fits "make my test suite faster this afternoon" and which fits "push a leaderboard score for a week". Where chi loses today.
  evidence: docs/autoresearch-gap-analysis.md, the existing comparison post, pi-autoresearch README (fetch and date it)

- id: offline-demo-internals
  status: open
  category: Guide
  funnel: BOFU
  query: "test an agent harness without an API key"
  angle: What the scripted adapter replays, what's real in the run (evaluator, store, champion, watchdog), and how to read the run store afterwards with chi status and chi ledger.
  evidence: chi/agents/scripted.py, examples/offline.yaml, examples/demo_candidates.json, run it ($0). Must go deeper than /blog/first-autoresearch-loop-no-api-key/

- id: gap-analysis-roadmap
  status: open
  category: Release
  funnel: MOFU
  query: "chi autoresearch roadmap"
  angle: The public gap list, ranked. What shipped (holdout gate), what's next (zero-config repo mode, git-backed history), and why that order.
  evidence: docs/autoresearch-gap-analysis.md, website/content/docs/changelog.md

- id: v0-2-0-release
  status: open
  category: Release
  funnel: BOFU
  query: "getchi 0.2.0"
  angle: What 0.2.0 changed for someone who tried 0.1.0. Director self-stop, sandboxed eval, the offline demo, NoiseGuard for local evals, a verified export.
  evidence: website/content/docs/changelog.md, git log v0.1.0..v0.2.0

- id: naming-chi
  status: open
  category: Guide
  funnel: BOFU
  query: "getchi chi cli install"
  angle: Short and useful. It's pronounced "kai", it installs as getchi, it runs as chi, and it isn't the Go router. Install paths, first command, where the docs are.
  evidence: README.md, website/static/llms.txt, angles.md `naming`, pyproject.toml

- id: cli-substrate-probe
  status: published 2026-09-26 /blog/agent-cli-installed-but-not-working/
  category: Guide
  funnel: MOFU
  query: "AI agent CLI installed but not working"
  angle: shutil.which only checks PATH, not the account or command template. Walk through chi's one-shot probe, the exact dogfood error it was built to catch, and the watchdog backstop for CLIs it doesn't cover.
  evidence: chi/providers/substrate.py, tests/test_substrate.py, chi/cli.py:99-111, chi/orchestrator/watchdog.py, chi/config.py:31, website/content/docs/getting-started.md, website/content/docs/concepts.md, angles.md `substrate`, drafts/2026-09-25.md, chi providers --probe @ 2026-09-26 (live run)
