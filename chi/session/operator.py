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
    {"type": "function", "function": {
        "name": "explore",
        "description": "Look at the user's machine (read-only): list a directory or"
                       " read the head of a text file. Explore before asking the user.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "fetch",
        "description": "Fetch a URL (GET, text only, truncated) — competition pages,"
                       " docs, problem definitions.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "set_auto_submit",
        "description": "Turn autonomous leaderboard submission on/off for the active"
                       " run's problem. When on, the fleet submits real improvements"
                       " itself (correct + beats current best by margin + rationed)."
                       " Use when the user asks chi to 'keep submitting on its own'.",
        "parameters": {"type": "object",
                       "properties": {"on": {"type": "boolean"},
                                      "current_best": {"type": "number"},
                                      "margin_pct": {"type": "number"}},
                       "required": ["on"]}}},
    {"type": "function", "function": {
        "name": "submit_leaderboard",
        "description": "Submit the current champion to the live leaderboard via"
                       " popcorn-cli. SERIALIZED (one at a time) and rationed;"
                       " REQUIRES the user's approval — it spends their submission"
                       " budget and changes their public rank. Only after a"
                       " benchmark shows a real improvement.",
        "parameters": {"type": "object",
                       "properties": {"reason": {"type": "string"}},
                       "required": ["reason"]}}},
    {"type": "function", "function": {
        "name": "start_director",
        "description": "Start the AUTONOMOUS research director on a problem directory."
                       " It runs the fleet in rounds, meta-reviews results, researches"
                       " when stuck, and re-steers the agents ON ITS OWN until the user"
                       " stops it. Use when the user wants chi to 'keep improving on its"
                       " own' from a single task — do NOT babysit it with per-round steers.",
        "parameters": {"type": "object",
                       "properties": {"problem_dir": {"type": "string"}},
                       "required": ["problem_dir"]}}},
    {"type": "function", "function": {
        "name": "stop_director",
        "description": "Stop the autonomous director at the next round boundary.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "director_status",
        "description": "Director state + running benchmark/$ spend counter (the guardrail"
                       " under run-until-stopped).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "scaffold_problem",
        "description": "Build a chi problem pack (problem.yaml + eval wrappers) by"
                       " delegating to a full-tool setup agent, wrapping an existing"
                       " evaluator found at `source`. Verifies with a baseline eval."
                       " Use after exploring; then start_run the returned directory.",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"},
                                      "source": {"type": "string"},
                                      "notes": {"type": "string"}},
                       "required": ["name", "source"]}}},
]

SYSTEM_PROMPT = """You are chi (χ), an autoresearch harness operator in a terminal session.
You control real runs of coding agents against problems with programmatic evaluators.

Rules:
- Be concise; this is a terminal. No markdown headers, short lines.
- Discover before asking: use explore (dirs/files) and fetch (URLs) instead of
  asking the user to run commands for you. Ask only for what you cannot find:
  auth, budgets, intent.
- start_run needs a directory containing problem.yaml. When the evaluator exists
  but isn't wrapped yet, call scaffold_problem(name, source, notes) — a full-tool
  setup agent writes and verifies the pack — then start_run its directory.
- When the user hands you a task to pursue AUTONOMOUSLY ("keep improving it on its
  own", "run until you beat X", "auto-research this"), use start_director, not
  start_run: the director self-steers (meta-review, research-when-stuck, strategy
  mutation) round after round until stopped. First make sure you have a problem
  directory and a clear goal — ask ONE focused question only if the task is too thin
  to act on; otherwise start it. While a director runs, user direction is folded into
  its next round automatically — you don't need to steer each round.
- While a plain run is active, user direction about the work becomes a steer() call.
- Answer state questions (scores, dead ends, status) from tools only — never invent
  numbers. If a tool errors, relay the error honestly.
- The user can also use slash commands directly; mention one when it is the
  faster path (/models, /setup, /resume).

Configured fleet: {fleet_summary}
"""

