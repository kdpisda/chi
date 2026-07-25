"""Textual frontend: Claude Code-style full-terminal session for chi.

All behavior lives in chi.session.engine.SessionEngine; this app only renders
the transcript, streams live run events, and supplies modal pickers.
"""

import threading
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, RichLog, Rule, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection
from textual_autocomplete import AutoComplete, DropdownItem

from chi import __version__
from chi.session.engine import SessionEngine

# a pixel-block χ, in the spirit of vendor-CLI marks
LOGO = "▚▄ ▄▞\n ▄█▄\n▞▀ ▀█▄"


class PromptInput(Input):
    """Input with persistent, system-wide up/down history."""

    BINDINGS = [
        Binding("up", "hist_prev", show=False),
        Binding("down", "hist_next", show=False),
    ]

    def on_mount(self) -> None:
        from chi.userconfig import load_history

        self._hist: list[str] = load_history()
        self._idx = len(self._hist)

    def remember(self, text: str) -> None:
        """Persist one submitted line to the global history."""
        from chi.userconfig import append_history

        append_history(text)
        self._hist.append(text)
        self._idx = len(self._hist)

    def action_hist_prev(self) -> None:
        if self._idx > 0:
            self._idx -= 1
            self.value = self._hist[self._idx]
            self.cursor_position = len(self.value)

    def action_hist_next(self) -> None:
        if self._idx < len(self._hist) - 1:
            self._idx += 1
            self.value = self._hist[self._idx]
        else:
            self._idx = len(self._hist)
            self.value = ""
        self.cursor_position = len(self.value)


