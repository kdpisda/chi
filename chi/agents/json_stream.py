"""JSON-stream CLI adapter: drive a vendor CLI in structured mode, see everything.

The prompt-file + stdout-grep approach (cli_subprocess) is blind — chi can't tell
what tools the agent ran, can't distinguish a real eval from a parsed artifact,
and can't detect an agent reaching around it (e.g. running popcorn-cli directly).

This adapter runs the CLI in its NDJSON event mode (claude's
`--output-format stream-json --verbose`) and parses the typed event stream, so
chi records exactly which tools the agent called (with a safety flag when a
shell tool invokes a submission binary), and reads real cost/tokens from the
final result event instead of guessing.

Verified against claude's stream-json schema (2026-07):
  {"type":"system","subtype":"init","model":..,"tools":[..]}
  {"type":"assistant","message":{"content":[{"type":"text"|"thinking"|"tool_use",..}]}}
  {"type":"user","message":{"content":[{"type":"tool_result",..}]}}
  {"type":"result","total_cost_usd":..,"usage":{"input_tokens":..,"output_tokens":..},
   "num_turns":..,"is_error":..}
"""

import json
import re
import shlex
import subprocess
import threading
from dataclasses import dataclass, field

from chi.agents.cli_subprocess import PROMPT_TEMPLATE
from chi.agents.protocol import CoderAdapter, IterationOutcome, SeedContext
from chi.store import events

# shell commands that reach a competition/submission binary — flagged when an
# agent runs them directly (defense-in-depth alongside the popcorn shim)
_SUBMISSION_BINARIES = ("popcorn-cli", "popcorn")


def _is_ranked(cmd: str) -> bool:
    """True only for a RANKED submit (mirrors the shim): explicit --mode
    leaderboard, or a `submit` subcommand with no --mode (default may rank).
    popcorn uses `submit` for benchmark too, so bare 'submit' isn't enough."""
    if re.search(r"--mode[= ]leaderboard", cmd):
        return True
    has_mode = re.search(r"--mode[= ]\S+", cmd) is not None
    return re.search(r"(^|\s)submit(\s|$)", cmd) is not None and not has_mode


@dataclass
class StreamResult:
    model: str | None = None
    texts: list[str] = field(default_factory=list)
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)  # (name, input)
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    turns: int = 0
    is_error: bool = False
    # shell tool calls that invoked a submission binary (agent reaching around chi)
    direct_submission_attempts: list[str] = field(default_factory=list)
    ranked_submission_attempts: list[str] = field(default_factory=list)


def _shell_command_of(name: str, tool_input: dict) -> str | None:
    """The command string if this tool call is a shell invocation, else None."""
    if name.lower() in ("bash", "shell", "run", "exec"):
        return str(tool_input.get("command") or tool_input.get("cmd") or "")
    return None


def parse_stream(lines) -> StreamResult:
    """Parse claude-style NDJSON events into a StreamResult (pure, testable)."""
    result = StreamResult()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = obj.get("type")
        if etype == "system" and obj.get("subtype") == "init":
            result.model = obj.get("model")
        elif etype == "assistant":
            for block in obj.get("message", {}).get("content", []):
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    result.texts.append(block["text"])
                elif btype == "tool_use":
                    tool_name = str(block.get("name", ""))
                    tool_input = block.get("input", {}) or {}
                    result.tool_calls.append((tool_name, tool_input))
                    cmd = _shell_command_of(tool_name, tool_input)
                    if cmd and any(b in cmd for b in _SUBMISSION_BINARIES):
                        result.direct_submission_attempts.append(cmd[:200])
                        if _is_ranked(cmd):
                            result.ranked_submission_attempts.append(cmd[:200])
        elif etype == "result":
            result.cost_usd = float(obj.get("total_cost_usd") or 0.0)
            usage = obj.get("usage", {}) or {}
            result.tokens_in = int(usage.get("input_tokens") or 0)
            result.tokens_out = int(usage.get("output_tokens") or 0)
            result.turns = int(obj.get("num_turns") or 0)
            result.is_error = bool(obj.get("is_error"))
    return result


