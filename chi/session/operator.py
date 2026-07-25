"""The conversational operator: chi's own brain for the interactive session.

Free text in the session goes here. The operator is an LLM with tools over the
engine — it starts runs, steers them, and answers questions from the store.
It never invents numbers: state answers come from tools.
"""

import json
from typing import TYPE_CHECKING, Any, Callable

from chi.providers.budgets import BudgetTracker
from chi.providers.llm import chat

if TYPE_CHECKING:
    from chi.session.engine import SessionEngine

TOOLS = [
    {"type": "function", "function": {
        "name": "start_run",
        "description": "Start an autoresearch run on a problem directory (must contain"
                       " problem.yaml). Uses the user's configured default coders.",
        "parameters": {"type": "object",
                       "properties": {"problem_dir": {"type": "string"},
                                      "max_iterations": {"type": "integer"}},
                       "required": ["problem_dir"]}}},
    {"type": "function", "function": {
        "name": "run_status",
        "description": "Current run state: active?, run id, best score so far.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_champion",
        "description": "Best correct candidate of the active/attached run.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "query_ledger",
        "description": "Search the run's experiments and negative ledger (dead ends).",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                       "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "steer",
        "description": "Send a steering directive to the active (or attached) run's"
                       " agents. Use when the user gives direction about the work.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                       "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "stop_run",
        "description": "Stop the active run at the next iteration boundary.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_sessions",
        "description": "List past sessions (all directories, system-wide).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "resume_session",
        "description": "Attach to a past session by run_id.",
        "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}},
                       "required": ["run_id"]}}},
]

SYSTEM_PROMPT = """You are chi (χ), an autoresearch harness operator in a terminal session.
You control real runs of coding agents against problems with programmatic evaluators.

Rules:
- Be concise; this is a terminal. No markdown headers, short lines.
- When the user points at a problem directory or asks to optimize something that has
  an evaluator, call start_run. If they name no directory, ask which one.
- While a run is active, user direction about the work becomes a steer() call.
- Answer state questions (scores, dead ends, status) from tools only — never invent
  numbers. If a tool errors, relay the error honestly.
- The user can also use slash commands directly; mention one when it is the
  faster path (/models, /setup, /resume).

Configured fleet: {fleet_summary}
"""

MAX_MESSAGES = 40  # hard history cap: system + the most recent turns
MAX_TOOL_ROUNDS = 8


class OperatorChat:
    """Persistent conversation with tool access to one session engine."""

    def __init__(
        self,
        engine: "SessionEngine",
        model: str,
        budget: BudgetTracker,
        completion_fn: Callable | None = None,
        fleet_summary: str = "",
    ) -> None:
        self.engine = engine
        self.model = model
        self.budget = budget
        self.completion_fn = completion_fn
        self.messages: list[dict] = [
            {"role": "system",
             "content": SYSTEM_PROMPT.format(fleet_summary=fleet_summary or "(none yet)")},
        ]
        self.last_context_pct: float | None = None

    def _context_limit(self) -> int | None:
        try:
            import litellm

            info = litellm.model_cost.get(self.model) or {}
            limit = info.get("max_input_tokens") or info.get("max_tokens")
            return int(limit) if limit else None
        except (ImportError, TypeError, ValueError):
            return None

    def _trim(self) -> None:
        """Keep the system prompt plus the newest messages under the hard cap."""
        if len(self.messages) > MAX_MESSAGES:
            self.messages = [self.messages[0]] + self.messages[-(MAX_MESSAGES - 1):]

    def turn(self, text: str) -> list[str]:
        """One user turn; returns transcript lines (tool activity + reply)."""
        self.messages.append({"role": "user", "content": text})
        lines: list[str] = []
        limit = self._context_limit()
        for _ in range(MAX_TOOL_ROUNDS):
            self._trim()
            result = chat(self.model, self.messages, budget=self.budget, role="operator",
                          tools=TOOLS, completion_fn=self.completion_fn)
            self.engine.record_operator_usage(result, limit)
            if limit:
                self.last_context_pct = 100.0 * result.tokens_in / limit
            tool_calls = getattr(result.message, "tool_calls", None)
            if not tool_calls:
                reply = result.text.strip() or "(no reply)"
                self.messages.append({"role": "assistant", "content": reply})
                lines.append(reply)
                return lines
            self.messages.append({"role": "assistant", "content": result.text or None,
                                  "tool_calls": [
                                      {"id": tc.id, "type": "function",
                                       "function": {"name": tc.function.name,
                                                    "arguments": tc.function.arguments}}
                                      for tc in tool_calls]})
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                output, shown = self._dispatch(tc.function.name, args)
                lines.extend(shown)
                self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                      "content": output})
        lines.append("(operator hit the tool-call cap for one turn — ask again)")
        return lines

    def _dispatch(self, name: str, args: dict) -> tuple[str, list[str]]:
        return dispatch_tool(self.engine, name, args)


