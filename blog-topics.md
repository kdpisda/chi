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
  status: published 2026-09-27 /blog/stop-coding-agent-looping/
  category: Deep dive
  funnel: TOFU
  query: "how to stop an AI coding agent from looping"
  angle: An agent's own "I'm done" is not a signal. Detect stalls from eval recency and repeated diff hashes, in code, with no model call.
  evidence: chi/orchestrator/watchdog.py, chi/config.py:31 (eval_recency_iters=10), angles.md `watchdog`, drafts/2026-09-13.md fact check

- id: benchmark-noise-agents
  status: published 2026-09-29 /blog/agent-benchmark-improvement-is-noise/
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
  status: published 2026-10-01 /blog/goodhart-law-ai-agents-benchmark/
  category: Deep dive
  funnel: TOFU
  query: "Goodhart's law AI agents benchmark proxy metric"
  angle: The score is a proxy with its own bias. The noisy_bench pack ranks an O(n) rewrite below the O(n²) baseline. A fleet optimizes the metric, not the goal.
  evidence: problems/noisy_bench, angles.md `proxymetric`, drafts/2026-09-20.md

- id: write-an-evaluator
  status: published 2026-09-28 /blog/write-an-evaluator-for-autoresearch/
  category: Guide
  funnel: MOFU
  query: "how to write an evaluator for autoresearch"
  angle: A good evaluator is three commands: a hard correctness gate on held-out seeds, a benchmark that prints one number, and a direction. Walk through problems/optimize_function line by line.
  evidence: website/content/docs/problems.md, problems/optimize_function/{problem.yaml,check.py,bench.py}

- id: negative-results-ledger
  status: published 2026-10-04 /blog/multi-agent-system-repeats-failed-approaches/
  category: Deep dive
  funnel: MOFU
  query: "multi-agent system repeats failed approaches"
  angle: Dead ends are first-class data, but an unscoped dead end can kill the winner. So a rule-out needs evidence and a scope, and agents can challenge it.
  evidence: chi/store/ledger.py, website/content/docs/concepts.md (ledger), angles.md `ledger`, drafts/2026-09-17.md

- id: blackboard-vs-agent-chat
  status: published 2026-10-08 /blog/multi-agent-shared-memory-vs-message-passing/
  category: Deep dive
  funnel: MOFU
  query: "multi-agent coordination shared memory vs message passing"
  angle: Agents that never talk to each other. A store keyed by code hash gives dedup for free and lets any agent be rebuilt from scratch, which answers context rot.
  evidence: chi/store/db.py, chi/eval/hashing.py, angles.md `blackboard`, drafts/2026-09-18.md

- id: rule-based-stuck-detection
  status: published 2026-10-11 /blog/detect-agent-loop-plateau-rules/
  category: Deep dive
  funnel: MOFU
  query: "detect when an agent loop has plateaued"
  angle: Classify each round as improving, plateaued or stuck with ~40 lines of explicit rules. The LLM's read is advisory, so the loop can't argue itself in circles.
  evidence: chi/director/review.py, docs/director.md, angles.md `classify`, drafts/2026-09-15.md

- id: autonomous-director-goal
  status: published 2026-11-13 /blog/run-coding-agent-unattended-overnight/
  category: Guide
  funnel: MOFU
  query: "run a coding agent unattended overnight with a budget"
  angle: Give the director one sentence ("get it under 500µs, don't spend over $5") and walk away. How rounds, self-stop and resume work.
  evidence: docs/director.md, chi/director/loop.py, website/content/docs/changelog.md v0.2.0 (self-stop)

- id: heterogeneous-fleet
  status: published 2026-10-29 /blog/use-multiple-llms-together-for-coding/
  category: Deep dive
  funnel: MOFU
  query: "use multiple LLMs together for coding"
  angle: Why a fleet mixes Claude, Codex, Grok and open models on purpose, and how adapters (vendor CLIs vs a LiteLLM tool loop) make that one config line.
  evidence: chi/agents/{cli_subprocess.py,litellm_loop.py,registry.py}, chi/providers/catalog.py, examples/fleet.yaml, angles.md `heterogeneous`

- id: sandbox-no-credentials
  status: published 2026-10-14 /blog/sandbox-ai-coding-agents-safely/
  category: Deep dive
  funnel: MOFU
  query: "sandbox AI coding agents safely"
  angle: The safest submit button is one the agent can't reach. Credentials are never mounted, eval can run in a jail, and ranked submissions stay manual.
  evidence: website/content/docs/security.md, chi/agents/sandbox.py, chi/eval/submission.py, angles.md `sandbox`, drafts/2026-09-23.md