MAX_MESSAGES = 40  # hard history cap: system + the most recent turns
MAX_TOOL_ROUNDS = 24  # tool calls per turn before we force a wrap-up reply
FORCE_REPLY = ("You have used your action budget for this turn. Do NOT call another"
               " tool. Reply to the user now: summarize what you found and what you"
               " recommend as the next step.")


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
        """One user turn; activity streams live via engine.emit_progress, reply returned."""
        self.messages.append({"role": "user", "content": text})
        limit = self._context_limit()
        for _ in range(MAX_TOOL_ROUNDS):
            self._trim()
            self.engine.busy_note = f"thinking via {self.model}"
            result = chat(self.model, self.messages, budget=self.budget, role="operator",
                          tools=TOOLS, completion_fn=self.completion_fn)
            self.engine.record_operator_usage(result, limit)
            if limit:
                self.last_context_pct = 100.0 * result.tokens_in / limit
            tool_calls = getattr(result.message, "tool_calls", None)
            if not tool_calls:
                reply = result.text.strip() or "(no reply)"
                self.messages.append({"role": "assistant", "content": reply})
                return [reply]
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
                self.engine.busy_note = f"running {tc.function.name}"
                self.engine.emit_progress(_progress_line(tc.function.name, args))
                output, shown = self._dispatch(tc.function.name, args)
                for line in shown:
                    self.engine.emit_progress(f"  {line}")
                self.messages.append({"role": "tool", "tool_call_id": tc.id,
                                      "content": output})
        # budget spent: one last call with tools withheld to force a real answer
        self.engine.busy_note = f"wrapping up via {self.model}"
        self.messages.append({"role": "user", "content": FORCE_REPLY})
        self._trim()
        result = chat(self.model, self.messages, budget=self.budget, role="operator",
                      completion_fn=self.completion_fn)
        self.engine.record_operator_usage(result, limit)
        reply = result.text.strip() or "(no reply)"
        self.messages.append({"role": "assistant", "content": reply})
        return [reply]

    def _dispatch(self, name: str, args: dict) -> tuple[str, list[str]]:
        return dispatch_tool(self.engine, name, args)


def _progress_line(name: str, args: dict) -> str:
    """One dim transcript line describing a tool call as it starts."""
    shown_args = ", ".join(
        f"{v}" if isinstance(v, (int, float)) else str(v)
        for v in list(args.values())[:2]
    )
    return f"→ {name}({shown_args[:80]})" if shown_args else f"→ {name}()"


def _plain(text: str) -> str:
    """If the CLI wrapped its answer in a reply-action JSON, unwrap it."""
    action = _extract_json(text)
    if action and action.get("action") == "reply" and action.get("text"):
        return str(action["text"])
    return text.strip()


# files chi refuses to read out to an LLM (secrets shouldn't leave the machine)
_SECRET_NAMES = {"credentials.env", ".env", "id_rsa", "id_ed25519", ".netrc",
                 ".pgpass", ".htpasswd"}
_SECRET_HINTS = ("secret", "token", "apikey", "api_key", "private_key", "credential")


def _looks_secret(path) -> bool:
    name = path.name.lower()
    if name in _SECRET_NAMES:
        return True
    return any(hint in name for hint in _SECRET_HINTS)


def explore_path(path_str: str) -> str:
    """Read-only look at the filesystem: directory listing or text-file head.

    Refuses to read obvious secret files — their contents would be sent to the
    operator model (and thus off-machine for API brains).
    """
    from pathlib import Path

    path = Path(path_str).expanduser()
    if not path.exists():
        return f"ERROR: {path} does not exist"
    resolved = path.resolve()  # follow symlinks before the secret check (evasion guard)
    if resolved.is_file() and (_looks_secret(path) or _looks_secret(resolved)):
        return (f"REFUSED: {path.name} looks like a secret; chi won't read credentials"
                " out to the model. Ask the user directly if you need this.")
    if path.is_dir():
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))[:200]
        except OSError as exc:
            return f"ERROR: {exc}"
        lines = [
            f"{'d' if entry.is_dir() else 'f'} {entry.name}"
            for entry in entries
        ]
        return f"dir {path} ({len(lines)} entries):\n" + "\n".join(lines)
    try:
        data = path.read_text(errors="replace")
    except OSError as exc:
        return f"ERROR: {exc}"
    head = data[:8000]
    return f"file {path} ({len(data)} bytes, showing {len(head)}):\n{head}"


def _blocked_host(url: str) -> str | None:
    """Return the host if it resolves to a private/loopback/link-local address (SSRF)."""
    import ipaddress
    import socket
    import urllib.parse

    host = urllib.parse.urlparse(url).hostname
    if not host:
        return "(no host)"
    if host in ("localhost", "localhost.localdomain"):
        return host
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None  # let the actual fetch surface DNS errors
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast):
            return host
    return None


