"""Adapter registry: pluggable agent substrates (item 6, first slice).

pi's "minimal core, everything is an extension" principle. chi's `_make_adapter`
was a hardcoded `if adapter == …` switch — every new substrate meant editing
core. This registry lets adapters register by name (built-ins here; third-party
adapters can register from a plugin), so adding a substrate is registration, not
a core edit.

Kept deliberately small: a name → factory map, plus `build_adapter` that also
constructs the coder's configured sandbox and threads it in. Backends
(local/Modal/SSH/leaderboard) can get the same treatment next.
"""

from typing import Callable

from chi.agents.protocol import CoderAdapter

# name -> factory(**kwargs) -> CoderAdapter
_ADAPTERS: dict[str, Callable[..., CoderAdapter]] = {}


def register_adapter(name: str, factory: Callable[..., CoderAdapter]) -> None:
    """Register an adapter factory under a name (idempotent overwrite)."""
    _ADAPTERS[name] = factory


def adapter(name: str):
    """Decorator form: @adapter('json_stream') class JsonStreamCliAdapter…"""
    def deco(cls):
        register_adapter(name, cls)
        return cls
    return deco


def known_adapters() -> list[str]:
    """Registered adapter names."""
    _ensure_builtins()
    return sorted(_ADAPTERS)


_builtins_loaded = False


def _ensure_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    from chi.agents.cli_subprocess import CliSubprocessAdapter
    from chi.agents.json_stream import JsonStreamCliAdapter
    from chi.agents.litellm_loop import LiteLLMLoopAdapter
    from chi.agents.scripted import ScriptedAdapter

    register_adapter("scripted", ScriptedAdapter)
    register_adapter("litellm_loop", LiteLLMLoopAdapter)
    register_adapter("cli_subprocess", CliSubprocessAdapter)
    register_adapter("json_stream", JsonStreamCliAdapter)
    _builtins_loaded = True


def build_adapter(coder, *, store, run_id, workdir, problem, budget, policies,
                  completion_fn=None) -> CoderAdapter:
    """Construct the configured adapter for a coder, with its sandbox."""
    _ensure_builtins()
    factory = _ADAPTERS.get(coder.adapter)
    if factory is None:
        raise ValueError(
            f"unknown adapter '{coder.adapter}' — known: {', '.join(known_adapters())}")
    if coder.adapter in ("cli_subprocess", "json_stream") and not coder.command:
        raise ValueError(f"{coder.adapter} adapter requires 'command' in the coder config")

    from chi.agents.sandbox import make_sandbox

    sandbox = make_sandbox(coder.sandbox, coder.sandbox_image, coder.sandbox_network)
    kwargs = dict(store=store, run_id=run_id, agent_id=coder.id, model=coder.model,
                  workdir=workdir, problem=problem, budget=budget, policies=policies,
                  command=coder.command, script=coder.script, sandbox=sandbox)
    if coder.adapter == "litellm_loop":
        return factory(**kwargs, completion_fn=completion_fn)
    return factory(**kwargs)
