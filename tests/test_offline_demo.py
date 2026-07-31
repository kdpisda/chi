"""The shipped offline demo must genuinely improve a champion with no API key.

This is chi's "proof it works" path for an evaluator with zero setup: the
`scripted` adapter replays canned candidate sources and really evaluates them,
so a fresh checkout can show a real "new best" without any provider credentials.
"""

import json
from pathlib import Path

from chi.config import load_fleet
from chi.orchestrator.loop import start_run

REPO_ROOT = Path(__file__).parent.parent
OFFLINE_YAML = REPO_ROOT / "examples" / "offline.yaml"
DEMO_CANDIDATES = REPO_ROOT / "examples" / "demo_candidates.json"

# every provider key the adapters might read — the demo must not depend on any
_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY",
)


def test_offline_demo_improves_without_key(tmp_path: Path, monkeypatch) -> None:
    for key in _PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    # run from the repo root so the yaml's relative problem/script paths resolve
    monkeypatch.chdir(REPO_ROOT)

    fleet = load_fleet(OFFLINE_YAML)
    summary = start_run(fleet, runs_root=tmp_path / "runs")

    assert summary.status == "done"
    assert summary.iterations > 0
    assert summary.baseline_score is not None
    assert summary.champion_score is not None
    # the itertools.accumulate rewrite really beats the O(n^2) baseline
    assert summary.champion_score < summary.baseline_score


def test_demo_candidates_are_distinct_and_end_with_accumulate() -> None:
    sources = json.loads(DEMO_CANDIDATES.read_text())
    assert len(sources) >= 2
    # distinct sources => each iteration is a genuinely new evaluation
    assert len(set(sources)) == len(sources)
    # the last candidate is the fast O(n) rewrite that becomes champion
    assert "itertools.accumulate" in sources[-1]