class JsonStreamCliAdapter(CoderAdapter):
    """Drives a CLI in NDJSON mode; records the structured trace, real cost/tokens.

    `command` is a template with {prompt_file}; the adapter appends the CLI's
    JSON-stream flags (default: claude's). Override via `json_flags` in the
    coder's command if a different CLI is used.
    """

    JSON_FLAGS = "--output-format stream-json --verbose"

    def _eval_count(self) -> int:
        rows = self.store.query(
            "SELECT COUNT(*) AS n FROM experiments WHERE run_id=? AND author=?",
            (self.run_id, self.agent_id),
        )
        return int(rows[0]["n"])

    def run_iteration(self, seed: SeedContext) -> IterationOutcome:
        """Run one CLI session in JSON mode; record the trace and real usage."""
        self.ack_steering(seed.steering_hash)
        before = self._eval_count()
        dead = "\n".join(
            f"- [{d['approach_class']}] {d['summary']} (scope: {d['ruled_out_scope']})"
            for d in seed.dead_ends
        ) or "- none yet"
        prompt_dir = self.workdir / ".chi"
        prompt_dir.mkdir(exist_ok=True)
        prompt_file = prompt_dir / "prompt.md"
        prompt_file.write_text(PROMPT_TEMPLATE.format(
            iteration=seed.iteration, candidate=seed.problem.candidate,
            metric=seed.problem.score.metric, direction=seed.problem.score.direction,
            baseline=seed.baseline_score, champion=seed.champion_score,
            mutation_note=seed.mutation_note, steering=seed.steering_text,
            dead_ends=dead, run_dir=self.store.run_dir.resolve(), agent_id=self.agent_id,
        ))

        import os

        from chi.eval.popcorn_shim import agent_env, install_shim

        base = self.command.format(prompt_file=prompt_file.resolve())
        argv = shlex.split(base) + shlex.split(self.JSON_FLAGS)
        shim_dir = self.workdir / ".chi" / "bin"
        install_shim(shim_dir)
        env = agent_env(os.environ, shim_dir)

        stop = threading.Event()

        def _beat() -> None:
            while not stop.wait(self.policies.heartbeat_seconds):
                self.heartbeat()

        thread = threading.Thread(target=_beat, daemon=True)
        thread.start()
        self.heartbeat()
        note = ""
        raw = ""
        try:
            proc = self.sandbox.run(
                argv, self.workdir, env, self.policies.iteration_timeout_seconds)
            raw = proc.stdout or ""
            if proc.returncode != 0:
                note = f"exit {proc.returncode}"
        except subprocess.TimeoutExpired as exc:
            raw = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            note = "timeout"
        finally:
            stop.set()

        parsed = parse_stream(raw.splitlines())
        # record the structured trace: each tool call is now visible to chi
        for tool_name, tool_input in parsed.tool_calls:
            detail = (tool_input.get("command") or tool_input.get("file_path")
                      or tool_input.get("path") or "")
            events.append_event(
                self.store, self.run_id, events.STATUS, agent_id=self.agent_id,
                payload={"tool_call": tool_name, "detail": str(detail)[:80]})
        # a ranked-submission attempt by the agent is a safety event (the shim
        # blocks it, but chi records the attempt so it's never silent)
        for cmd in parsed.ranked_submission_attempts:
            events.append_event(
                self.store, self.run_id, events.STATUS, agent_id=self.agent_id,
                payload={"safety": "agent attempted a direct ranked submission (blocked)",
                         "command": cmd})
        if parsed.is_error and not note:
            note = "agent reported error"

        log = self.store.run_dir / "mirror" / f"agent_{self.agent_id}_iter{seed.iteration}.log"
        log.write_text(f"model={parsed.model}\nnote={note}\nturns={parsed.turns}\n"
                       f"tool_calls={[n for n, _ in parsed.tool_calls]}\n--- raw ---\n{raw}\n")
        return IterationOutcome(
            evals_run=self._eval_count() - before, note=note,
            tokens_in=parsed.tokens_in, tokens_out=parsed.tokens_out,
            cost_usd=parsed.cost_usd,
        )
