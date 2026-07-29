"""Web-capable research call, fired only when the Director is stuck.

Routes through the CLI brain (its web access is the vendor CLI's own — spec
decision). Always degrades to "" on no brain / empty / error, so a research
failure never sinks the loop.
"""

from typing import Callable

_PROMPT = (
    "You are the research director for a batched dense Cholesky factorization CUDA"
    " kernel targeting an NVIDIA B200. You are STUCK at {champ}µs (geomean). These"
    " approach classes are already ruled out — do NOT suggest them: {dead}."
    " Search the web if you can (CUDA C++ / Blackwell microarchitecture / cuSOLVER /"
    " recent batched-cholesky papers) and report 3-5 CONCRETE, genuinely different"
    " techniques worth trying next, each one line. Techniques only, no preamble.")


class Researcher:
    def __init__(self, brain_fn: Callable[[str], str] | None = None,
                 max_chars: int = 2000) -> None:
        self._brain = brain_fn
        self._max = max_chars

    def research(self, champion_score: float, dead_classes: list) -> str:
        """One brain call for new ideas; "" on no brain / empty / error."""
        if self._brain is None:
            return ""
        prompt = _PROMPT.format(champ=champion_score,
                                dead=", ".join(dead_classes) or "none")
        try:
            out = self._brain(prompt) or ""
        except Exception:
            return ""
        return out.strip()[: self._max]
