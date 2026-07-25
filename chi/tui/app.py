"""Textual frontend: Claude Code-style full-terminal session for chi.

All behavior lives in chi.session.engine.SessionEngine; this app only renders
the transcript, streams live run events, and supplies modal pickers.
"""

import threading

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, RichLog, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection
from textual_autocomplete import AutoComplete, DropdownItem

from chi import __version__
from chi.session.engine import SessionEngine

BANNER = f"chi (χ) {__version__} — /help for commands · plain text steers the active run"


class PickScreen(ModalScreen[list]):
    """Filterable selection modal (checkbox list for multi, option list for single)."""

    BINDINGS = [
        Binding("escape", "cancel", "cancel", priority=True),
        Binding("enter", "accept", "accept", priority=True),
    ]

    def __init__(self, message: str, choices: list[tuple[str, str]], multi: bool) -> None:
        super().__init__()
        self._message = message
        self._choices = choices
        self._multi = multi
        self._chosen: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._message, id="pick-title")
            yield Input(placeholder="type to filter…", id="pick-filter")
            if self._multi:
                yield SelectionList(id="pick-list")
            else:
                yield OptionList(id="pick-list")
            hint = ("space toggle · enter accept · esc cancel" if self._multi
                    else "enter select · esc cancel")
            yield Static(hint, id="pick-hint")

    def on_mount(self) -> None:
        self._refill("")
        self.query_one("#pick-filter", Input).focus()

    def _visible(self, needle: str) -> list[tuple[str, str]]:
        needle = needle.lower()
        return [c for c in self._choices if needle in c[1].lower() or needle in c[0].lower()]

    def _refill(self, needle: str) -> None:
        visible = self._visible(needle)
        if self._multi:
            widget = self.query_one("#pick-list", SelectionList)
            widget.clear_options()
            widget.add_options([
                Selection(label, value, initial_state=value in self._chosen)
                for value, label in visible
            ])
        else:
            widget = self.query_one("#pick-list", OptionList)
            widget.clear_options()
            widget.add_options([Option(label, id=value) for value, label in visible])

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "pick-filter":
            self._sync_chosen()
            self._refill(event.value)

    def _sync_chosen(self) -> None:
        if not self._multi:
            return
        widget = self.query_one("#pick-list", SelectionList)
        visible = {value for value, _ in self._visible(self.query_one("#pick-filter", Input).value)}
        self._chosen = (self._chosen - visible) | set(widget.selected)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not self._multi:
            self.dismiss([event.option.id])

    def action_accept(self) -> None:
        if self._multi:
            self._sync_chosen()
            self.dismiss(sorted(self._chosen))
        else:
            widget = self.query_one("#pick-list", OptionList)
            if widget.highlighted is not None:
                option = widget.get_option_at_index(widget.highlighted)
                self.dismiss([option.id])
            else:
                self.dismiss([])

    def action_cancel(self) -> None:
        self.dismiss([])


class ChiApp(App):
    """The interactive chi harness."""

    TITLE = "chi (χ)"
    CSS = """
    #banner { height: 1; padding: 0 1; color: $accent; text-style: bold; }
    #transcript { height: 1fr; border: round $primary 30%; padding: 0 1; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    #prompt { border: round $accent; }
    PickScreen { align: center middle; }
    #dialog { width: 80%; height: 70%; border: round $accent; background: $surface;
              padding: 1; }
    #pick-title { text-style: bold; }
    #pick-hint { color: $text-muted; }
    #pick-list { height: 1fr; }
    """
    BINDINGS = [Binding("ctrl+c", "request_quit", "quit", priority=True)]

    def __init__(self, engine: SessionEngine | None = None) -> None:
        super().__init__()
        self.engine = engine or SessionEngine()
        self.engine.picker_fn = self._pick_from_thread
        self.transcript_lines: list[str] = []  # plain mirror, used by tests

    def compose(self) -> ComposeResult:
        yield Static(BANNER, id="banner")
        yield RichLog(id="transcript", wrap=True)
        yield Static("idle", id="status")
        prompt = Input(placeholder="/help for commands — type to chi", id="prompt")
        yield prompt
        yield AutoComplete(target=prompt, candidates=self._candidates)

    def _candidates(self, state) -> list[DropdownItem]:
        text = state.text
        if not text.startswith("/") or " " in text:
            return []
        return [DropdownItem(name) for name in sorted(self.engine.commands)]

    def on_mount(self) -> None:
        self.set_interval(0.5, self._pump)
        self.query_one("#prompt", Input).focus()

    # -- transcript ---------------------------------------------------------

    def _style_for(self, line: str) -> str | None:
        if line.startswith(("error:", "⚠")):
            return "yellow" if line.startswith("⚠") else "red"
        if "★ new best" in line:
            return "green bold"
        if line.startswith(("run finished", "run ", "starting run")):
            return "cyan"
        if line.startswith("❯"):
            return "dim"
        return None

    def _write(self, line: str, style: str | None = None) -> None:
        self.transcript_lines.append(line)
        self.query_one("#transcript", RichLog).write(
            Text(line, style=style if style is not None else (self._style_for(line) or ""))
        )

    def _write_lines(self, lines: list[str]) -> None:
        for line in lines:
            self._write(line)

    # -- input --------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "prompt":
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        self._write(f"❯ {text}")
        self._submit(text)

    @work(thread=True, exclusive=False)
    def _submit(self, text: str) -> None:
        lines = self.engine.submit(text)
        self.call_from_thread(self._write_lines, lines)
        if self.engine.quit_requested:
            self.call_from_thread(self.exit)

    # -- live pump ------------------------------------------------------------

    def _pump(self) -> None:
        for line in self.engine.poll_events():
            self._write(line)
        snap = self.engine.snapshot()
        if snap["active"]:
            best = snap["best"] if snap["best"] is not None else "—"
            status = f"● running {snap['run_id'] or '(starting)'} · best {best}"
        else:
            status = "idle"
        self.query_one("#status", Static).update(status)

    # -- picker bridge (called from the submit worker thread) ---------------

    def _pick_from_thread(self, message: str, choices: list[tuple[str, str]],
                          multi: bool) -> list[str]:
        result: list[str] = []
        done = threading.Event()

        def show() -> None:
            def finished(values: list | None) -> None:
                result.extend(values or [])
                done.set()

            self.push_screen(PickScreen(message, choices, multi), finished)

        self.call_from_thread(show)
        done.wait(timeout=600)
        return result

    def action_request_quit(self) -> None:
        if self.engine.has_active_run():
            self._write("a run is active — /stop it first")
            return
        self.exit()


def run_app(engine: SessionEngine | None = None) -> None:
    """Launch the full-terminal chi UI."""
    ChiApp(engine).run()
