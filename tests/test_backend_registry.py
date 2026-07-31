import shutil
from pathlib import Path

import pytest
import yaml

from chi.config import load_problem
from chi.eval.popcorn import PopcornBackend
from chi.eval.registry import backend, build_backend, known_backends, register_backend

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"


def _leaderboard_pack(tmp_path: Path, **overrides) -> Path:
    """optimize_function copy re-manifested as a leaderboard + auto_submit pack."""
    pack = tmp_path / "pack"
    shutil.copytree(PROBLEM_DIR, pack)
    m = yaml.safe_load((pack / "problem.yaml").read_text())
    m.update({"leaderboard": "gpumode-776", "benchmark_cmd": "popcorn bench {candidate}",
              "submit_cmd": "popcorn submit {candidate}", "auto_submit": True})
    m.update(overrides)
    (pack / "problem.yaml").write_text(yaml.safe_dump(m))
    return pack


# --- registry ---------------------------------------------------------------

def test_registry_knows_builtin_popcorn() -> None:
    assert "popcorn" in known_backends()


def test_register_and_build_backend() -> None:
    built = {}

    class _Fake:
        def __init__(self, **kwargs):
            built.update(kwargs)

    register_backend("fake-box", _Fake)
    assert "fake-box" in known_backends()
    b = build_backend("fake-box", leaderboard="lb")
    assert isinstance(b, _Fake) and built == {"leaderboard": "lb"}


def test_build_backend_unknown_lists_known() -> None:
    with pytest.raises(ValueError, match="unknown eval backend 'nope'") as exc:
        build_backend("nope")
    assert "popcorn" in str(exc.value)  # error names the known backends


def test_decorator_form_registers() -> None:
    @backend("decorated-box")
    class _Decorated:
        def __init__(self, **kwargs): ...

    assert "decorated-box" in known_backends()
    assert isinstance(build_backend("decorated-box"), _Decorated)


def test_builtin_popcorn_builds_popcorn_backend() -> None:
    b = build_backend("popcorn", leaderboard="lb",
                      benchmark_cmd="bench {candidate}", submit_cmd="submit {candidate}")
    assert isinstance(b, PopcornBackend)
    assert b.leaderboard == "lb"


# --- _build_auto_submitter through the registry -----------------------------

def test_build_auto_submitter_defaults_to_popcorn(tmp_path: Path) -> None:
    from chi.eval.autosubmit import AutoSubmitter
    from chi.orchestrator.loop import _build_auto_submitter
    from chi.store.db import Store

    problem = load_problem(_leaderboard_pack(tmp_path))
    assert problem.eval_backend == "popcorn"  # pack named no backend -> default
    store = Store.open(tmp_path / "run")
    sub = _build_auto_submitter(store, "run", problem)
    assert isinstance(sub, AutoSubmitter)
    assert isinstance(sub.backend, PopcornBackend)
    assert sub.backend.leaderboard == "gpumode-776"
    assert sub.backend.submit_cmd == "popcorn submit {candidate}"


def test_pack_can_name_a_registered_backend(tmp_path: Path) -> None:
    from chi.orchestrator.loop import _build_auto_submitter
    from chi.store.db import Store

    built = {}

    class _SshBox:
        def __init__(self, **kwargs):
            built.update(kwargs)

    register_backend("ssh-box", _SshBox)
    problem = load_problem(_leaderboard_pack(tmp_path, eval_backend="ssh-box"))
    store = Store.open(tmp_path / "run")
    sub = _build_auto_submitter(store, "run", problem)
    assert isinstance(sub.backend, _SshBox)  # pack choice, not the builtin
    assert built == {"leaderboard": "gpumode-776",
                     "benchmark_cmd": "popcorn bench {candidate}",
                     "submit_cmd": "popcorn submit {candidate}"}
