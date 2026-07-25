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
    # offer_setup=False: the first-run question modal would swallow test input
    return ChiApp(SessionEngine(runs_root=tmp_path / "runs"), offer_setup=False)


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


async def test_working_indicator_while_operator_thinks(tmp_path: Path) -> None:
    import time as _time
    from types import SimpleNamespace

    from chi.config import CoderCfg
    from chi.userconfig import UserConfig, save_user_config

    save_user_config(UserConfig(role_models={"orchestrator": "test/m"}))

    def slow_completion(model, messages, **kwargs):
        _time.sleep(1.2)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done",
                                                              tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            _hidden_params={"response_cost": 0.0},
        )

    app = _app(tmp_path)
    app.engine.completion_fn = slow_completion
    async with app.run_test() as pilot:
        for ch in "hi":
            await pilot.press(ch)
        await pilot.press("enter")
        await asyncio.sleep(0.7)
        await pilot.pause()
        assert app._busy_count == 1  # spinner active while the operator thinks
        from textual.widgets import Static

        activity = app.query_one("#activity", Static)
        assert activity.display is True  # inline line right below the user's query
        await _wait_for(app, "done", timeout_s=15.0)
        await asyncio.sleep(0.8)  # let the next pump tick hide the activity line
        await pilot.pause()
        assert app._busy_count == 0
        assert activity.display is False


async def test_escape_returns_focus_to_prompt(tmp_path: Path) -> None:
    from textual.widgets import Input, RichLog

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.set_focus(app.query_one("#transcript", RichLog))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is app.query_one("#prompt", Input)


async def test_pick_screen_arrows_work_from_filter(tmp_path: Path) -> None:
    from textual.widgets import SelectionList

    app = _app(tmp_path)
    results: list = []
    async with app.run_test() as pilot:
        app.push_screen(
            PickScreen("pick", [("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")], multi=True),
            lambda values: results.extend(values or []),
        )
        await pilot.pause()
        # focus is in the filter input; arrows must still drive the list
        # (first press lands on item 0, second on item 1)
        await pilot.press("down", "down")
        sel = app.screen.query_one("#pick-list", SelectionList)
        assert sel.highlighted == 1
        await pilot.press("ctrl+space")   # toggle highlighted without leaving filter
        await pilot.press("enter")
        await pilot.pause()
    assert results == ["b"]


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
