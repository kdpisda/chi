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


async def test_working_indicator_shows_and_hides_with_busy_state(tmp_path: Path) -> None:
    # the indicator is a pure function of busy state; test that _pump reveals it
    # while busy and hides it when idle (the threaded operator round-trip that
    # sets busy state is covered by the engine/operator unit tests + live use)
    from textual.widgets import Static

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        activity = app.query_one("#activity", Static)
        assert activity.display is False  # idle at start

        import time as _time

        app._busy_count = 1
        app._busy_since = _time.monotonic()
        app.engine.busy_note = "thinking via test/model"
        app._pump()
        await pilot.pause()
        assert activity.display is True  # shows while busy, below the user's query
        assert "thinking" in str(activity.render())

        app._busy_count = 0
        app._busy_since = None
        app._pump()
        await pilot.pause()
        assert activity.display is False  # hides when idle


async def test_escape_returns_focus_to_prompt(tmp_path: Path) -> None:
    from textual.widgets import RichLog

    from chi.tui.app import PromptArea

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.set_focus(app.query_one("#transcript", RichLog))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is app.query_one("#prompt", PromptArea)


async def test_multiline_paste_and_submit(tmp_path: Path) -> None:
    from textual.events import Paste

    from chi.tui.app import PromptArea

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", PromptArea)
        # real terminal paste is delivered app-level and routed to the focused widget
        app.post_message(Paste("line one\nline two\nline three"))
        await pilot.pause()
        assert prompt.document.line_count == 3  # newlines preserved, not flattened
        await pilot.press("enter")
        await pilot.pause()
        # echoed into the transcript as one multi-line entry
        assert any(line == "❯ line one" for line in app.transcript_lines)
        assert any(line == "  line three" for line in app.transcript_lines)


async def test_ctrl_j_and_backslash_add_newlines(tmp_path: Path) -> None:
    from chi.tui.app import PromptArea

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", PromptArea)
        for ch in "abc":
            await pilot.press(ch)
        await pilot.press("ctrl+j")
        for ch in "def":
            await pilot.press(ch)
        assert prompt.text == "abc\ndef"
        # trailing backslash + enter continues the draft instead of submitting
        await pilot.press("backslash", "enter")
        await pilot.pause()
        assert prompt.text == "abc\ndef\n"
        assert not any(line.startswith("❯") for line in app.transcript_lines)


async def test_tab_completes_slash_commands(tmp_path: Path) -> None:
    from chi.tui.app import PromptArea

    app = _app(tmp_path)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", PromptArea)
        for ch in "/cha":
            await pilot.press(ch)
        await pilot.press("tab")
        assert prompt.text == "/champion"


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