- id: budgets-hard-caps
  status: published 2026-10-02 /blog/limit-llm-api-cost-coding-agents/
  category: Guide
  funnel: MOFU
  query: "limit LLM API cost for agents"
  angle: Dollar caps per run and per role, enforced by the harness rather than by asking the model to be frugal. What a real four-round run cost.
  evidence: chi/providers/budgets.py, website/content/docs/getting-started.md, angles.md `numbers` ($1.71, 812µs→636µs)

- id: steering-mid-run
  status: published 2026-09-30 /blog/steer-autonomous-coding-agent-mid-run/
  category: Guide
  funnel: MOFU
  query: "steer an autonomous coding agent while it runs"
  angle: Two-layer steering. Type a directive into a live run, and the coders hot-reload steering.md between iterations. No restart, no lost context.
  evidence: chi/orchestrator/steering.py, website/content/docs/concepts.md (steering), README.md session commands

- id: mechanism-not-wired
  status: published 2026-11-12 /blog/agent-harness-bug-postmortem-wiring/
  category: Case study
  funnel: TOFU
  query: "agent harness bug postmortem"
  angle: A mechanism in the repo isn't a mechanism on the path you ship. How a deliberately noisy pack showed NoiseGuard only ran under the director.
  evidence: angles.md `wiring`, drafts/2026-09-19.md, problems/noisy_bench, git log for the fix

- id: postmortem-hand-run-fleet
  status: published 2026-11-15 /blog/lessons-running-multi-agent-llm-fleet/
  category: Case study
  funnel: TOFU
  query: "lessons from running a multi-agent LLM fleet"
  angle: Four ways a hand-run multi-model fleet failed (dead ends, wasted evals, silent stalls, no steering), and the mechanism each failure turned into.
  evidence: docs/superpowers/specs/2026-07-25-chi-v1-design.md, docs/product-spec-v1.md (pain points), angles.md `postmortem`

- id: autoresearch-explained
  status: published 2026-10-07 /blog/what-is-autoresearch-ai-agents/
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
  status: published 2026-10-05 /blog/test-agent-harness-without-api-key/
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
  status: published 2026-10-03 /blog/getchi-0-2-0-release-notes/
  category: Release
  funnel: BOFU
  query: "getchi 0.2.0"
  angle: What 0.2.0 changed for someone who tried 0.1.0. Director self-stop, sandboxed eval, the offline demo, NoiseGuard for local evals, a verified export.
  evidence: website/content/docs/changelog.md, git log v0.1.0..v0.2.0

- id: naming-chi
  status: published 2026-10-09 /blog/install-getchi-chi-cli/
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

- id: director-cost-ceiling-accounting
  status: published 2026-10-06 /blog/agent-loop-cost-ceiling-stops-early/
  category: Deep dive
  funnel: MOFU
  query: "agent loop cost ceiling stops early"
  angle: The director sums the whole run's event costs each round and adds that to its own counter (chi/director/round.py:62-64, loop.py:92). Verify with a multi-round run whether the ceiling double-counts, then say what a user should set.
  evidence: chi/director/round.py, chi/director/loop.py, tests/test_director_loop.py:337, a multi-round director run with a scripted adapter ($0)

- id: dedup-race-fix
  status: open
  category: Release
  funnel: MOFU
  query: "sqlite unique constraint failed experiments dedup race agents"
  angle: Once the check-then-insert race in record_experiment (chi/store/ledger.py:29-31) is fixed on main, a short Release/Deep-dive post with before/after counts over the same 10-run repro (scratchpad: two coders, identical script).
  evidence: drafts/blog run 2026-10-08 (2 of 10 runs failed one agent), fix commit, rerun of the repro

- id: correctness-gate-exit-code
  status: published 2026-10-30 /blog/agent-passes-correctness-check-exit-code/
  category: Case study
  funnel: TOFU
  query: "coding agent pass correctness check exit code"
  angle: A candidate that exits 0 before the comparison passes chi's gate. Repro, cause, and a child-process checker pattern.
  evidence: chi/eval/runner.py returncode test, problems/optimize_function/check.py, repro 2026-10-30, drafts/2026-10-29.md

- id: autoresearch-on-test-suite
  status: published 2026-11-20 /blog/speed-up-slow-test-suite-ai-agent/
  category: Guide
  funnel: TOFU
  query: "speed up slow test suite with an AI coding agent"
  angle: A test-suite runtime is a clean score (seconds, lower is better) with a built-in correctness gate (tests pass). How to write that evaluator and where it goes wrong (flaky tests, cached runs).
  evidence: docs on evaluators, problems/optimize_function/check.py pattern, run a $0 offline pack to show the evaluator output. Must differ from /blog/write-an-evaluator-for-autoresearch/ by targeting the test-suite reader.