class QuestionScreen(ModalScreen[str | None]):
    """Single-choice question with a toggler, Claude Code / codex style."""

    BINDINGS = [Binding("escape", "cancel", "cancel", priority=True)]

    def __init__(self, question: str, options: list[tuple[str, str]]) -> None:
        super().__init__()
        self._question = question
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="qdialog"):
            yield Static(self._question, id="q-title")
            yield OptionList(
                *[Option(f"{n}. {label}", id=value)
                  for n, (value, label) in enumerate(self._options, start=1)],
                id="q-options",
            )
            yield Static("↑↓ toggle · enter select · 1-9 quick pick · esc cancel", id="q-hint")

    def on_mount(self) -> None:
        options = self.query_one("#q-options", OptionList)
        options.highlighted = 0
        options.focus()

    def on_key(self, event) -> None:
        if event.key.isdigit():
            index = int(event.key) - 1
            if 0 <= index < len(self._options):
                event.stop()
                self.dismiss(self._options[index][0])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PickScreen(ModalScreen[list]):
    """Filterable selection modal (checkbox list for multi, option list for single).

    Fully keyboard-driven: type filters, ↑↓ move the list even while typing,
    space (in the list) or tab+space toggles, enter accepts, esc cancels.
    Mouse clicking still works everywhere.
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel", priority=True),
        Binding("enter", "accept", "accept", priority=True),
        Binding("up", "move(-1)", "up", priority=True, show=False),
        Binding("down", "move(1)", "down", priority=True, show=False),
        Binding("ctrl+space", "toggle_highlighted", "toggle", priority=True, show=False),
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
            hint = ("↑↓ move · ctrl+space toggle (or tab + space) · enter accept"
                    " · esc cancel" if self._multi
                    else "↑↓ move · enter select · esc cancel")
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

    def _list_widget(self) -> OptionList:
        return self.query_one("#pick-list", SelectionList if self._multi else OptionList)

    def action_move(self, delta: int) -> None:
        """Arrow keys drive the list even while the filter input has focus."""
        widget = self._list_widget()
        if widget.option_count == 0:
            return
        current = widget.highlighted if widget.highlighted is not None else -1
        widget.highlighted = max(0, min(widget.option_count - 1, current + delta))

    def action_toggle_highlighted(self) -> None:
        if not self._multi:
            return
        widget = self.query_one("#pick-list", SelectionList)
        if widget.highlighted is not None:
            widget.toggle(widget.get_option_at_index(widget.highlighted))

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


class SecretScreen(ModalScreen[str | None]):
    """Masked single-value input (API keys) — enter submits, esc cancels."""

    BINDINGS = [Binding("escape", "cancel", "cancel", priority=True)]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="sdialog"):
            yield Static(self._prompt, id="s-title")
            yield Input(password=True, placeholder="paste or type, enter to save", id="s-input")
            yield Static("enter save · esc cancel", id="s-hint")

    def on_mount(self) -> None:
        self.query_one("#s-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChiApp(App):
    """The interactive chi harness."""

    TITLE = "chi (χ)"
    CSS = """
    #header-row { height: auto; padding: 1 1 0 2; }
    #logo { width: auto; color: $accent; text-style: bold; margin-right: 2; }
    #header-text { width: 1fr; }
    #transcript { height: 1fr; padding: 1 2 0 2; border: none;
                  background: transparent; scrollbar-size-vertical: 1; }
    #prompt-rule { margin: 0 1; color: $foreground 20%; }
    #prompt-row { height: 1; padding: 0 1; }
    #prompt-prefix { width: 2; color: $accent; text-style: bold; }
    #prompt { border: none; height: 1; padding: 0; background: transparent; width: 1fr; }
    #status { height: 1; padding: 0 2; color: $text-muted; }
    PickScreen { align: center middle; }
    #dialog { width: 80%; height: 70%; border: round $accent; background: $surface;
              padding: 1; }
    #pick-title { text-style: bold; }
    #pick-hint { color: $text-muted; }
    #pick-list { height: 1fr; }
    QuestionScreen { align: center middle; }
    #qdialog { width: 64; max-width: 90%; height: auto; border: round $accent;
               background: $surface; padding: 1 2; }
    #q-title { text-style: bold; margin-bottom: 1; }
    #q-options { height: auto; }
    #q-hint { color: $text-muted; margin-top: 1; }
    SecretScreen { align: center middle; }
    #sdialog { width: 64; max-width: 90%; height: auto; border: round $accent;
               background: $surface; padding: 1 2; }
    #s-title { text-style: bold; margin-bottom: 1; }
    #s-hint { color: $text-muted; margin-top: 1; }
    """
    BINDINGS = [
        Binding("ctrl+c", "request_quit", "quit", priority=True),
        Binding("escape", "focus_prompt", show=False),
        Binding("pageup", "scroll_transcript(-1)", show=False, priority=True),
        Binding("pagedown", "scroll_transcript(1)", show=False, priority=True),
    ]

    def __init__(self, engine: SessionEngine | None = None, offer_setup: bool = True) -> None:
        super().__init__()
        self.engine = engine or SessionEngine()
        self.engine.picker_fn = self._pick_from_thread
        self.engine.ask_fn = self._ask_from_thread
        self.engine.secret_fn = self._secret_from_thread
        self._offer_setup = offer_setup
        self.transcript_lines: list[str] = []  # plain mirror, used by tests

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-row"):
            yield Static(LOGO, id="logo")
            yield Static("", id="header-text")
        yield RichLog(id="transcript", wrap=True)
        yield Rule(id="prompt-rule")
        with Horizontal(id="prompt-row"):
            yield Static("›", id="prompt-prefix")
            prompt = PromptInput(placeholder="/help for commands — plain text steers", id="prompt")
            yield prompt
        yield Static("", id="status")
        yield AutoComplete(target=prompt, candidates=self._candidates)

    def _candidates(self, state) -> list[DropdownItem]:
        text = state.text
        if not text.startswith("/") or " " in text:
            return []
        return [DropdownItem(name) for name in sorted(self.engine.commands)]

    def on_mount(self) -> None:
        self._refresh_header()
        self.set_interval(0.5, self._pump)
        self.query_one("#prompt", Input).focus()
        if self._offer_setup:
            self._first_run_setup()

    @work(thread=True, exclusive=False)
    def _first_run_setup(self) -> None:
        lines = self.engine.maybe_first_run_setup()
        if lines:
            self.call_from_thread(self._write_lines, lines)

    def action_focus_prompt(self) -> None:
        """Escape always returns the keyboard to the prompt."""
        self.query_one("#prompt", Input).focus()

    def action_scroll_transcript(self, direction: int) -> None:
        transcript = self.query_one("#transcript", RichLog)
        if direction < 0:
            transcript.scroll_page_up()
        else:
            transcript.scroll_page_down()

    def _refresh_header(self) -> None:
        from chi.userconfig import load_user_config

        coders = load_user_config().default_coders
        if coders:
            models = ", ".join(c.model for c in coders[:3])
            if len(coders) > 3:
                models += f" +{len(coders) - 3}"
            coders_line = f"{len(coders)} coder(s): {models} · /models"
        else:
            coders_line = "no default coders — /models to pick"
        text = Text()
        text.append("chi (χ) ", style="bold")
        text.append(f"v{__version__}\n", style="dim")
        text.append(coders_line + "\n", style="dim")
        text.append(f"{Path.cwd()} · runs {self.engine.runs_root}", style="dim")
        self.query_one("#header-text", Static).update(text)

    # -- transcript ---------------------------------------------------------

    def _style_for(self, line: str) -> str | None:
        if line.startswith(("error:", "⚠")):
            return "yellow" if line.startswith("⚠") else "red"
        if "★ new best" in line:
            return "green bold"
        if line.startswith(("run finished", "run ", "starting run", "resumed ")):
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
        self._refresh_header()

    # -- input --------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "prompt":
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if isinstance(event.input, PromptInput):
            event.input.remember(text)
        self._write(f"❯ {text}")
        self._submit(text)

    @work(thread=True, exclusive=False)
    def _submit(self, text: str) -> None:
        lines = self.engine.submit(text)
        self.call_from_thread(self._write_lines, lines)
        if self.engine.quit_requested:
            self.call_from_thread(self.exit)

    # -- live pump ------------------------------------------------------------

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

    def _telemetry(self, snap: dict) -> str:
        parts: list[str] = []
        if snap["context_pct"] is not None:
            parts.append(f"ctx {snap['context_pct']:.0f}%")
        if snap["tokens_in"] or snap["tokens_out"]:
            parts.append(f"{self._fmt_tokens(snap['tokens_in'])}↑"
                         f" {self._fmt_tokens(snap['tokens_out'])}↓")
        if snap["cost_usd"]:
            parts.append(f"${snap['cost_usd']:.3f}")
        return " · ".join(parts)

    def _pump(self) -> None:
        if self.engine.quit_requested:
            self.exit()
            return
        for line in self.engine.poll_events():
            self._write(line)
        snap = self.engine.snapshot()
        telemetry = self._telemetry(snap)
        if snap["active"]:
            best = snap["best"] if snap["best"] is not None else "—"
            status = f"● running {snap['run_id'] or '(starting)'} · best {best}"
            if telemetry:
                status += f" · {telemetry}"
            status += " · /stop to interrupt · plain text steers"
        else:
            status = "▸ idle"
            if telemetry:
                status += f" · last run {telemetry}"
            status += " · /run start · /resume sessions · /help commands · exit quits"
        self.query_one("#status", Static).update(status)

    # -- modal bridges (called from submit/setup worker threads) -------------

    def _await_modal(self, done: threading.Event, timeout_s: float = 600.0) -> None:
        """Wait for a modal result without ever blocking app shutdown."""
        waited = 0.0
        while not done.wait(0.2):
            waited += 0.2
            if waited >= timeout_s or not self.is_running:
                return

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
        self._await_modal(done)
        return result

    def _ask_from_thread(self, question: str, options: list[tuple[str, str]]) -> str | None:
        result: list[str | None] = []
        done = threading.Event()

        def show() -> None:
            def finished(value: str | None) -> None:
                result.append(value)
                done.set()

            self.push_screen(QuestionScreen(question, options), finished)

        self.call_from_thread(show)
        self._await_modal(done)
        return result[0] if result else None

    def _secret_from_thread(self, prompt: str) -> str | None:
        result: list[str | None] = []
        done = threading.Event()

        def show() -> None:
            def finished(value: str | None) -> None:
                result.append(value)
                done.set()

            self.push_screen(SecretScreen(prompt), finished)

        self.call_from_thread(show)
        self._await_modal(done)
        return result[0] if result else None

    def action_request_quit(self) -> None:
        if self.engine.has_active_run():
            self._write("a run is active — /stop it first")
            return
        self.exit()


def run_app(engine: SessionEngine | None = None) -> None:
    """Launch the full-terminal chi UI."""
    ChiApp(engine).run()
