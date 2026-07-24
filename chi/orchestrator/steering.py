"""Two-layer steering: harness-generated digest + optional operator overrides."""

import hashlib
from dataclasses import dataclass

from chi.store import events, ledger
from chi.store.db import Store

TEMPLATE = """# Operator directives (optional)

Chi runs unattended; edit this file only when you want to redirect the run.
Conventions (field-proven): numbered directives (`## §1 ...`); to override an
earlier directive write `SUPERSEDES §N` in the new one; list forbidden
approaches under a `DEAD — do not retry` heading.

<!-- Write directives below this line. -->
"""


@dataclass
class SteeringState:
    text: str
    operator_hash: str


class Steering:
    """Builds the directive agents run under; watches the operator file."""

    def __init__(self, store: Store, run_id: str, direction: str = "minimize") -> None:
        self._store = store
        self._run_id = run_id
        self._direction = direction
        self._path = store.run_dir / "steering.md"
        self._last_hash: str | None = None

    def refresh(self) -> SteeringState:
        """Re-read operator file (creating from template), rebuild combined text."""
        if not self._path.exists():
            self._path.write_text(TEMPLATE)
        operator_text = self._path.read_text()
        operator_hash = "sha256:" + hashlib.sha256(operator_text.encode()).hexdigest()
        if operator_hash != self._last_hash:
            events.append_event(
                self._store, self._run_id, events.STEER_UPDATE,
                payload={"steering_hash": operator_hash, "content": operator_text},
            )
            self._last_hash = operator_hash
        combined = self._auto_digest() + "\n\n---\n\n" + operator_text
        return SteeringState(text=combined, operator_hash=operator_hash)

    def _auto_digest(self) -> str:
        """Deterministic run-state digest: champion, negatives, focus."""
        champ = ledger.champion(self._store, self._run_id, self._direction)
        negatives = ledger.list_negatives(self._store, self._run_id)
        lines = [f"# Auto-steering digest (run {self._run_id})"]
        if champ is None:
            lines.append("- Champion: none yet — establish a correct improving candidate.")
        else:
            lines.append(
                f"- Champion: {champ['score_value']} ({champ['score_metric']})"
                f" hash={champ['code_hash'][:18]}…"
            )
        if negatives:
            classes = ", ".join(sorted({n["approach_class"] for n in negatives}))
            lines.append(f"- Ruled out ({len(negatives)}): {classes} — do not retry without"
                         " a distinguishing hypothesis (chi challenge).")
        else:
            lines.append("- Ruled out: nothing yet.")
        lines.append("- Focus: beat the champion; record every attempt honestly.")
        return "\n".join(lines)
