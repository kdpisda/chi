import json
import shutil
import stat
from pathlib import Path

from chi.agents.context import build_seed_context
from chi.agents.json_stream import JsonStreamCliAdapter, parse_stream
from chi.config import PoliciesCfg, load_problem
from chi.orchestrator.steering import Steering
from chi.providers.budgets import BudgetTracker
from chi.store.db import Store
from chi.store.events import list_events

PROBLEM_DIR = Path(__file__).parent.parent / "problems" / "optimize_function"


def _stream(*objs) -> list[str]:
    return [json.dumps(o) for o in objs]


def test_parse_extracts_tools_text_and_usage() -> None:
    lines = _stream(
        {"type": "system", "subtype": "init", "model": "claude-opus-4-8", "tools": ["Read"]},
        {"type": "assistant", "message": {"content": [{"type": "thinking"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "candidate.py"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "x"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done."}]}},
        {"type": "result", "total_cost_usd": 0.29, "num_turns": 2, "is_error": False,
         "usage": {"input_tokens": 1500, "output_tokens": 179}},
    )
    r = parse_stream(lines)
    assert r.model == "claude-opus-4-8"
    assert r.tool_calls == [("Read", {"file_path": "candidate.py"})]
    assert r.texts == ["done."]
    assert r.cost_usd == 0.29 and r.tokens_in == 1500 and r.tokens_out == 179
    assert r.turns == 2 and r.is_error is False


def test_parse_detects_direct_and_ranked_submission() -> None:
    lines = _stream(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "popcorn-cli submit c.py --leaderboard cholesky --mode benchmark"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "popcorn-cli submit c.py --leaderboard cholesky --mode leaderboard"}}]}},
        {"type": "result", "total_cost_usd": 0.0, "usage": {}},
    )
    r = parse_stream(lines)
    assert len(r.direct_submission_attempts) == 2  # both touch popcorn
    assert len(r.ranked_submission_attempts) == 1  # only the leaderboard-mode one
    assert "--mode leaderboard" in r.ranked_submission_attempts[0]


def test_parse_ignores_garbage_lines() -> None:
    r = parse_stream(["not json", "", "{bad", json.dumps({"type": "result"})])
    assert r.cost_usd == 0.0 and r.tool_calls == []


def test_adapter_records_trace_and_usage(tmp_path: Path) -> None:
    # a fake CLI that emits a canned stream-json trace, then improves the candidate
    run_dir = tmp_path / "run"
    store = Store.open(run_dir)
    shutil.copytree(PROBLEM_DIR, run_dir / "workdir")
    store.execute("INSERT INTO agents (agent_id, run_id, adapter, model, started_at)"
                  " VALUES ('claude-A','run','json_stream','claude','2026-01-01T00:00:00Z')")
    prob = load_problem(run_dir / "workdir")

    stream = "\n".join(_stream(
        {"type": "system", "subtype": "init", "model": "claude-opus-4-8"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "popcorn-cli submit c.py --mode leaderboard"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "improved"}]}},
        {"type": "result", "total_cost_usd": 0.12, "num_turns": 3, "is_error": False,
         "usage": {"input_tokens": 900, "output_tokens": 50}},
    ))
    fake = tmp_path / "fake_claude.sh"
    fake.write_text(f"#!/bin/bash\ncat <<'EOF'\n{stream}\nEOF\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    adapter = JsonStreamCliAdapter(
        store=store, run_id="run", agent_id="claude-A", model="claude",
        workdir=run_dir / "workdir", problem=prob, budget=BudgetTracker(total_usd=1.0),
        policies=PoliciesCfg(), command=f"{fake} {{prompt_file}}")
    state = Steering(store, "run").refresh()
    seed = build_seed_context(store, "run", prob, run_dir / "workdir", state, 0, None)
    out = adapter.run_iteration(seed)

    # real usage came from the structured result event, not a guess
    assert out.cost_usd == 0.12 and out.tokens_in == 900 and out.tokens_out == 50
    # chi recorded the tool call it saw
    statuses = list_events(store, "run", "STATUS")
    assert any('"tool_call": "Bash"' in s["payload_json"] for s in statuses)
    # the agent's direct ranked-submission attempt was recorded as a safety event
    assert any('"safety"' in s["payload_json"] for s in statuses)
