import subprocess

import pytest

from chi.providers.substrate import clear_cache, probe_substrate


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


def _runner(stdout: str, rc: int = 0, stderr: str = ""):
    def run(argv):
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr=stderr)
    return run


def test_working_cli_probes_ok() -> None:
    s = probe_substrate("claude", runner=_runner("PONG"))
    assert s.ok and s.detail == "responds"


def test_codex_account_rejection_detected() -> None:
    # the exact failure from today's dogfood
    err = ('ERROR: {"error":{"message":"The \'gpt-5.3-codex\' model is not '
           'supported when using Codex with a ChatGPT account."}}')
    s = probe_substrate("codex", runner=_runner("", rc=1, stderr=err))
    assert not s.ok and "account/config rejected" in s.detail


def test_nonzero_exit_is_failure() -> None:
    s = probe_substrate("grok", runner=_runner("", rc=2, stderr="usage: grok ..."))
    assert not s.ok and "exit 2" in s.detail


def test_ran_without_expected_reply_is_usable_unconfirmed() -> None:
    s = probe_substrate("claude", runner=_runner("some other output"))
    assert s.ok and "unconfirmed" in s.detail


def test_timeout_is_failure() -> None:
    def timeout_runner(argv):
        raise subprocess.TimeoutExpired(argv, 60)
    s = probe_substrate("claude", runner=timeout_runner)
    assert not s.ok and "timed out" in s.detail


def test_result_is_cached() -> None:
    calls = {"n": 0}

    def counting(argv):
        calls["n"] += 1
        return subprocess.CompletedProcess(argv, 0, stdout="PONG", stderr="")

    probe_substrate("claude", runner=counting)
    probe_substrate("claude", runner=counting)
    assert calls["n"] == 1  # second call served from cache


def test_unknown_cli() -> None:
    s = probe_substrate("nonesuch", runner=_runner("x"))
    assert not s.ok and "unknown" in s.detail
