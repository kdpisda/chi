---
title: "An AI agent CLI can be installed and still not work"
description: "shutil.which checks PATH, not your account, model, or flags. Here is the one-shot probe chi runs first, and the exact account error it was built to catch."
date: 2026-09-26T02:39:21Z
draft: false
category: "Guide"
tags: ["coding agents", "CLI", "autoresearch", "reliability", "fleets"]
toc: true
newsletter:
  subject: "My CLI was installed. It still failed every call."
  preheader: "shutil.which checks PATH. It doesn't check your account, your model, or your flags."
  body: |
    `which codex` returns a path. That path can still fail on the very first call, because the CLI is installed but the account behind it can't use the model you asked for.

    That's what happened in one of chi's own dogfood runs: one CLI's account rejected every model, a second was getting the wrong flags, and both only surfaced mid-run. So chi grew a one-shot probe that catches this before a fleet starts, not after it wastes a run.

    The post has the exact error text, the code that classifies it, and a live run of the probe against my own machine.
---

`which codex` prints a path. `which claude` prints a path. Every coding agent CLI
your fleet needs is right there on `PATH`. None of that tells you whether any of
them will actually produce output when chi calls them for real — with your
account, your model choice, and the exact flags chi passes.

One of chi's own dogfood runs found this the expensive way. One installed CLI
had an account that rejected every single model chi tried to call through it.
A second CLI was getting the wrong command-line flags from chi. Neither
failure crashed the run — both just surfaced mid-run, after chi had already
spent iterations assuming both coders were working.

## "Installed" only means `PATH` has an entry

`chi run` drives each vendor CLI through an [adapter](/docs/concepts/) —
`cli_subprocess` for a headless CLI like codex or grok, `json_stream` for
claude's structured streaming mode. Before either one runs, the only check
chi (or you, with plain `which`) had was presence on `PATH`. That check
answers "is a binary there," not "does it run for this user." An account on
the wrong plan, a stale login token, or a
CLI flag that changed between versions all pass a `which` check and then fail
on every single invocation.

The fix chi shipped is
[`chi/providers/substrate.py`](https://github.com/kdpisda/chi/blob/main/chi/providers/substrate.py),
and its own docstring names the exact failure that motivated it:

> Today's dogfood wasted a run because codex is installed but its ChatGPT
> account rejects every model ("not supported when using Codex with a ChatGPT
> account"), and grok's command template was wrong — both discovered only
> mid-run.

## What the probe actually sends

`probe_substrate` runs one real, cheap call per CLI: a prompt that asks for a
single word back.

```python
# chi/providers/substrate.py
_PROBE_PROMPT = "Reply with exactly the single word: PONG"

_PROBE_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p", _PROBE_PROMPT],
    "codex": ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
              _PROBE_PROMPT],
    "grok": ["grok", "-p", _PROBE_PROMPT],
}

_ACCOUNT_FAIL = ("not supported when using", "invalid_request_error",
                 "unauthorized", "not authenticated", "please run", "login")
```

These are the probe's own commands, simpler than the real headless templates
`chi run` uses per iteration (`chi/providers/catalog.py`'s `CLI_SUBSTRATES`) —
the probe only needs *any* reply, not a working session. It classifies the
result three ways:

- **exit 0 and the reply contains "pong"** → `ok`, detail `"responds"`.
- **nonzero exit, or the output matches one of the `_ACCOUNT_FAIL` signatures**
  → not ok, detail names the exit code or the matched signature, e.g.
  `"account/config rejected (not supported when using)"`.
- **exit 0, no error signature, but no "pong" either** → treated as `ok`,
  detail `"ran (reply unconfirmed)"`. More on why that third case matters below.

`tests/test_substrate.py` pins the exact failure the probe was built for,
using the real error text from that dogfood run:

```python
def test_codex_account_rejection_detected() -> None:
    # the exact failure from today's dogfood
    err = ('ERROR: {"error":{"message":"The \'gpt-5.3-codex\' model is not '
           'supported when using Codex with a ChatGPT account."}}')
    s = probe_substrate("codex", runner=_runner("", rc=1, stderr=err))
    assert not s.ok and "account/config rejected" in s.detail
```

## Run it

It's exposed as a flag on `chi providers`, documented in [Getting
started](/docs/getting-started/#non-interactive-use):

```sh
chi providers --probe
```

I ran it against this machine, which has `claude` on `PATH` and nothing else:

```
probing installed CLI substrates (runs each once)…
OK   claude     responds
```

One real subprocess call, one real reply, in about two seconds. On a machine
with `codex` installed under a ChatGPT-only account, the same command prints
`FAIL codex account/config rejected (not supported when using)` instead — a
few seconds of output you can read before you decide to point a fleet at that
CLI, instead of an hour into a run you can't see.

## Where this doesn't help

- **It's opt-in.** Grepping the codebase for callers of `probe_substrate` or
  `probe_all` turns up exactly one: the `--probe` flag in
  [`chi/cli.py`](https://github.com/kdpisda/chi/blob/main/chi/cli.py#L99-L111).
  `chi run` does not call it automatically. You have to remember to run
  `chi providers --probe` yourself before an unattended fleet, the same way
  you'd remember to run `chi validate` on a new `fleet.yaml`.
- **It only knows three substrates.** `claude`, `codex`, and `grok` are the
  hardcoded CLI adapters in `CLI_SUBSTRATES`. A model routed through
  `litellm_loop` (any LiteLLM-reachable API model) isn't a CLI at all, so it
  isn't probed here — that path is checked instead by `chi ping --fleet`,
  which calls each distinct model once and prints latency, cost, and any
  error.
- **A silent wrong answer still passes.** The third branch above —
  exit 0, no error text, no "PONG" — is scored `ok` with `"ran (reply
  unconfirmed)"`. That's deliberate: a CLI that wraps or truncates its output
  shouldn't be marked broken on a false negative. But it also means a CLI that
  runs, exits clean, and quietly ignores the prompt will read as healthy.
- **Results are cached per process**, so a token that expires mid-run, after
  the probe already said `OK`, won't be caught until the next call actually
  fails.

None of that makes the probe useless — it turns two whole classes of "installed
but broken" into a one-line answer before your budget is on the line. It just
isn't a guarantee that stays valid for the length of an overnight run.

If you skip the probe, or a CLI breaks after it, the run isn't defenseless
either. chi's [watchdog](https://github.com/kdpisda/chi/blob/main/chi/orchestrator/watchdog.py)
counts iterations a coder goes without producing an eval, and a coder whose
CLI errors instantly on every call is exactly that: zero evals, iteration
after iteration. Once that streak reaches `eval_recency_iters` (default `10`,
`chi/config.py:31`) the watchdog kills or mutates that coder instead of
letting it burn the rest of the run doing nothing. It's a real backstop, but a
slower and blunter one — ten wasted iterations against zero, and a kill
instead of a flag naming the exact account or flag problem.

## Try it before your next fleet run

If you're about to point a fleet at a real problem, run the probe first:

```sh
uv tool install getchi
chi providers --probe
chi run examples/offline.yaml   # $0, no keys, to see the full loop first
```

The [adapters and blackboard store](/docs/concepts/) doc covers how each
substrate feeds the same run store once it's confirmed working, and [Getting
started](/docs/getting-started/) has the rest of the non-interactive
commands for scripts and CI.
