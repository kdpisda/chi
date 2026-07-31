"""Eval-backend registry: pluggable benchmark/submit backends.

Same pi principle as chi/agents/registry.py — minimal core, everything is an
extension. `_build_auto_submitter` hardcoded PopcornBackend, so every new
backend (Modal, an SSH box, a different leaderboard CLI) meant editing core.
Backends register by name (built-ins here; third-party backends can register
from a plugin), so adding one is registration, not a core edit.

A backend is duck-typed: `benchmark(candidate)` and `submit(candidate)` in the
shape of PopcornBackend.
"""

from typing import Any, Callable

# name -> factory(**kwargs) -> backend
_BACKENDS: dict[str, Callable[..., Any]] = {}


def register_backend(name: str, factory: Callable[..., Any]) -> None:
    """Register a backend factory under a name (idempotent overwrite)."""
    _BACKENDS[name] = factory


def backend(name: str) -> Callable[[type], type]:
    """Decorator form: @backend('modal') class ModalBackend…"""
    def deco(cls: type) -> type:
        register_backend(name, cls)
        return cls
    return deco


def known_backends() -> list[str]:
    """Registered backend names."""
    _ensure_builtins()
    return sorted(_BACKENDS)


_builtins_loaded = False


def _ensure_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    register_backend("popcorn", _popcorn_factory)
    _builtins_loaded = True


def _popcorn_factory(**kwargs: Any) -> Any:
    """Late-bound so monkeypatching chi.eval.popcorn.PopcornBackend still works."""
    from chi.eval import popcorn

    return popcorn.PopcornBackend(**kwargs)


def build_backend(name: str, **kwargs: Any) -> Any:
    """Construct the named eval backend."""
    _ensure_builtins()
    factory = _BACKENDS.get(name)
    if factory is None:
        raise ValueError(
            f"unknown eval backend '{name}' — known: {', '.join(known_backends())}")
    return factory(**kwargs)
