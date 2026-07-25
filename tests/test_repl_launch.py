from pathlib import Path

from chi.session.engine import SessionEngine
from chi.tui.repl import run_repl


def test_repl_quits_via_engine(tmp_path: Path, capsys) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    inputs = iter(["/help", "/quit"])
    run_repl(engine, prompt_fn=lambda: next(inputs))
    out = capsys.readouterr().out
    assert "/models" in out and "bye" in out
    assert engine.quit_requested


def test_repl_survives_eof_when_idle(tmp_path: Path, capsys) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")

    def eof() -> str:
        raise EOFError

    run_repl(engine, prompt_fn=eof)  # returns instead of raising
    assert "chi" in capsys.readouterr().out


def test_engine_command_names_match_repl_completer_source(tmp_path: Path) -> None:
    engine = SessionEngine(runs_root=tmp_path / "runs")
    expected = {"/help", "/vendors", "/providers", "/models", "/run", "/status",
                "/steer", "/stop", "/ledger", "/champion", "/quit", "/exit", "/resume"}
    assert set(engine.commands) == expected