def dispatch_tool(engine: "SessionEngine", name: str, args: dict) -> tuple[str, list[str]]:
    """Run one operator tool; returns (output for the model, lines shown to the user)."""
    if name == "start_run":
        shown = engine.launch_problem(str(args.get("problem_dir", "")),
                                      args.get("max_iterations"))
        return "\n".join(shown), shown
    if name == "run_status":
        return json.dumps(engine.snapshot()), []
    if name == "get_champion":
        shown = engine.commands["/champion"]("")
        return "\n".join(shown), []
    if name == "query_ledger":
        shown = engine.commands["/ledger"](str(args.get("text", "")))
        return "\n".join(shown[:20]), []
    if name == "steer":
        shown = engine.commands["/steer"](str(args.get("text", "")))
        return "\n".join(shown), shown
    if name == "stop_run":
        shown = engine.commands["/stop"]("")
        return "\n".join(shown), shown
    if name == "list_sessions":
        from chi.userconfig import list_sessions

        return json.dumps(list_sessions()[:15]), []
    if name == "resume_session":
        shown = engine.commands["/resume"](str(args.get("run_id", "")))
        return "\n".join(shown), shown
    return f"ERROR: unknown tool {name}", []


CLI_PROMPT = """You are chi (χ), an autoresearch harness operator in a terminal session.
You control real runs of coding agents against problems with programmatic evaluators.
Reply with ONLY one JSON object, nothing else. Available actions:
{{"action":"reply","text":"..."}}                       answer the user (concise, terminal)
{{"action":"start_run","problem_dir":"...","max_iterations":0}}   start a run (dir has problem.yaml; max_iterations optional)
{{"action":"run_status"}}  {{"action":"get_champion"}}  {{"action":"query_ledger","text":"..."}}
{{"action":"steer","text":"..."}}  {{"action":"stop_run"}}
{{"action":"list_sessions"}}  {{"action":"resume_session","run_id":"..."}}
Never invent scores or state — use the state below and the query actions.

State: {state}
Fleet: {fleet_summary}

Conversation so far:
{history}
user: {text}

JSON:"""


def _default_cli_runner(cli: str) -> Callable[[str], str]:
    """Run one prompt through a vendor CLI, returning its stdout text."""
    import subprocess

    def run(prompt: str) -> str:
        commands = {"claude": ["claude", "-p", prompt],
                    "codex": ["codex", "exec", prompt]}
        proc = subprocess.run(commands[cli], capture_output=True, text=True, timeout=240)
        return proc.stdout.strip() or proc.stderr.strip()

    return run


def _extract_json(text: str) -> dict | None:
    """First parseable JSON object in text, else None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start:end + 1])
                        return parsed if isinstance(parsed, dict) else None
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


class CliOperatorChat:
    """Operator brain driven by a vendor CLI (claude/codex) — no API key needed.

    The CLI is used for pure text generation with a JSON-action protocol;
    chi executes the actions itself, so the CLI needs no tool permissions.
    """

    def __init__(
        self,
        engine: "SessionEngine",
        cli: str,
        runner: Callable[[str], str] | None = None,
        fleet_summary: str = "",
    ) -> None:
        self.engine = engine
        self.cli = cli
        self.model = f"{cli} (cli)"
        self.runner = runner or _default_cli_runner(cli)
        self.fleet_summary = fleet_summary or "(none yet)"
        self.history: list[tuple[str, str]] = []  # (speaker, text), trimmed
        self.last_context_pct = None

    def _prompt(self, text: str, extra: str = "") -> str:
        history = "\n".join(f"{who}: {what}" for who, what in self.history[-20:])
        return CLI_PROMPT.format(
            state=json.dumps(self.engine.snapshot()),
            fleet_summary=self.fleet_summary,
            history=history + (f"\n{extra}" if extra else ""),
            text=text,
        )

    def turn(self, text: str) -> list[str]:
        """One user turn: at most one action plus one follow-up reply."""
        self.history.append(("user", text))
        raw = self.runner(self._prompt(text))
        action = _extract_json(raw)
        if action is None or action.get("action") in (None, "reply"):
            reply = (action or {}).get("text") or raw or "(no reply)"
            self.history.append(("chi", reply))
            return [reply]
        name = str(action.pop("action"))
        output, shown = dispatch_tool(self.engine, name, action)
        self.history.append(("chi", f"[{name}] {output[:200]}"))
        followup_raw = self.runner(
            self._prompt(text, extra=f"chi executed {name}; result: {output[:500]}\n"
                                     "Now respond to the user with a reply action.")
        )
        followup = _extract_json(followup_raw)
        reply = (followup or {}).get("text") or followup_raw or "(done)"
        self.history.append(("chi", reply))
        return shown + [reply]


def fleet_summary_text() -> str:
    """One line describing the configured fleet, for the operator system prompt."""
    from chi.userconfig import load_user_config

    cfg = load_user_config()
    parts = [f"{c.id}={c.model}({c.adapter})" for c in cfg.default_coders]
    parts += [f"{role}={model}" for role, model in cfg.role_models.items()]
    return ", ".join(parts) if parts else "(none yet)"