- id: read-the-run-store
  status: published 2026-11-04 /blog/inspect-coding-agent-run-history/
  category: Guide
  funnel: BOFU
  query: "inspect coding agent run history chi status ledger"
  angle: After a run, what chi status, chi ledger and the SQLite store tell you (champion, rejected candidates, reasons). Must go beyond /blog/test-agent-harness-without-api-key/.
  evidence: chi/store/, chi/cli.py, run examples/offline.yaml and quote the output

- id: resume-agent-run-after-crash
  status: published 2026-11-11 /blog/resume-coding-agent-run-after-crash/
  category: Guide
  funnel: MOFU
  query: "resume autonomous coding agent run after crash"
  angle: What state survives a killed run (store, ledger, champion) and how resume picks up. Verify in code and with a $0 kill-and-resume test before writing.
  evidence: docs/director.md, chi/director/loop.py, chi/store/ — needs a local kill/resume repro
  note: 2026-11-06 premise check. /resume reattaches and replays only (chi/session/engine.py:641). It does not restart a killed run. Reframe as "what a killed run leaves in the store" or drop.
  done: 2026-11-11 reframed per the 11-06 note. Post says plainly there is no in-place resume; shows a SIGKILL/SIGINT kill repro and restart-from-champion via chi champion --export.

- id: lucky-sample-target-stop
  status: published 2026-11-05 /blog/agent-loop-stops-lucky-benchmark-score/
  category: Deep dive
  funnel: TOFU
  query: "agent stops at target score lucky benchmark sample"
  angle: The director's target stop reads the raw ledger champion, not the NoiseGuard verdict. Repro and workarounds.
  evidence: chi/director/loop.py:72-80,102-115,176, chi/director/review.py:16-17, replay 2026-11-05

- id: kill-and-restart-fleet
  status: published 2026-11-21 /blog/multi-agent-fleet-crash-recovery/
  category: Case study
  funnel: MOFU
  query: "multi agent fleet crash recovery sqlite"
  angle: Follow-up to the crash post. Kill a two-coder scripted fleet mid-run and check whether the shared store and champions/ archive stay consistent (dedup, leases, partial rows).
  evidence: chi/orchestrator/loop.py _launch_fleet, chi/store/tasks.py expire_stale_leases (not called on the run path), a $0 two-coder scripted kill repro

- id: director-saturation-stop
  status: published 2026-11-22 /blog/agent-loop-stop-when-metric-stops-improving/
  category: Deep dive
  funnel: TOFU
  query: "agent loop keeps running after metric stops improving"
  angle: A per-agent watchdog stops a coder; nothing stops the loop above it when the score goes flat. What a saturation-aware stop needs (window, noise band, a loud halt like the dead-eval rule), shown against chi's director, where it is still open gap #6.
  evidence: chi/director/loop.py:89-194 (no flat-metric stop), chi/director/review.py classify_state, docs/autoresearch-gap-analysis.md gap 6, $0 director run 2026-11-13 (13 rounds, 12 stuck, 10 WATCHDOG_KILL, stopped only by request_stop). Best after a fix lands on main, else frame as design + open gap.

- id: director-without-brain
  status: published 2026-11-16 /blog/coding-agent-loop-without-llm-planner/
  category: Guide
  funnel: MOFU
  query: "autonomous coding agent loop without llm planner"
  angle: What chi's director still does with no brain (rule-based state, dead-class blocks, near-miss promotion) and what only the brain adds (research on stuck, a new strategy for one coder). How operator_cli wires it, and the sorted(strategies)[0] reassignment quirk.
  evidence: chi/director/strategy.py:207-250, chi/director/research.py:34-46, chi/session/engine.py:519-532, drafts/2026-11-03.md (strategypick replay), a $0 director run with a stub brain
  done: 2026-11-16 also found a failing brain CLI's stderr becomes the New direction label, and an exception in Strategist.plan (strategy.py:65, unwrapped) ends the director with handle.error never shown; near-miss block never fires (mark_near_miss only called in tests).

