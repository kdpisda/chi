---
title: "How to Sandbox AI Coding Agents Safely: Two Docker Tiers"
description: "Sandbox AI coding agents without fooling yourself. What chi's docker and docker-cli tiers mount, what each still exposes, and why the default is no sandbox."
date: 2026-10-14T02:32:34Z
draft: false
category: "Deep dive"
tags: ["security", "sandbox", "coding agents", "autoresearch", "fleets"]
toc: true
newsletter:
  subject: "The agent that can't submit has no credential to leak"
  preheader: "Two Docker tiers, one default of no sandbox, and a shim I call a speed bump."
  body: |
    A coding agent with a shell can do whatever your user can do. In my own dogfood runs, chi's agents fired leaderboard benchmarks on their own, which is how I learned to stop trusting the agent to behave.

    So I wrote down exactly what each of chi's sandbox options protects. I printed the Docker command for both tiers, ran the PATH shim against a fake CLI, and found where it stops: call the real binary by its full path and it goes straight through.

    The safer tier has no network. The tier that lets a vendor CLI work has network and a read-only credential, and the repo says so plainly. [Read the breakdown](https://getchi.dev/blog/sandbox-ai-coding-agents-safely/).
---

A coding agent has a shell. Whatever your user can do, it can do: read `~/.ssh`,
call the CLI you logged into last month, push a ranked submission that
you can't take back. Most advice on how to sandbox AI coding agents safely
stops at "run it in a container", which leaves out what the container
can still reach.

This post is the version I wanted when I built chi, an open-source harness
that runs a fleet of coding agents against an evaluator. I'll cover what the
default does (nothing), what chi's two opt-in Docker tiers mount, what each
one still exposes, and the one control that is a speed bump and not a
sandbox. Everything below I read from the code or ran on 2026-10-14.

## How do you sandbox an AI coding agent safely?

Decide what the agent must not be able to reach, then make it physically
absent from the agent's environment. A rule the agent is asked to follow is
not a boundary. A credential that was never mounted is.

For chi that comes down to three levels. By default chi runs the agent on the
host, with the permissions of whoever launched it
([`SECURITY.md`](https://github.com/kdpisda/chi/blob/main/SECURITY.md)). That
is the right choice for problems you wrote yourself and the wrong one for
anything else. Two opt-in tiers set `sandbox:` on a coder in `fleet.yaml`
to run the agent's command in Docker instead
([`chi/agents/sandbox.py`](https://github.com/kdpisda/chi/blob/main/chi/agents/sandbox.py)).

## What does each Docker tier actually mount?

I built the real `docker run` argv for both tiers with chi's own
`make_sandbox` and `build_docker_command`, against a throwaway home
directory holding empty `.claude` and `.codex` folders. I did not start a
container. The argv is a pure function, so you can read it without Docker.

```text
docker      docker run --rm --network none -v $WORKDIR:/workspace -w /workspace chi-agent claude -p go
docker-cli  docker run --rm --network bridge -v $WORKDIR:/workspace -w /workspace -v $HOME/.claude:/home/agent/.claude:ro -v $HOME/.codex:/home/agent/.codex:ro chi-agent claude -p go
```

| | `docker` | `docker-cli` |
|---|---|---|
| Network | none | bridge (egress) |
| Host files | only the coder's workdir, read-write | the same, plus allowlisted auth dirs, read-only |
| Credentials | none | whichever of `~/.claude`, `~/.codex`, `~/.grok` exist |
| Leaderboard auth | never mounted | never mounted |
| Persistence | `--rm` | `--rm` |

The auth list is a constant, `CLI_AUTH_ALLOWLIST`, in the same file. A path
that doesn't exist on the host is skipped, so Docker never creates an empty
root-owned mount point. In my run only the two folders I created appeared.

## Why is docker-cli weaker, and when do you need it?

A vendor CLI can't work in the full jail. It needs the network to reach its
API and its login state to authenticate. So `docker-cli` grants both, and
the repo states the consequence directly: a prompt-injected agent now has
egress and a valid vendor credential, so anything readable in `/workspace`
can leave. I'd rather that sentence live in the docs than in an incident
report.

What `docker-cli` still prevents, per
[the security page](/docs/security/): reading host files beyond the workdir
and the read-only auth dirs, reading any other host credential, altering the
mounted auth (`:ro`), and persisting anything after the run (`--rm`).

My rule of thumb: use `docker` when the agent doesn't need a vendor API from
inside the container, and `docker-cli` only for CLI coders, knowing what it
trades away. Build the image with the Dockerfile in the repo
(`docker build -t chi-agent -f docker/chi-agent.Dockerfile .`).

## Why can an agent in the jail never submit?

The jail doesn't forbid submitting. It removes what submitting needs. The
leaderboard CLI authenticates from files under the host home, and neither tier
mounts them. chi does its own ranked submits from the host process
(`CHI_GATED_SUBMIT=1` is set only in chi's gated runner,
[`chi/eval/popcorn.py:73-80`](https://github.com/kdpisda/chi/blob/main/chi/eval/popcorn.py#L73-L80)),
through a gate that allows one submission at a time and rations them.

I ran the gate with its defaults on a fake clock (one submission per 1,800
seconds):

```text
submitted #1
second at t=60s: submission rationed — retry in 1740s
at t=1801s: submitted #2
```

The gate is
[`chi/eval/submission.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/submission.py).
Even under the autonomous director, ranked submissions stay manual: the
[security docs](/docs/security/) say chi surfaces the verified improvement and
you fire the submission yourself.

## What is the PATH shim, and why isn't it a sandbox?

Without Docker, agents run on the host with a `popcorn-cli` shim first on their
`PATH`. In dogfood the agents fired their own popcorn benchmarks, which is why
the shim exists. I ran it against a fake `popcorn-cli` that just echoes its
arguments:

```text
$ popcorn-cli submit --mode benchmark f.py
REAL popcorn-cli called: submit --mode benchmark f.py        exit=0
$ popcorn-cli submit --mode leaderboard f.py
REFUSED by chi: ranked leaderboard submission must go through chi's gate ...   exit=3
$ popcorn-cli submit f.py
REFUSED by chi: ranked leaderboard submission must go through chi's gate ...   exit=3
$ /path/to/real/popcorn-cli submit --mode leaderboard f.py
REAL popcorn-cli called: submit --mode leaderboard f.py      exit=0
```

Benchmarks pass, ranked and bare `submit` calls are refused, and the last
line is the point: the shim only catches the command name. An agent that
calls the real binary by absolute path walks around it. The source says so in
its own docstring: it does not strip credentials, and "a hard credential
boundary requires a real sandbox"
([`chi/eval/popcorn_shim.py`](https://github.com/kdpisda/chi/blob/main/chi/eval/popcorn_shim.py)).
It keeps a well-meaning agent from burning your rate limit. It does not stop
a hostile one.

## Can the evaluator run in a jail too?

Yes, separately. A problem can set `eval_sandbox: docker`, and the
correctness and benchmark commands then run through the same sandbox, so a
hostile candidate can't reach the host while being scored
([`chi/eval/runner.py:38-43`](https://github.com/kdpisda/chi/blob/main/chi/eval/runner.py#L38-L43)).
The config only accepts `none` or `docker` there. `docker-cli` is excluded
on purpose, because it mounts vendor auth that untrusted candidate code must
never see ([`chi/config.py:124-129`](https://github.com/kdpisda/chi/blob/main/chi/config.py#L124-L129)).

## Where this falls short

- **The default is no sandbox.** You have to opt in per coder.
- **I didn't start a container.** I verified the command chi builds and the
  shim's behavior, not Docker's isolation itself. Docker is the boundary, so
  its weaknesses (a shared kernel, a misconfigured daemon) are yours too.
- **`docker-cli` can leak the workdir.** Egress plus a live vendor
  credential is an exfiltration channel. If that bothers you, run chi itself
  inside a VM, as `SECURITY.md` recommends for untrusted problems.
- **The strongest isolation is not built.** The repo lists a real per-agent
  micro-VM sandbox as the top remaining hardening item, and I won't pretend
  otherwise. The wider list is in the
  [gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).
- **Only run problem packs you trust.** A `problem.yaml` entrypoint is a shell
  command chi executes.

## Try it

The sandbox needs Docker and a coder that talks to a real vendor, so the offline
demo won't exercise it. Read [the security docs](/docs/security/), then install
chi and run the scripted loop that needs no key and no container:
[a first autoresearch loop with no API key](/blog/first-autoresearch-loop-no-api-key/).
