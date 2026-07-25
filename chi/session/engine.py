"""UI-agnostic session engine: slash commands, live run tail, text steering.

Both the prompt_toolkit REPL and the future Textual frontend consume only
this class. Free text with no active run is the reserved seam for the
conversational LLM operator layer (next iteration).
"""

import json
from pathlib import Path
from typing import Callable

from chi.config import load_fleet
from chi.session.runner import RunHandle
from chi.store.db import Store, utcnow


class SessionEngine:
    """Session state + command dispatch, independent of any terminal UI."""

    def __init__(self, runs_root: Path = Path("runs"), picker_fn: Callable | None = None) -> None:
        self.runs_root = Path(runs_root)
        self.picker_fn = picker_fn
        self.quit_requested = False
        self._handle: RunHandle | None = None
        self._reader: Store | None = None
        self._cursor = 0
        self._best_score: float | None = None
        self._direction = "minimize"
        self._last_run_dir: Path | None = None
        self.commands: dict[str, Callable[[str], list[str]]] = {
            "/help": self._cmd_help,
            "/vendors": self._cmd_vendors,
            "/providers": self._cmd_vendors,
            "/models": self._cmd_models,
            "/run": self._cmd_run,
            "/status": self._cmd_status,
            "/steer": self._cmd_steer,
            "/stop": self._cmd_stop,
            "/ledger": self._cmd_ledger,
            "/champion": self._cmd_champion,
            "/quit": self._cmd_quit,
        }

    # -- public interface ------------------------------------------------

    def submit(self, text: str) -> list[str]:
        """Handle one line of operator input; returns transcript lines."""
        text = text.strip()
        if not text:
            return []
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            handler = self.commands.get(parts[0])
            if handler is None:
                return [f"error: unknown command {parts[0]} — try /help"]
            try:
                return handler(parts[1] if len(parts) > 1 else "")
            except Exception as exc:  # keep the session alive on any command failure
                return [f"error: {exc}"]
        return self._free_text(text)

    def has_active_run(self) -> bool:
        """True while a run is executing."""
        return self._handle is not None and self._handle.alive

    def poll_events(self) -> list[str]:
        """New formatted run events since the last poll (empty when idle)."""
        if self._handle is None:
            return []
        lines: list[str] = []
        if self._handle.error is not None:
            lines.append(f"error: run failed: {self._handle.error}")
            self._handle = None
            return lines
        if self._reader is None:
            if not self._handle.ready.is_set() or self._handle.run_dir is None:
                return []
            self._reader = Store.open(self._handle.run_dir)
            self._last_run_dir = self._handle.run_dir
            lines.append(f"run {self._handle.run_id} started → {self._handle.run_dir}")
        rows = self._reader.query(
            "SELECT * FROM events WHERE event_id > ? ORDER BY event_id", (self._cursor,)
        )
        for row in rows:
            self._cursor = row["event_id"]
            formatted = self._format_event(row)
            if formatted is not None:
                lines.append(formatted)
        if not self._handle.alive and self._handle.summary is not None:
            s = self._handle.summary
            lines.append(
                f"run finished [{s.status}] iterations={s.iterations}"
                f" baseline={s.baseline_score} champion={s.champion_score}"
                f" cost=${s.total_cost_usd:.4f}"
            )
            self._handle = None
            self._reader = None
            self._cursor = 0
            self._best_score = None
        return lines

    # -- event formatting --------------------------------------------------

    def _format_event(self, row) -> str | None:
        type_ = row["type"]
        payload = json.loads(row["payload_json"])
        agent = row["agent_id"] or "-"
        if type_ == "HEARTBEAT":
            return None
        if type_ == "ITERATION_START":
            return f"[{agent}] iteration {payload.get('iteration')} started"
        if type_ == "ITERATION_COMPLETE":
            note = payload.get("note") or ""
            return (f"[{agent}] iteration {payload.get('iteration')} complete —"
                    f" {payload.get('evals_run')} eval(s){' (' + note + ')' if note else ''}")
        if type_ == "RESULT":
            score = payload.get("score_value")
            correct = payload.get("correct")
            if not correct:
                return f"[{agent}] candidate rejected (correctness gate)"
            marker = ""
            if score is not None:
                better = (self._best_score is None
                          or (self._direction == "minimize" and score < self._best_score)
                          or (self._direction == "maximize" and score > self._best_score))
                if better:
                    self._best_score = score
                    marker = "  ★ new best"
            return f"[{agent}] scored {score}{marker}"
        if type_ == "DEAD_END":
            return f"[{agent}] dead end recorded: {payload.get('approach_class')}"
        if type_ == "WATCHDOG_KILL":
            return f"⚠ watchdog killed {agent}: {payload.get('reason')}"
        if type_ == "BUDGET_BLOCK":
            return f"⚠ budget block: {payload.get('reason')}"
        if type_ == "STOP":
            reason = payload.get("reason") or payload.get("status") or ""
            return f"run stop: {reason}" if reason else None
        return None

    # -- commands ----------------------------------------------------------

    def _cmd_help(self, args: str) -> list[str]:
        return [
            "/vendors            select providers (alias /providers)",
            "/models             pick coder models",
            "/run [fleet.yaml]   start a run (default ./fleet.yaml)",
            "/status             run state",
            "/steer <text>       send a steering directive",
            "/stop               stop the active run at the next iteration",
            "/ledger [negative]  show experiments or dead-ends",
            "/champion           show the best candidate",
            "/quit               leave the session",
            "",
            "Plain text while a run is active becomes a steering directive.",
        ]

    def _cmd_vendors(self, args: str) -> list[str]:
        from chi.providers.catalog import list_providers
        from chi.tui.picker import PickerUnavailable, fuzzy_select
        from chi.userconfig import load_user_config, save_user_config

        infos = list_providers(all_providers="all" in args)
        cfg = load_user_config()
        lines = [
            f"{'*' if i.key in cfg.enabled_providers else ' '} {i.key:<14} {i.kind:<4}"
            f" {'ready' if i.ready else i.detail}"
            for i in infos
        ]
        try:
            picked = fuzzy_select(
                "Enable providers (tab to multi-select):",
                [(i.key, f"{i.key} [{i.kind}] {'ready' if i.ready else i.detail}")
                 for i in infos],
                multi=True,
                picker_fn=self.picker_fn,
            )
        except PickerUnavailable:
            return lines + ["(picker unavailable — use `chi providers --enable a,b`)"]
        cfg.enabled_providers = picked
        save_user_config(cfg)
        return lines + [f"enabled: {', '.join(picked) or '(none)'}"]

    def _cmd_models(self, args: str) -> list[str]:
        from chi.cli import _coders_from_picks, _model_label
        from chi.providers.catalog import list_models, list_providers
        from chi.tui.picker import PickerUnavailable, fuzzy_select
        from chi.userconfig import load_user_config, save_user_config

        infos = list_providers()
        cfg = load_user_config()
        ready = [i.key for i in infos if i.ready]
        keys = [k for k in ready if k in cfg.enabled_providers] or ready
        candidates = list_models(keys)
        if not candidates:
            return ["no models available — check /vendors (keys/CLIs missing?)"]
        try:
            picks = fuzzy_select(
                "Pick coder models (tab to multi-select):",
                [(m.id, _model_label(m)) for m in candidates],
                multi=True,
                picker_fn=self.picker_fn,
            )
        except PickerUnavailable:
            return [_model_label(m) for m in candidates] + [
                "(picker unavailable — use `chi models --pick m1,m2`)"
            ]
        if not picks:
            return ["nothing selected"]
        cfg.default_coders = _coders_from_picks(picks, candidates)
        save_user_config(cfg)
        return [f"saved {len(cfg.default_coders)} default coder(s):"] + [
            f"  {c.id}: {c.model} ({c.adapter})" for c in cfg.default_coders
        ]

    def _cmd_run(self, args: str) -> list[str]:
        if self.has_active_run():
            return ["error: a run is already active — /stop it first"]
        fleet_path = Path(args.strip()) if args.strip() else Path("fleet.yaml")
        if not fleet_path.exists():
            return [f"error: {fleet_path} not found — pass a path: /run path/to/fleet.yaml"]
        fleet = load_fleet(fleet_path)
        self._handle = RunHandle(fleet, self.runs_root)
        self._reader = None
        self._cursor = 0
        self._best_score = None
        try:
            from chi.config import load_problem

            self._direction = load_problem(Path(fleet.problem)).score.direction
        except (FileNotFoundError, ValueError):
            self._direction = "minimize"
        self._handle.start()
        return [f"starting run from {fleet_path} …"]

    def _cmd_status(self, args: str) -> list[str]:
        if self.has_active_run() and self._handle is not None:
            return [
                f"run {self._handle.run_id or '(starting)'} active"
                f" — best so far: {self._best_score}",
            ]
        if self._last_run_dir is not None:
            return [f"idle — last run: {self._last_run_dir}"]
        return ["idle — no runs this session"]

    def _cmd_steer(self, args: str) -> list[str]:
        if not args.strip():
            return ["usage: /steer <directive>"]
        return self._free_text(args.strip())

    def _cmd_stop(self, args: str) -> list[str]:
        if not self.has_active_run() or self._handle is None:
            return ["no active run"]
        self._handle.request_stop()
        return ["stop requested — the run ends at the next iteration boundary"]

    def _cmd_ledger(self, args: str) -> list[str]:
        run_dir = self._active_or_last_run_dir()
        if run_dir is None:
            return ["no run to inspect"]
        store = Store.open(run_dir)
        table = "negative_ledger" if "negative" in args else "experiments"
        rows = store.query(f"SELECT * FROM {table} ORDER BY ts")
        if not rows:
            return [f"{table}: empty"]
        return [json.dumps(dict(r)) for r in rows]

    def _cmd_champion(self, args: str) -> list[str]:
        from chi.store import ledger

        run_dir = self._active_or_last_run_dir()
        if run_dir is None:
            return ["no run to inspect"]
        store = Store.open(run_dir)
        runs = store.query("SELECT run_id FROM runs")
        if not runs:
            return ["no run recorded"]
        champ = ledger.champion(store, runs[0]["run_id"], self._direction)
        if champ is None:
            return ["no champion yet"]
        return [f"champion: {champ['score_value']} ({champ['code_hash'][:18]}…)"]

    def _cmd_quit(self, args: str) -> list[str]:
        if self.has_active_run():
            return ["a run is active — /stop it first (or wait for it to finish)"]
        self.quit_requested = True
        return ["bye"]

    # -- free text -----------------------------------------------------------

    def _free_text(self, text: str) -> list[str]:
        if self.has_active_run() and self._handle is not None and self._handle.run_dir:
            steering_path = self._handle.run_dir / "steering.md"
            existing = steering_path.read_text() if steering_path.exists() else ""
            steering_path.write_text(existing + f"\n## §op {utcnow()}\n{text}\n")
            return ["steering directive queued for the next iteration"]
        return [
            "no active run — /run to start one, /help for commands",
            "(conversational mode is coming in the next iteration of chi)",
        ]

    def _active_or_last_run_dir(self) -> Path | None:
        if self._handle is not None and self._handle.run_dir is not None:
            return self._handle.run_dir
        return self._last_run_dir
