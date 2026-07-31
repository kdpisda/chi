"""Turn a round's digest + state into steering + strategy mutation.

Deterministic where it can be (dead-class enforcement, near-miss promotion);
brain-assisted only for inventing a genuinely new strategy on a plateau/stuck.
Writes the operator section of steering.md — the file coders hot-reload — so the
Director steers the fleet without touching coder code.
"""

from pathlib import Path
from typing import Callable

from chi.director.types import DirectorState, RoundDigest, StrategyUpdate

_HEADER = "# Director directives (auto-generated each round)\n"
_OP_MARKER = "## §op"  # operator interjections live under this heading


class Strategist:
    def __init__(self, store, run_id: str, run_dir: Path, direction: str = "minimize",
                 brain_fn: Callable[[str], str] | None = None, *,
                 problem_name: str = "the target problem",
                 problem_description: str = "", metric: str = "score") -> None:
        self._store = store
        self._run_id = run_id
        self._run_dir = Path(run_dir)
        self._direction = direction
        self._brain = brain_fn
        self._name = problem_name
        self._description = problem_description
        self._metric = metric

    def plan(self, digest: RoundDigest, state: DirectorState,
             per_coder_strategy: dict, research_findings: str = "") -> StrategyUpdate:
        lines = [_HEADER, f"Round {digest.round_index}: state = {state.value}.",
                 f"Champion to beat: {digest.champion_score}.", ""]
        if digest.dead_classes:
            lines.append("## DEAD — do not retry")
            for approach in digest.dead_classes:
                mark = " (repeated — hard block)" if approach in digest.repeated_dead_classes else ""
                lines.append(f"- {approach}{mark}")
            lines.append("")
        promoted: list[str] = []
        if digest.near_misses:
            lines.append("## PROMISING bases (near champion — refine these)")
            for near_miss in digest.near_misses:
                lines.append(f"- {near_miss['code_hash']} (score {near_miss['score']})")
                promoted.append(near_miss["code_hash"])
            lines.append("")
        if research_findings:
            lines.append("## Research findings")
            lines.append(research_findings.strip())
            lines.append("")
        strategies = dict(per_coder_strategy)
        if state in (DirectorState.PLATEAUED, DirectorState.STUCK) and self._brain and strategies:
            context = f" Context: {self._description}." if self._description else ""
            prompt = (
                f'You are the research director for the optimization problem "{self._name}".'
                f" Objective: {self._direction} {self._metric}.{context}"
                f" State: {state.value}. Champion {self._metric} {digest.champion_score}."
                f" Dead approaches (do NOT propose these):"
                f" {', '.join(digest.dead_classes) or 'none'}."
                f" {('Research: ' + research_findings) if research_findings else ''}"
                " Reply with ONE short kebab-case label for a genuinely different"
                " approach to try next. Label only, no prose.")
            raw = (self._brain(prompt) or "").strip()
            label = raw.splitlines()[0].strip() if raw else ""
            if label:
                weakest = sorted(strategies)[0]  # deterministic pick without per-coder scores
                strategies[weakest] = label
                lines.append(f"## New direction\n- {weakest}: try `{label}`\n")
        return StrategyUpdate(
            steering_text="\n".join(lines), per_coder_strategy=strategies,
            new_dead_classes=digest.dead_classes, promoted_near_misses=promoted,
            researched=bool(research_findings),
        )

    def apply(self, update: StrategyUpdate) -> None:
        """Write the Director block to steering.md, preserving operator interjections.

        The Director rewrites its directives every round; operator lines (under a
        `## §op` heading) are re-appended so a mid-run interjection is not clobbered.
        """
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / "steering.md"
        preserved = ""
        if path.exists():
            existing = path.read_text()
            if _OP_MARKER in existing:
                preserved = "\n\n" + existing[existing.index(_OP_MARKER):]
        path.write_text(update.steering_text + preserved)