def fetch_url(url: str, opener: Callable | None = None) -> str:
    """GET a URL and return de-tagged text, truncated. opener is a test seam."""
    import re

    if not url.startswith(("http://", "https://")):
        return "ERROR: only http(s) URLs"
    blocked = _blocked_host(url)
    if blocked:
        return f"ERROR: refusing to fetch {blocked} (internal/metadata address)"
    try:
        if opener is not None:
            raw = opener(url)
        else:
            import urllib.error
            import urllib.request

            # no-redirect opener: a 3xx to an internal host would bypass _blocked_host
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):  # noqa: D401,ANN001,ANN002,ANN003
                    return None

            opener_obj = urllib.request.build_opener(_NoRedirect)
            request = urllib.request.Request(url, headers={"User-Agent": "chi/0.1"})
            try:
                with opener_obj.open(request, timeout=20) as response:
                    raw = response.read(200_000).decode("utf-8", errors="replace")
            except urllib.error.HTTPError as http_exc:
                if http_exc.code in (301, 302, 303, 307, 308):
                    location = http_exc.headers.get("Location", "")
                    blocked = _blocked_host(location) if location else None
                    if blocked:
                        return f"ERROR: redirect to internal host {blocked} blocked"
                    return (f"ERROR: {url} redirected ({http_exc.code}) to {location};"
                            " fetch that URL directly if it is safe")
                raise
    except Exception as exc:  # DNS/TLS/HTTP each raise differently; report, don't die
        return f"ERROR: fetch failed: {exc}"
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000] or "(empty page)"


def dispatch_tool(engine: "SessionEngine", name: str, args: dict) -> tuple[str, list[str]]:
    """Run one operator tool; returns (output for the model, lines shown to the user)."""
    if name == "explore":
        return explore_path(str(args.get("path", ""))), []
    if name == "fetch":
        return fetch_url(str(args.get("url", "")), opener=engine.fetch_opener), []
    if name == "set_auto_submit":
        shown = engine.set_auto_submit(
            bool(args.get("on")), args.get("current_best"), args.get("margin_pct"))
        return "\n".join(shown), shown
    if name == "submit_leaderboard":
        shown = engine.submit_leaderboard(str(args.get("reason", "")))
        return "\n".join(shown), shown
    if name == "scaffold_problem":
        shown = engine.scaffold_problem(
            str(args.get("name", "")), str(args.get("source", "")),
            str(args.get("notes", "")),
        )
        return "\n".join(shown), shown
    if name == "start_run":
        shown = engine.launch_problem(str(args.get("problem_dir", "")),
                                      args.get("max_iterations"))
        return "\n".join(shown), shown
    if name == "start_director":
        shown = engine.start_director(str(args.get("problem_dir", "")))
        return "\n".join(shown), shown
    if name == "stop_director":
        shown = engine.stop_director()
        return "\n".join(shown), shown
    if name == "director_status":
        shown = engine.director_status()
        return "\n".join(shown), []
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
{{"action":"start_director","problem_dir":"..."}}  start the AUTONOMOUS director (self-steers until stopped)
{{"action":"stop_director"}}  {{"action":"director_status"}}
{{"action":"list_sessions"}}  {{"action":"resume_session","run_id":"..."}}
{{"action":"explore","path":"..."}}          look at a directory or file (read-only)
{{"action":"fetch","url":"..."}}             fetch a web page (text, truncated)
{{"action":"scaffold_problem","name":"...","source":"...","notes":"..."}}  build+verify a problem pack from an existing evaluator
Never invent scores or state — use the state below and the query actions.
Discover with explore/fetch before asking the user anything they didn't volunteer.

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

    MAX_ACTIONS = 16

    def turn(self, text: str) -> list[str]:
        """One user turn: a chain of actions (explore → … → reply), streamed live."""
        self.history.append(("user", text))
        extra = ""
        for step in range(self.MAX_ACTIONS + 1):
            over_budget = step == self.MAX_ACTIONS
            self.engine.busy_note = (f"wrapping up via {self.cli} CLI" if over_budget
                                     else f"thinking via {self.cli} CLI")
            raw = self.runner(self._prompt(text, extra=extra + (FORCE_REPLY + "\n"
                                                                if over_budget else "")))
            action = _extract_json(raw)
            if over_budget or action is None or action.get("action") in (None, "reply"):
                reply = (action or {}).get("text") or _plain(raw) or "(no reply)"
                self.history.append(("chi", reply))
                return [reply]
            name = str(action.pop("action"))
            self.engine.busy_note = f"running {name}"
            self.engine.emit_progress(_progress_line(name, action))
            output, shown = dispatch_tool(self.engine, name, action)
            for line in shown:
                self.engine.emit_progress(f"  {line}")
            self.history.append(("chi", f"[{name}] {output[:200]}"))
            extra += (f"chi executed {name}; result:\n{output[:1500]}\n"
                      "Respond with another action, or a reply for the user.\n")
        return ["(no reply)"]  # unreachable: the over_budget branch always returns


def fleet_summary_text() -> str:
    """One line describing the configured fleet, for the operator system prompt."""
    from chi.userconfig import load_user_config

    cfg = load_user_config()
    parts = [f"{c.id}={c.model}({c.adapter})" for c in cfg.default_coders]
    parts += [f"{role}={model}" for role, model in cfg.role_models.items()]
    return ", ".join(parts) if parts else "(none yet)"
