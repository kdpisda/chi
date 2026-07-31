"""Interactive chi session: prompt_toolkit REPL over the session engine."""

import threading
import time
from typing import Callable

from chi.session.engine import SessionEngine

BANNER = """\
chi (χ) — autoresearch harness. /help for commands, /quit to leave.
Plain text talks to chi — name a problem directory to start a run."""


def run_repl(
    engine: SessionEngine | None = None,
    prompt_fn: Callable[[], str] | None = None,
) -> None:
    """Run the interactive session until /quit or EOF.

    prompt_fn is injectable for tests; the default builds a prompt_toolkit
    session with slash-command completion and a live event pump.
    """
    engine = engine or SessionEngine()
    print(BANNER)

    stop_pump = threading.Event()

    def _pump() -> None:
        while not stop_pump.wait(1.0):
            for line in engine.poll_events():
                print(line)

    if prompt_fn is None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.patch_stdout import patch_stdout

        from chi.userconfig import history_path

        session: "PromptSession[str]" = PromptSession(
            completer=WordCompleter(sorted(engine.commands), sentence=True),
            history=FileHistory(str(history_path())),
        )

        if engine.ask_fn is None:
            def _ask(question: str, options: list[tuple[str, str]]) -> str | None:
                print(question)
                for n, (_, label) in enumerate(options, start=1):
                    print(f"  {n}. {label}")
                try:
                    raw = input(f"choose 1-{len(options)} (blank cancels): ").strip()
                except (EOFError, KeyboardInterrupt):
                    return None
                if raw.isdigit() and 1 <= int(raw) <= len(options):
                    return options[int(raw) - 1][0]
                return None

            engine.ask_fn = _ask

        if engine.secret_fn is None:
            import getpass

            def _secret(prompt: str) -> str | None:
                try:
                    return getpass.getpass(f"{prompt}: ") or None
                except (EOFError, KeyboardInterrupt):
                    return None

            engine.secret_fn = _secret

        def _default_prompt() -> str:
            return session.prompt("chi> ")

        prompt_fn = _default_prompt
        pump_ctx = patch_stdout
    else:
        from contextlib import nullcontext

        pump_ctx = nullcontext

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()
    try:
        with pump_ctx():
            while not engine.quit_requested:
                try:
                    text = prompt_fn()
                except KeyboardInterrupt:
                    continue  # clear the current line, like other REPLs
                except EOFError:
                    if engine.has_active_run() or engine.has_active_director():
                        what = "a director" if engine.has_active_director() else "a run"
                        print(f"{what} is running — /stop it first (exiting kills it),"
                              " or Ctrl-D again to leave")
                        try:
                            text = prompt_fn()
                        except EOFError:
                            break
                    else:
                        break
                if text.strip() and not text.strip().startswith("/"):
                    print("… chi is working")
                for line in engine.submit(text):
                    print(line)
    finally:
        stop_pump.set()
        # drain anything the pump missed so the transcript is complete
        for line in engine.poll_events():
            print(line)
        time.sleep(0)  # let the pump thread observe the stop event
