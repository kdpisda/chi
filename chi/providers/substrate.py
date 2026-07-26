"""Substrate capability probing: does this CLI actually run, for THIS account?

Today's dogfood wasted a run because codex is installed but its ChatGPT account
rejects every model ("not supported when using Codex with a ChatGPT account"),
and grok's command template was wrong — both discovered only mid-run. A cheap
capability probe catches "installed but non-functional" before a fleet launches,
so `chi providers`/setup can show `codex: installed but FAILS (account)` instead
of failing silently 40 iterations deep.

Capability, not just presence: `shutil.which` says a binary exists; a probe says
it actually produces output for this user's auth/model.
"""

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from chi.providers.catalog import CLI_SUBSTRATES

# a trivial prompt each CLI should answer near-instantly if it works at all
_PROBE_PROMPT = "Reply with exactly the single word: PONG"

# how to run a one-shot probe per CLI (verified-working headless forms, 2026-07)
_PROBE_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p", _PROBE_PROMPT],
    "codex": ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
              _PROBE_PROMPT],
    "grok": ["grok", "-p", _PROBE_PROMPT],
}

# error signatures that mean "installed but this account/config can't use it"
_ACCOUNT_FAIL = ("not supported when using", "invalid_request_error",
                 "unauthorized", "not authenticated", "please run", "login")


@dataclass
class SubstrateStatus:
    cli: str
    ok: bool
    detail: str


_CACHE: dict[str, SubstrateStatus] = {}


def probe_substrate(
    cli: str,
    runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
    timeout: int = 60,
    use_cache: bool = True,
) -> SubstrateStatus:
    """Run a one-shot probe; True only if the CLI actually produces a reply.

    Cached per-CLI for the process (probing spends a real CLI call).
    """
    if use_cache and cli in _CACHE:
        return _CACHE[cli]
    if cli not in _PROBE_COMMANDS:
        status = SubstrateStatus(cli, False, "unknown CLI substrate")
        _CACHE[cli] = status
        return status
    if runner is None and shutil.which(cli) is None:
        status = SubstrateStatus(cli, False, "not installed (not on PATH)")
        _CACHE[cli] = status
        return status

    def _default_runner(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    run = runner or _default_runner
    try:
        proc = run(_PROBE_COMMANDS[cli])
    except subprocess.TimeoutExpired:
        status = SubstrateStatus(cli, False, "probe timed out")
        _CACHE[cli] = status
        return status
    except Exception as exc:  # any spawn failure means non-functional here
        status = SubstrateStatus(cli, False, f"probe failed: {exc}"[:120])
        _CACHE[cli] = status
        return status

    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    if proc.returncode != 0 or any(sig in out for sig in _ACCOUNT_FAIL):
        reason = next((sig for sig in _ACCOUNT_FAIL if sig in out), None)
        detail = f"account/config rejected ({reason})" if reason else \
            f"exit {proc.returncode}"
        status = SubstrateStatus(cli, False, detail)
    elif "pong" in out:
        status = SubstrateStatus(cli, True, "responds")
    else:
        # ran, no error, but no expected reply — treat as usable-but-unverified
        status = SubstrateStatus(cli, True, "ran (reply unconfirmed)")
    _CACHE[cli] = status
    return status


def probe_all(runner: Callable | None = None) -> list[SubstrateStatus]:
    """Probe every installed CLI substrate."""
    return [probe_substrate(cli, runner=runner) for cli in CLI_SUBSTRATES
            if runner is not None or shutil.which(cli) is not None]


def clear_cache() -> None:
    """Forget cached probe results (e.g. after the user fixes auth)."""
    _CACHE.clear()
