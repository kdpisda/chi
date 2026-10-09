---
title: "How to Install getchi and Run the chi CLI (First-Run Trap)"
description: "The PyPI package is getchi, the command is chi. Install it with uv, then avoid the trap where chi run examples/offline.yaml fails outside a repo checkout."
date: 2026-10-09T02:31:22Z
draft: false
category: "Guide"
tags: ["getting started", "install", "getchi", "autoresearch", "CLI"]
toc: true
newsletter:
  subject: "I installed my own tool and the demo crashed"
  preheader: "Install getchi from PyPI, then dodge a path trap on the very first command."
  body: |
    The package is `getchi`, the command is `chi`, and the name is pronounced "kai". That part is easy. The first command after install is where I found a trap.

    I installed getchi from PyPI into a clean environment, then ran the offline demo from an empty directory, the way a new user would. It died with a `FileNotFoundError`, even though the demo files are inside the wheel.

    The new post covers the install, what the name collisions mean, why the demo needs a working directory with the right files, and the two-command fix. It ends with a zero-cost run you can read afterward.

    [Read how to install getchi and run chi](https://getchi.dev/blog/install-getchi-chi-cli/)
---

You saw "chi" in a README, typed `pip install chi`, and got somebody else's
package. The project is published as **getchi**, and the command it installs is
`chi`. Here is the short version of the install, and one trap I hit on the first
command when I tested it from a clean directory.

## How do I install getchi and get the chi command?

```sh
uv tool install getchi
chi version
```

I ran this today in a fresh tool directory. It installed one executable, `chi`,
and `chi version` printed `0.2.0`. That matches `chi/__init__.py` and the latest
release on PyPI, which lists two releases so far, 0.1.0 and 0.2.0 (fetched
from `pypi.org/pypi/getchi/json`, 2026-10-09). chi needs Python 3.11 or newer
([`pyproject.toml`](https://github.com/kdpisda/chi/blob/main/pyproject.toml)).

`uv tool install` puts chi in its own isolated environment and puts the
command on your PATH, so it does not touch your project's dependencies. If you
only want to try it, `uvx --from getchi chi version` ran for me without a
permanent install and printed the same `0.2.0`. The
[getting started docs](/docs/getting-started/) also list `pip install getchi`.
I did not test pip in this run.

## Why is the package getchi and the command chi?

Because the bare name is taken. `pypi.org/pypi/chi/json` returns a package named
`chi` (version 0.2, uploaded in 2017 by another author, home page
`github.com/rmst/chi`, fetched 2026-10-09). There is also a well-known Go HTTP
router called chi, which this project is unrelated to. The repo's own comment
says the bare name "collides with the go-chi router and others"
([`README.md`](https://github.com/kdpisda/chi/blob/main/README.md)).

So the distribution is `getchi` and the entry point is declared as `chi =
"chi.cli:app"` in `pyproject.toml`. The name is the Greek letter χ,
pronounced "kai". If you ever see `chi: command not found` right after an
install, the usual cause is that uv's tool bin directory is not on your PATH.
uv warns about this at install time, and `uv tool update-shell` fixes
it.

## What happens when I run the offline demo right after installing?

chi ships a demo that needs no API key. A **scripted** coder replays three canned
candidates for a prefix-sum problem, and the evaluator, run store and champion
selection are all real. The docs and the README tell you to run it like this:

```sh
chi run examples/offline.yaml
```

I ran exactly that from an empty directory after a clean `uv tool install
getchi`. It failed:

```text
FileNotFoundError: [Errno 2] No such file or directory: 'examples/offline.yaml'
```

The wheel does contain the demo. `pyproject.toml` force-includes `examples` and
`problems/optimize_function` into it, and I found both directories at the top of
the tool's `site-packages`. But `examples/offline.yaml` is a relative path
resolved against your current directory, and the file points at
`problems/optimize_function` and `examples/demo_candidates.json` the same way.
The file's own header says: "Run it from the repo root."

You have two ways out. Clone the repo and run from its root, which the
[first-loop post](/blog/first-autoresearch-loop-no-api-key/) does:

```sh
git clone https://github.com/kdpisda/chi && cd chi
chi run examples/offline.yaml
```

Or copy the bundled files out of the installed package, no clone needed:

```sh
SP=$(echo "$(uv tool dir)"/getchi/lib/python3*/site-packages)
mkdir chi-demo && cd chi-demo
cp -r "$SP/examples" "$SP/problems" .
chi run examples/offline.yaml
```

I ran the second version. It finished in about 2 seconds of wall time and
printed:

```json
{
  "iterations": 3,
  "baseline_score": 43.07,
  "champion_score": 0.064,
  "total_cost_usd": 0,
  "status": "done"
}
```

I trimmed the output to the fields that matter. Scores are runtime in
milliseconds, lower is better, and they move from run to run on a shared
machine, so treat 43 to 0.06 as "hundreds of times faster", not a benchmark. The speedup is the point of the demo problem, which starts from a
deliberately O(n²) function.

## Where does chi write its results?

Not in the directory you ran from. By default runs go to a global data
directory, `~/.local/share/chi/runs`, so history is shared across projects
(`default_runs_root()` in [`chi/userconfig.py`](https://github.com/kdpisda/chi/blob/main/chi/userconfig.py)).
Set `CHI_DATA_DIR` to move it, or pass `--runs-root` to `chi run` for one run.
The run printed its `run_dir`, and `chi status <run_dir>` reads the run row and
the latest events back. Older posts and the README show `runs/<run_id>` paths, which
no longer match the default.

Run `chi validate examples/offline.yaml` first if you want a cheap check that
the paths resolve. It printed `OK fleet 'offline-demo' (1 coder(s))` for me.

## What do I need before running real agents?

The offline demo needs nothing. A real run needs at least one provider: an API
key for anything LiteLLM routes, or an installed vendor CLI such as `claude`,
`codex` or `grok`. Optional Docker adds sandboxed coders. The
[getting started page](/docs/getting-started/) walks through `/vendors`,
`/models` and `/setkey` inside the interactive session.

## Where this falls short

The first-run path is rougher than it should be. The wheel carries the demo
files but nothing tells you where they landed, and `chi run` raises a raw
traceback for a missing fleet file instead of a one-line hint. I have not
changed either on `main`, and I did not look for other commands with the same
relative-path behavior. If you want a plain list of known gaps, see the
[gap analysis](https://github.com/kdpisda/chi/blob/main/docs/autoresearch-gap-analysis.md).

## Next step

Do the copy-out version above, run the demo once, and read the run back with
`chi status`. Then star the [repo on GitHub](https://github.com/kdpisda/chi) if
the install worked for you, and open an issue if it did not. That is the fastest
way to get the first-run path fixed.