- id: director-brain-failure-fix
  status: open
  category: Release
  funnel: BOFU
  query: "llm planner call fails agent loop silently"
  angle: Once main wraps the strategist brain call, surfaces DirectorHandle.error in /director, and treats a nonzero CLI exit as no answer, a short before/after post with the same fake-CLI repro.
  evidence: chi/director/strategy.py:65, chi/session/operator.py:482-483, chi/session/director_runner.py:150-152, chi/session/engine.py director_status; fix commit; rerun of the 2026-11-16 fake-claude and raising-brain repros (scratch scripts drove SessionEngine.start_director with CHI_CONFIG_DIR + a fake claude on PATH)

- id: agent-edits-own-benchmark
  status: published 2026-11-17 /blog/coding-agent-edits-its-own-benchmark/
  category: Case study
  funnel: TOFU
  query: "coding agent edits its own benchmark"
  angle: The scorer lives in the agent's writable workdir. A candidate that rewrites bench.py on import becomes champion, and one that also rewrites holdout_bench.py gets a "generalizes" holdout verdict, because the holdout runs the candidate inside chi's private holdout dir.
  evidence: chi/orchestrator/loop.py:334-335,404, chi/eval/runner.py:34,137, chi/cli.py:282, chi/eval/holdout.py:178-184, chi/eval/sample.py:26, chi/agents/{cli_subprocess.py:25,litellm_loop.py:49,156-172}, $0 scripted repros 2026-11-17
  added: 2026-11-17 by the blog routine (not in backlog before; every other open topic failed rotation, evidence or cannibalization)

- id: evaluator-tamper-fix
  status: open
  category: Release
  funnel: BOFU
  query: "protect evaluator from coding agent tampering"
  angle: Once main scores from a read-only pack copy outside every workdir, re-copies holdout files per sample, hashes scoring files at run start, and stops chi eval rereading problem.yaml from the workdir, a before/after post rerunning the 2026-11-17 tamper repros.
  evidence: fix commit; rerun of the three scripted tamper candidates (bench.py only, bench.py + holdout_bench.py, problem.yaml correctness edit via chi eval)

- id: killed-run-sweep-fix
  status: open
  category: Release
  funnel: BOFU
  query: "agent run stuck in running status after crash"
  angle: Once main marks killed runs (or sweeps them on /resume or chi status) and calls expire_stale_leases, a short before/after post rerunning the 2026-11-21 fleet kill harness (28 kills; status running and tasks claimed in all of them today).
  evidence: chi/orchestrator/loop.py:447 (only place ended_at is set), chi/store/tasks.py:75 expire_stale_leases (no caller on the run path), fix commit, rerun of the scratch kill harness (two scripted coders, random SIGKILL/SIGINT, checks integrity, rows=events=mirror=archives, tasks, run status)

- id: plain-run-champion-noise
  status: published 2026-11-24 /blog/verify-coding-agent-speedup-is-real/
  category: Deep dive
  funnel: MOFU
  query: "best score from agent loop is just benchmark noise"
  angle: chi run picks its champion as the single best median in the ledger (ledger.champion, ORDER BY score_value LIMIT 1); the NoiseGuard re-measure lives in the director. In the 2026-11-21 fleet kills, 6 of 17 "champions" were still-quadratic variants 0.5-3.2 ms under a baseline that itself ranged 27.7-45.9 ms. Show when plain chi run vs the director protects you. Check overlap with /blog/agent-benchmark-improvement-is-noise/ first and take the plain-run-vs-director angle only.
  evidence: chi/store/ledger.py champion(), chi/director NoiseGuard code, a $0 offline run with repeated baselines
  done: 2026-11-24 published under query "verify a coding agent's speedup is real" (narrower than the original query, to stay clear of /blog/agent-benchmark-improvement-is-noise/). Scripted identical and ~3.9%-slower variants on optimize_function; 24 of 30 plain runs exported a fake champion.

- id: director-raw-baseline-after-confirm
  status: published 2026-11-26 /blog/noisy-benchmark-hides-real-improvements/
  category: Deep dive
  funnel: MOFU
  query: "noise guard baseline lucky score agent loop"
  angle: After NoiseGuard confirms a win, chi's director sets prev_best to the raw ledger best (chi/director/loop.py:155-157), not the confirmed median. In the 2026-11-22 stop-rule simulation that variant stopped early more often than no confirmation at all. Measure the effect on classify_state labels in a $0 director run on noisy_bench before writing; frame as a fix if one lands.
  evidence: chi/director/loop.py:102-110,155-157, chi/eval/noise.py, chi/session/director_runner.py:27-57, 2026-11-22 sim (confirm vs confirm_raw), a $0 director run on problems/noisy_bench
  added: 2026-11-22 by the blog routine
  done: 2026-11-26 published under query "noisy benchmark hides real improvements". Hypothesis mostly refuted in a live director run: median-baseline patch moved improving labels 127 -> 131 of 220; score.repeats 5 -> 148; 10% steps -> 177. Reframed around false plateaued/stuck labels on real progress.

