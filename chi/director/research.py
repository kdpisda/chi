"""Web-capable research call, fired only when the Director is stuck.

Routes through the CLI brain (its web access is the vendor CLI's own — spec
decision). Always degrades to "" on no brain / empty / error, so a research
failure never sinks the loop.
"""

from typing import Callable

_PROMPT = (
    'You are the research director for the optimization problem "{name}".\n'
    "{context}"
    "Objective: {direction} {metric}. You are STUCK — the current champion"
    " {metric} is {champ}. These approach classes are already ruled out — do NOT"
    " suggest them: {dead}. Search the web if you can for techniques relevant to"
    " THIS problem and report 3-5 CONCRETE, genuinely different techniques worth"
    " trying next, each one line. Techniques only, no preamble.")


class Researcher:
    def __init__(self, brain_fn: Callable[[str], str] | None = None,
                 max_chars: int = 2000, *,
                 problem_name: str = "the target problem",
                 problem_description: str = "", metric: str = "score",
                 direction: str = "minimize") -> None:
        self._brain = brain_fn
        self._max = max_chars
        self._name = problem_name
        self._description = problem_description
        self._metric = metric
        self._direction = direction

    def research(self, champion_score: float, dead_classes: list) -> str:
        """One brain call for new ideas; "" on no brain / empty / error."""
        if self._brain is None:
            return ""
        context = f"Context: {self._description}\n" if self._description else ""
        prompt = _PROMPT.format(name=self._name, context=context,
                                metric=self._metric, direction=self._direction,
                                champ=champion_score,
                                dead=", ".join(dead_classes) or "none")
        try:
            out = self._brain(prompt) or ""
        except Exception:
            return ""
        return out.strip()[: self._max]
