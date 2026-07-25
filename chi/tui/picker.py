"""Fuzzy selection wrapper: InquirerPy in a TTY, injectable everywhere else."""

import sys
from typing import Callable


class PickerUnavailable(RuntimeError):
    """Raised when no interactive terminal is available for the fuzzy picker."""


def fuzzy_select(
    message: str,
    choices: list[tuple[str, str]],
    multi: bool = False,
    picker_fn: Callable | None = None,
) -> list[str]:
    """Fuzzy-pick from (value, label) choices; returns selected values as a list."""
    if picker_fn is not None:
        result = picker_fn(message, choices, multi)
        return list(result) if isinstance(result, (list, tuple)) else [result]
    if not sys.stdin.isatty():
        raise PickerUnavailable("no interactive terminal; use the non-interactive flags")
    from InquirerPy import inquirer

    prompt = inquirer.fuzzy(
        message=message,
        choices=[{"value": value, "name": label} for value, label in choices],
        multiselect=multi,
    )
    result = prompt.execute()
    return list(result) if isinstance(result, list) else [result]