- id: flaky-benchmark-timeout
  status: published 2026-11-25 /blog/flaky-benchmark-timeout-agent-loop/
  category: Case study
  funnel: TOFU
  query: "flaky benchmark timeout in an agent loop"
  angle: A benchmark timeout records the candidate correct with a null score, and evaluate() serves that row from the code-hash cache forever, so a one-off host stall removes the run's best candidate. A correctness timeout caches a correct candidate as incorrect. Workarounds: post-run query, retry inside the entrypoint, timeout headroom.
  evidence: chi/eval/runner.py:70-78,95-100,122-128, chi/eval/hashing.py, chi/store/schema.sql (no detail column), angles.md staleresult, drafts/2026-10-08.md, $0 scripted repros 2026-11-25 (flaky_host wrapper)
  added: 2026-11-25 by the blog routine (yesterday was Deep dive/MOFU; every open topic failed rotation, waited on an unlanded fix, or cannibalized chi-vs-pi)

- id: stale-result-fix
  status: open
  category: Release
  funnel: BOFU
  query: "agent loop retries failed benchmark"
  angle: Once main stops serving a cache hit whose score is null (or retries benchmark/correctness timeouts) and stores the eval failure reason, a short before/after post rerunning the 2026-11-25 flaky_host repro (stall on call 7: champion = baseline, 7 bench calls; correctness stall: correct 0, seeds [11]).
  evidence: chi/eval/runner.py:70-78,95-100,122-128, fix commit, rerun of the flaky_host stall repro (scratch pack copy of optimize_function, timeout_seconds 10, scripted adapter resubmitting the accumulate candidate)
  added: 2026-11-25 by the blog routine

- id: dry-run-problem-before-tokens
  status: published 2026-11-27 /blog/dry-run-agent-benchmark-before-spending-tokens/
  category: Guide
  funnel: BOFU
  query: "dry-run an agent benchmark before spending tokens"
  angle: chi validate is a schema check. Zero seeds, repeats 0, negative tolerance, a misspelled key and deleted scripts all print OK. A three-candidate scripted dry run (baseline copy, deliberate wrong answer, known-good) through the real evaluator catches the worst of them at $0. With seeds [], the wrong answer became champion.
  evidence: chi/cli.py:69-80, chi/config.py:97,145-153, chi/eval/runner.py:90-108,122-143, angles.md validateschema, $0 scripted dry runs 2026-11-27
  added: 2026-11-27 by the blog routine (yesterday was Deep dive/MOFU; every open topic failed rotation, waited on an unlanded fix, or cannibalized chi-vs-pi; no BOFU post in the last 7 days)

- id: validate-semantic-checks-fix
  status: open
  category: Release
  funnel: BOFU
  query: "chi validate checks problem files"
  angle: Once chi validate refuses zero seeds, repeats < 1, unknown keys and missing entrypoint/candidate files (or chi run stops on a null baseline score), a short before/after post rerunning the 2026-11-27 broken-copy table and scripted dry runs.
  evidence: chi/cli.py:69-80, chi/config.py (ProblemConfig extra handling), chi/eval/runner.py:90-108,122-143, fix commit, rerun of the five broken optimize_function copies
  added: 2026-11-27 by the blog routine

- id: maximize-signed-metric
  status: published 2026-11-28 /blog/maximize-metric-coding-agent-loop/
  category: Deep dive
  funnel: MOFU
  query: "maximize a metric with a coding agent loop"
  angle: direction maximize is honored by every score comparison, but the evaluator drops any sample <= 0 regardless of direction (runner.py:137, sample.py:39), so a signed metric (percent faster than reference) leaves a run with no baseline and no champion. Three pack copies (throughput, percent, ratio) replayed offline; fix is a positive-by-construction ratio.
  evidence: chi/eval/runner.py:133-140, chi/eval/sample.py:39, chi/store/ledger.py:56-64, chi/eval/noise.py, chi/director/review.py:31-41, chi/eval/autosubmit.py:58-63, chi/eval/holdout.py:81-87, scripted runs on optimize_function copies
  added: 2026-11-28 by the blog routine (from angles.md maximizesign; X post 10-28 text not reused; yesterday was Guide/BOFU, so gap-analysis-roadmap (Release, no newly shipped evidence) was passed over again)
