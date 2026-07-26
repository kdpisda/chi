"""Minimal tools-in-a-loop coder over LiteLLM (any API provider)."""

import json
from pathlib import Path
from typing import Any, Callable

from chi.agents.protocol import CoderAdapter, IterationOutcome, SeedContext
from chi.eval.runner import evaluate
from chi.providers.llm import chat
from chi.store import ledger

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file inside the working directory.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Overwrite a file inside the working directory.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"},
                                      "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_eval",
        "description": "Evaluate the current candidate (correctness gate + benchmark).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "query_knowledge",
        "description": "Search prior experiments and the negative ledger.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "report_deadend",
        "description": "Record a ruled-out approach WITH evidence (measured numbers).",
        "parameters": {"type": "object",
                       "properties": {"approach_class": {"type": "string"},
                                      "summary": {"type": "string"},
                                      "evidence_json": {"type": "string"}},
                       "required": ["approach_class", "summary", "evidence_json"]}}},
]

SYSTEM_PROMPT = """You are a coder agent in the Chi autoresearch harness.
Goal: improve the candidate's score ({metric}, {direction}) while staying correct.
Work protocol (mandatory):
1. Check the dead-ends list below before proposing an approach; do not retry ruled-out
   approaches without a distinguishing hypothesis.
2. Edit ONLY {candidate} via write_file, then call run_eval to measure.
3. If an approach measurably fails, call report_deadend with the numbers.
Current state:
- iteration: {iteration}
- baseline score: {baseline}
- champion score: {champion}
{mutation_note}
Steering directive (obey it):
{steering}

Dead ends (do not retry):
{dead_ends}

Current candidate source:
```python
{candidate_code}
```"""


class LiteLLMLoopAdapter(CoderAdapter):
    """Tools-in-a-loop over any LiteLLM-routable model."""

    CONTEXT_GUARD = 0.8  # stop the iteration when the prompt passes this fill ratio

    def __init__(self, *args: Any, completion_fn: Callable | None = None,
                 context_limit: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.completion_fn = completion_fn
        self.max_tool_calls = 15
        self._context_limit = context_limit

    def context_limit(self) -> int | None:
        """Model context size in tokens (explicit override, else litellm registry)."""
        if self._context_limit is not None:
            return self._context_limit
        try:
            import litellm

            info = litellm.model_cost.get(self.model) or {}
            limit = info.get("max_input_tokens") or info.get("max_tokens")
            return int(limit) if limit else None
        except (ImportError, TypeError, ValueError):
            return None

    def run_iteration(self, seed: SeedContext) -> IterationOutcome:
        """One improvement session: chat until no tool calls or the cap hits."""
        self.ack_steering(seed.steering_hash)
        self._strategy = seed.strategy
        self.heartbeat()
        dead = "\n".join(
            f"- [{d['approach_class']}] {d['summary']} (scope: {d['ruled_out_scope']})"
            for d in seed.dead_ends
        ) or "- none yet"
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(
                metric=seed.problem.score.metric, direction=seed.problem.score.direction,
                candidate=seed.problem.candidate, iteration=seed.iteration,
                baseline=seed.baseline_score, champion=seed.champion_score,
                mutation_note=seed.mutation_note, steering=seed.steering_text,
                dead_ends=dead, candidate_code=seed.candidate_code)},
            {"role": "user", "content": "Improve the candidate now. Use the tools."},
        ]
        evals_run = 0
        calls = 0
        tokens_in = tokens_out = 0
        cost_usd = 0.0
        context_pct: float | None = None
        note = ""
        limit = self.context_limit()
        while calls < self.max_tool_calls:
            result = chat(self.model, messages, budget=self.budget, tools=TOOLS,
                          completion_fn=self.completion_fn)
            self.heartbeat()
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            cost_usd += result.cost_usd
            if limit:
                context_pct = 100.0 * result.tokens_in / limit
                if result.tokens_in >= self.CONTEXT_GUARD * limit:
                    # per-design context strategy: end the iteration; the next one
                    # respawns from a fresh seed context built from the store
                    note = f"context guard at {context_pct:.0f}% — fresh context next iteration"
                    break
            tool_calls = getattr(result.message, "tool_calls", None)
            if not tool_calls:
                break
            messages.append({"role": "assistant", "content": result.text or None,
                             "tool_calls": [
                                 {"id": tc.id, "type": "function",
                                  "function": {"name": tc.function.name,
                                               "arguments": tc.function.arguments}}
                                 for tc in tool_calls]})
            for tc in tool_calls:
                calls += 1
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                output, did_eval = self._dispatch(name, args)
                evals_run += int(did_eval)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})
        return IterationOutcome(
            evals_run=evals_run, note=note, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=cost_usd, context_pct=context_pct,
        )

    def _safe_path(self, rel: str) -> Path | None:
        """Resolve a path inside the workdir; None if it escapes."""
        target = (self.workdir / rel).resolve()
        return target if target.is_relative_to(self.workdir.resolve()) else None

    def _dispatch(self, name: str, args: dict) -> tuple[str, bool]:
        """Execute one tool call; returns (output-for-model, did_eval)."""
        if name == "read_file":
            path = self._safe_path(str(args.get("path", "")))
            if path is None or not path.exists():
                return "ERROR: path outside workdir or missing", False
            return path.read_text(), False
        if name == "write_file":
            path = self._safe_path(str(args.get("path", "")))
            if path is None:
                return "ERROR: path outside workdir", False
            path.write_text(str(args.get("content", "")))
            return "written", False
        if name == "run_eval":
            result = evaluate(self.problem, self.workdir, store=self.store,
                              run_id=self.run_id, agent_id=self.agent_id,
                              strategy=getattr(self, "_strategy", None))
            from dataclasses import asdict

            return json.dumps(asdict(result)), True
        if name == "query_knowledge":
            return json.dumps(
                ledger.query_knowledge(self.store, self.run_id, str(args.get("query", "")))
            ), False
        if name == "report_deadend":
            try:
                evidence = json.loads(str(args.get("evidence_json", "")))
            except json.JSONDecodeError:
                evidence = {}
            if not evidence:
                return "REJECTED: evidence_json must be non-empty JSON", False
            neg_id = ledger.add_negative(
                self.store, self.run_id, approach_class=str(args["approach_class"]),
                summary=str(args["summary"]), evidence=evidence, authored_by=self.agent_id,
            )
            return neg_id, False
        return f"ERROR: unknown tool {name}", False
