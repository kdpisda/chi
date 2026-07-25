import asyncio
import json
import time
from pathlib import Path

import pytest
import yaml

from chi.session.engine import SessionEngine
from chi.tui.app import ChiApp, PickScreen

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"

GOOD = ("import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CHI_CONFIG_DIR", str(tmp_path / "chi-cfg"))


def _app(tmp_path: Path) -> ChiApp:
    return ChiApp(SessionEngine(runs_root=tmp_path / "runs"))


async def _submit_line(pilot, text: str) -> None:
    for ch in text:
        await pilot.press(ch)
    await pilot.press("enter")
    # the autocomplete dropdown may consume the first enter to complete
    await pilot.press("enter")
    await pilot.pause()


async def _wait_for(app: ChiApp, needle: str, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if any(needle in line for line in app.transcript_lines):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"'{needle}' never appeared; got {app.transcript_lines}")


async def test_help_renders_in_transcript(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _submit_line(pilot, "/help")
        await _wait_for(app, "/models")


async def test_unknown_command_shows_error(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _submit_line(pilot, "/bogus")
        await _wait_for(app, "unknown command")


async def test_quit_exits_app(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _submit_line(pilot, "/quit")
        await asyncio.sleep(0.3)
        await pilot.pause()
    assert app.engine.quit_requested


async def test_run_streams_into_transcript_and_status(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([GOOD]))
    fleet = tmp_path / "fleet.yaml"
    fleet.write_text(yaml.safe_dump({
        "run_name": "ui", "problem": str(PROBLEM_DIR),
        "budgets": {"total_usd": 1.0},
        "coders": [{"id": "c1", "model": "scripted", "adapter": "scripted",
                     "script": str(script)}],
        "policies": {"max_iterations": 1},
    }))
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await _submit_line(pilot, f"/run {fleet}")
        await _wait_for(app, "run finished [done]", timeout_s=60.0)
        assert any("★ new best" in line for line in app.transcript_lines)


async def test_pick_screen_multi_filter_and_accept(tmp_path: Path) -> None:
    app = _app(tmp_path)
    results: list = []
    async with app.run_test() as pilot:
        app.push_screen(
            PickScreen("pick", [("a", "Alpha Model"), ("b", "Beta Model")], multi=True),
            lambda values: results.extend(values or []),
        )
        await pilot.pause()
        # filter down to Beta, toggle it via the list, accept
        for ch in "beta":
            await pilot.press(ch)
        await pilot.pause()
        screen = app.screen
        from textual.widgets import SelectionList

        sel = screen.query_one("#pick-list", SelectionList)
        sel.select_all()
        await pilot.press("enter")
        await pilot.pause()
    assert results == ["b"]
