"""UI-agnostic session engine: slash commands, live run tail, text steering.

Both the prompt_toolkit REPL and the future Textual frontend consume only
this class. Free text with no active run is the reserved seam for the
conversational LLM operator layer (next iteration).
"""

import json
import threading
from pathlib import Path
from typing import Callable

from chi.config import load_fleet
from chi.session.runner import RunHandle
from chi.store.db import Store, utcnow


def _short_error(exc: Exception) -> str:
    """Human-sized error text: strip provider JSON bodies, collapse whitespace."""
    text = f"{type(exc).__name__}: {exc}"
    brace = text.find("{")
    if brace > 0:
        text = text[:brace].rstrip(" -:(")
    text = " ".join(text.split())
    return text[:200] if text else type(exc).__name__


class SessionEngine:
    """Session state + command dispatch, independent of any terminal UI."""

    def __init__(self, runs_root: Path | None = None, picker_fn: Callable | None = None) -> None:
        from chi.userconfig import default_runs_root

        self.runs_root = Path(runs_root) if runs_root is not None else default_runs_root()
        self.picker_fn = picker_fn
        self.ask_fn: Callable | None = None  # frontends: (question, options) -> value|None
        self.secret_fn: Callable | None = None  # frontends: (prompt) -> secret|None
        self.completion_fn: Callable | None = None  # test seam for the operator LLM
        self.cli_runner_fn: Callable | None = None  # test seam for the CLI operator
        self.busy_note: str | None = None  # what a long operation is doing (frontends show it)
        self._progress: list[str] = []  # live activity lines, drained by poll_events
        self._progress_lock = threading.Lock()
        self._operator_chat = None
        self.quit_requested = False
        self._quit_after_stop = False
        self._handle: RunHandle | None = None
        self._reader: Store | None = None
        self._cursor = 0
        self._best_score: float | None = None
        self._tokens_in = 0
        self._tokens_out = 0
        self._cost_usd = 0.0
        self._context_pct: float | None = None
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
            "/exit": self._cmd_quit,
            "/resume": self._cmd_resume,
            "/setup": self._cmd_setup,
            "/setkey": self._cmd_setkey,
        }

    # -- public interface ------------------------------------------------

    def submit(self, text: str) -> list[str]:
        """Handle one line of operator input; returns transcript lines."""
        text = text.strip()
        if not text:
            return []
        if text.lower() in ("exit", "quit"):
            return self._cmd_quit("")
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            handler = self.commands.get(parts[0])
            if handler is None:
                return [f"error: unknown command {parts[0]} — try /help"]
            try:
                return handler(parts[1] if len(parts) > 1 else "")
            except Exception as exc:  # keep the session alive on any command failure
                return [f"error: {_short_error(exc)}"]
        try:
            return self._free_text(text)
        except Exception as exc:  # never leak raw provider JSON into the transcript
            return [f"error: {_short_error(exc)}"]

    def has_active_run(self) -> bool:
        """True while a run is executing."""
        return self._handle is not None and self._handle.alive

    def snapshot(self) -> dict:
        """Cheap status snapshot for frontends (status bars, headers)."""
        return {
            "active": self.has_active_run(),
            "run_id": self._handle.run_id if self._handle is not None else None,
            "best": self._best_score,
            "tokens_in": self._tokens_in,
            "tokens_out": self._tokens_out,
            "cost_usd": self._cost_usd,
            "context_pct": self._context_pct,
        }

    def emit_progress(self, line: str) -> None:
        """Push one live activity line (thread-safe; frontends drain via poll_events)."""
        with self._progress_lock:
            self._progress.append(line)

    def _drain_progress(self) -> list[str]:
        with self._progress_lock:
            drained, self._progress = self._progress, []
        return drained

    def poll_events(self) -> list[str]:
        """New activity + formatted run events since the last poll."""
        lines: list[str] = self._drain_progress()
        if self._handle is None:
            return lines
        if self._handle.error is not None:
            lines.append(f"error: run failed: {self._handle.error}")
            self._handle = None
            return lines
        if self._reader is None:
            if not self._handle.ready.is_set() or self._handle.run_dir is None:
                return lines  # keep already-drained progress lines
            self._reader = Store.open(self._handle.run_dir)
            self._last_run_dir = self._handle.run_dir
            lines.append(f"run {self._handle.run_id} started → {self._handle.run_dir}")
        rows = self._reader.query(
            "SELECT * FROM events WHERE event_id > ? ORDER BY event_id", (self._cursor,)
        )
        for row in rows:
            self._cursor = row["event_id"]
            self._tokens_in += row["tokens_in"]
            self._tokens_out += row["tokens_out"]
            self._cost_usd += row["cost_usd"]
            if row["type"] == "ITERATION_COMPLETE":
                pct = json.loads(row["payload_json"]).get("context_pct")
                if pct is not None:
                    self._context_pct = pct
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
            if self._quit_after_stop:
                self._quit_after_stop = False
                self.quit_requested = True
                lines.append("bye")
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
            "/resume [run_id]    attach to any past session, from any directory",
            "/setup              apply the recommended model setup for this machine",
            "/setkey <provider>  store an API key (masked input, saved 0600)",
            "/models roles       assign models to orchestrator/planner/critic/researcher",
            "/quit               leave the session (also: exit, quit, /exit)",
            "",
            "Plain text talks to chi: name a problem directory to start a run,",
            "give direction mid-run (chi steers the agents), ask about results.",
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
        self._operator_chat = None
        return lines + [f"enabled: {', '.join(picked) or '(none)'}"]

    def _candidate_models(self) -> list:
        from chi.providers.catalog import list_models, list_providers
        from chi.userconfig import load_user_config

        infos = list_providers()
        cfg = load_user_config()
        ready = [i.key for i in infos if i.ready]
        keys = [k for k in ready if k in cfg.enabled_providers] or ready
        return list_models(keys)

    def _cmd_models(self, args: str) -> list[str]:
        if args.strip().split()[:1] == ["roles"]:
            return self._models_for_roles()
        from chi.cli import _model_label
        from chi.config import CoderCfg
        from chi.providers.catalog import CLI_MODEL_CHOICES, cli_command
        from chi.tui.picker import PickerUnavailable, fuzzy_select
        from chi.userconfig import load_user_config, save_user_config

        candidates = self._candidate_models()
        if not candidates:
            return ["no models available — /vendors to check providers,"
                    " /setkey <provider> to store a key"]
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
        by_id = {m.id: m for m in candidates}
        coders: list[CoderCfg] = []
        for n, pick in enumerate(picks, start=1):
            info = by_id.get(pick)
            if info is None or info.kind == "api":
                coders.append(CoderCfg(id=f"c{n}", model=pick, adapter="litellm_loop"))
                continue
            variants = CLI_MODEL_CHOICES.get(pick, ["default"])
            choice = "default"
            if len(variants) > 1:
                choice = self._ask(
                    f"Which model should the {pick} CLI use?",
                    [(v, v if v != "default" else "default (the CLI's own setting)")
                     for v in variants],
                ) or "default"
            model_name = pick if choice == "default" else f"{pick}:{choice}"
            coders.append(CoderCfg(id=f"c{n}", model=model_name, adapter="cli_subprocess",
                                   command=cli_command(pick, choice)))
        cfg = load_user_config()
        cfg.default_coders = coders
        save_user_config(cfg)
        self._operator_chat = None
        return [f"saved {len(coders)} default coder(s):"] + [
            f"  {c.id}: {c.model} ({c.adapter})" for c in coders
        ]

    ROLES = ["orchestrator", "planner", "critic", "researcher"]

    def _models_for_roles(self) -> list[str]:
        from chi.cli import _model_label
        from chi.tui.picker import PickerUnavailable, fuzzy_select
        from chi.userconfig import load_user_config, save_user_config

        role = self._ask(
            "Assign a model to which role? (stored for the upcoming orchestrator/critic layers)",
            [(r, r) for r in self.ROLES],
        )
        if role is None:
            return ["cancelled"]
        candidates = [m for m in self._candidate_models() if m.kind == "api"]
        if not candidates:
            return ["no API models available — roles need an API model, not a CLI"]
        try:
            picked = fuzzy_select(
                f"Model for {role}:",
                [(m.id, _model_label(m)) for m in candidates],
                multi=False,
                picker_fn=self.picker_fn,
            )
        except PickerUnavailable:
            return ["(picker unavailable — use `chi models --role "
                    f"{role} --pick <model>`)"]
        if not picked:
            return ["nothing selected"]
        cfg = load_user_config()
        cfg.role_models[role] = picked[0]
        save_user_config(cfg)
        self._operator_chat = None
        return [f"{role}: {picked[0]}"]

    def _cmd_setup(self, args: str) -> list[str]:
        from chi.providers.catalog import list_models, list_providers
        from chi.providers.recommend import recommend_setup
        from chi.userconfig import load_user_config, save_user_config

        providers = list_providers()
        coders, role_models, summary = recommend_setup(providers, list_models())
        if not coders and not role_models:
            return ["recommendation:"] + summary
        choice = self._ask(
            "Apply this recommended setup?\n" + "\n".join(summary),
            [("apply", "Apply it"), ("cancel", "Cancel")],
        )
        if choice != "apply":
            return ["recommendation:"] + summary + ["(not applied — /models to pick manually)"]
        cfg = load_user_config()
        cfg.default_coders = coders
        cfg.role_models = {**cfg.role_models, **role_models}
        save_user_config(cfg)
        self._operator_chat = None
        return ["applied recommended setup:"] + summary

    def maybe_first_run_setup(self) -> list[str]:
        """On a fresh machine, offer the recommended setup once (frontends call this)."""
        from chi.userconfig import load_user_config

        cfg = load_user_config()
        if cfg.default_coders or cfg.role_models or self.ask_fn is None:
            return []
        choice = self._ask(
            "No models configured yet. Set up now?",
            [("recommended", "Use the recommended setup (detects your keys & CLIs)"),
             ("manual", "Pick models manually"),
             ("skip", "Not now (/setup later)")],
        )
        if choice == "recommended":
            from chi.providers.catalog import list_models, list_providers
            from chi.providers.recommend import recommend_setup
            from chi.userconfig import save_user_config

            coders, role_models, summary = recommend_setup(list_providers(), list_models())
            if coders or role_models:
                cfg.default_coders = coders
                cfg.role_models = role_models
                save_user_config(cfg)
                self._operator_chat = None
                return ["applied recommended setup:"] + summary
            return ["nothing detected to recommend:"] + summary
        if choice == "manual":
            return self._cmd_models("")
        return ["skipped — note: chi can't chat or start runs until models are"
                " configured; /setup or /models when ready"]

    def _cmd_setkey(self, args: str) -> list[str]:
        import os

        from chi.providers.catalog import key_env_var
        from chi.userconfig import set_credential

        provider = args.strip()
        if not provider:
            return ["usage: /setkey <provider>   (e.g. /setkey deepseek)"]
        env_var = key_env_var(provider)
        if env_var is None:
            return [f"error: unknown provider '{provider}' — see /vendors"]
        if self.secret_fn is None:
            return ["no masked input available here — use `chi providers --set-key "
                    f"{provider}` instead"]
        value = self.secret_fn(f"{env_var} for {provider}")
        if not value:
            return ["cancelled"]
        path = set_credential(env_var, value)
        os.environ[env_var] = value  # effective immediately for /models readiness
        return [f"stored {env_var} in {path} (0600) — {provider} is ready"]

    def _cmd_run(self, args: str) -> list[str]:
        fleet_path = Path(args.strip()) if args.strip() else Path("fleet.yaml")
        if not fleet_path.exists():
            return [f"error: {fleet_path} not found — pass a path: /run path/to/fleet.yaml"]
        return self._launch(load_fleet(fleet_path), source=str(fleet_path))

    def launch_problem(self, problem_dir: str, max_iterations: int | None = None) -> list[str]:
        """Start a run on a problem directory using the configured default coders."""
        from chi.config import BudgetsCfg, FleetConfig, PoliciesCfg, resolve_coders
        from chi.userconfig import load_user_config

        path = Path(problem_dir).expanduser()
        if not (path / "problem.yaml").exists():
            return [f"error: {path} is not a problem directory (no problem.yaml)"]
        cfg = load_user_config()
        policies = PoliciesCfg(max_iterations=max_iterations) if max_iterations \
            else PoliciesCfg()
        fleet = FleetConfig(
            run_name=path.name, problem=path,
            budgets=BudgetsCfg(total_usd=cfg.default_budget_usd),
            coders=[], policies=policies,
        )
        try:
            resolve_coders(fleet)  # fail before launching when nothing is configured
        except ValueError as exc:
            return [f"error: {exc}"]
        return self._launch(fleet, source=str(path))

    def _launch(self, fleet, source: str) -> list[str]:
        if self.has_active_run():
            return ["error: a run is already active — /stop it first"]
        self._handle = RunHandle(fleet, self.runs_root)
        self._reader = None
        self._cursor = 0
        self._best_score = None
        self._tokens_in = 0
        self._tokens_out = 0
        self._cost_usd = 0.0
        self._context_pct = None
        try:
            from chi.config import load_problem

            self._direction = load_problem(Path(fleet.problem)).score.direction
        except (FileNotFoundError, ValueError):
            self._direction = "minimize"
        self._handle.start()
        return [f"starting run from {source} …"]

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
        if self.has_active_run() and self._handle is not None and self._handle.run_dir:
            self._append_steering(self._handle.run_dir, args.strip())
            return ["steering directive queued for the next iteration"]
        if self._last_run_dir is not None:
            self._append_steering(self._last_run_dir, args.strip())
            return [f"directive written to {self._last_run_dir.name}/steering.md —"
                    " a live session on that run picks it up at its next iteration"]
        return ["no run to steer — /run or /resume first"]

    def _cmd_resume(self, args: str) -> list[str]:
        from chi.tui.picker import PickerUnavailable, fuzzy_select
        from chi.userconfig import list_sessions

        sessions = list_sessions()
        if not sessions:
            return ["no sessions recorded yet — /run to create one"]
        if args.strip():
            matches = [s for s in sessions if s["run_id"] == args.strip()]
            if not matches:
                return [f"error: unknown session '{args.strip()}' — /resume to list"]
            chosen = matches[0]
        else:
            choices = [(s["run_id"], self._session_label(s)) for s in sessions]
            try:
                picked = fuzzy_select("Resume a session:", choices, multi=False,
                                      picker_fn=self.picker_fn)
            except PickerUnavailable:
                return [self._session_label(s) for s in sessions] + [
                    "(use /resume <run_id>)"
                ]
            if not picked:
                return ["nothing selected"]
            chosen = next(s for s in sessions if s["run_id"] == picked[0])
        run_dir = Path(chosen["run_dir"])
        if not run_dir.exists():
            return [f"error: run directory missing: {run_dir}"]
        self._last_run_dir = run_dir
        try:
            from chi.config import load_problem

            self._direction = load_problem(run_dir / "workdir").score.direction
        except (FileNotFoundError, ValueError):
            self._direction = "minimize"
        lines = [
            f"resumed {chosen['run_id']} [{chosen.get('status', '?')}]"
            f" — started {chosen.get('started_at', '?')[:19]} in {chosen.get('cwd', '?')}",
        ]
        lines.extend(self._cmd_champion(""))
        lines.append("(/status /ledger /champion inspect it; /steer still reaches it)")
        return lines

    @staticmethod
    def _session_label(s: dict) -> str:
        problem = Path(s.get("problem", "?")).name
        champ = s.get("champion_score")
        champ_txt = "—" if champ is None else f"{champ:.4g}"
        return (f"{s['run_id']}  [{s.get('status', '?')}]  {problem}"
                f"  champ={champ_txt}  {s.get('started_at', '')[:16]}")

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

    def _ask(self, question: str, options: list[tuple[str, str]]) -> str | None:
        """Single-choice question to the operator; None when no frontend can ask."""
        if self.ask_fn is None:
            return None
        return self.ask_fn(question, options)

    def _cmd_quit(self, args: str) -> list[str]:
        if self.has_active_run():
            choice = self._ask(
                "A run is active — quit anyway?",
                [("stop", "Stop the run, then quit"),
                 ("stay", "Keep running (stay in the session)")],
            )
            if choice == "stop" and self._handle is not None:
                self._handle.request_stop()
                self._quit_after_stop = True
                return ["stopping the run — chi quits when it lands"]
            return ["a run is active — /stop it first (or wait for it to finish)"]
        self.quit_requested = True
        return ["bye"]

    # -- free text -----------------------------------------------------------

    def record_operator_usage(self, result, context_limit: int | None) -> None:
        """Fold operator-LLM usage into the session telemetry (footer)."""
        self._tokens_in += result.tokens_in
        self._tokens_out += result.tokens_out
        self._cost_usd += result.cost_usd
        if context_limit:
            self._context_pct = 100.0 * result.tokens_in / context_limit

    def _operator(self):
        """Lazily build the operator chat (CLI-backed or API-backed); None if neither."""
        if self._operator_chat is not None:
            return self._operator_chat
        from chi.userconfig import load_user_config

        cfg = load_user_config()
        if cfg.operator_cli:
            import shutil

            if shutil.which(cfg.operator_cli) is not None:
                from chi.session.operator import CliOperatorChat, fleet_summary_text

                self._operator_chat = CliOperatorChat(
                    self, cfg.operator_cli, runner=self.cli_runner_fn,
                    fleet_summary=fleet_summary_text(),
                )
                return self._operator_chat
        model = cfg.role_models.get("orchestrator")
        if model is None:
            model = next(
                (c.model for c in cfg.default_coders if c.adapter == "litellm_loop"), None
            )
        if model is None:
            return None
        from chi.providers.budgets import BudgetTracker
        from chi.session.operator import OperatorChat, fleet_summary_text

        self._operator_chat = OperatorChat(
            self, model, BudgetTracker(cfg.default_budget_usd),
            completion_fn=self.completion_fn, fleet_summary=fleet_summary_text(),
        )
        return self._operator_chat

    def _available_clis(self) -> list[str]:
        import shutil

        from chi.providers.catalog import CLI_SUBSTRATES

        return [cli for cli in CLI_SUBSTRATES if shutil.which(cli) is not None]

    def _offer_operator_fallback(self, reason: str, retry_text: str) -> list[str]:
        """Ask how chi should think when the API operator is missing or failing."""
        from chi.userconfig import load_user_config, save_user_config

        clis = self._available_clis()
        options: list[tuple[str, str]] = [
            (cli, f"Use the {cli} CLI as chi's brain (no API key needed)") for cli in clis
        ]
        options.append(("setkey", "Enter an API key now (/setkey <provider>)"))
        options.append(("skip", "Not now"))
        if self.ask_fn is None or not clis:
            hints = [f"{reason}"]
            if clis:
                hints.append(f"tip: a vendor CLI can drive chi — set operator_cli:"
                             f" {clis[0]} in ~/.config/chi/config.yaml")
            hints.append("or store a key: /setkey <provider>")
            return hints
        choice = self._ask(f"{reason}\nHow should chi think?", options)
        if choice in clis:
            cfg = load_user_config()
            cfg.operator_cli = choice
            save_user_config(cfg)
            self._operator_chat = None
            operator = self._operator()
            if operator is not None:
                return [f"chi now thinks via the {choice} CLI"] + operator.turn(retry_text)
            return [f"error: could not start the {choice} CLI operator"]
        if choice == "setkey":
            return ["run /setkey <provider> (e.g. /setkey anthropic), then ask again"]
        return ["okay — conversation stays off until models are set (/setup, /setkey)"]

    def _free_text(self, text: str) -> list[str]:
        from chi.providers.budgets import BudgetExceededError
        from chi.userconfig import load_user_config

        operator = self._operator()
        if operator is None:
            cfg = load_user_config()
            if self.ask_fn is not None and self._available_clis():
                reason = ("No models are configured yet." if not cfg.default_coders
                          else "The conversational operator has no API model to think with.")
                return self._offer_operator_fallback(reason, text)
            if not cfg.default_coders:
                return ["chi needs models before it can work — /setup applies the"
                        " recommended fleet, or /models to pick manually"]
            return ["the conversational operator needs an API model or a vendor CLI —"
                    " /setkey <provider>, or set operator_cli: claude in"
                    " ~/.config/chi/config.yaml"]
        self.busy_note = f"thinking via {operator.model}"
        try:
            return operator.turn(text)
        except BudgetExceededError as exc:
            return [f"error: {exc} — raise default_budget_usd in"
                    " ~/.config/chi/config.yaml to continue"]
        except Exception as exc:  # provider/auth failures: recover, never dump JSON
            self._operator_chat = None
            return self._offer_operator_fallback(
                f"The operator model failed: {_short_error(exc)}", text)
        finally:
            self.busy_note = None

    @staticmethod
    def _append_steering(run_dir: Path, text: str) -> None:
        steering_path = run_dir / "steering.md"
        existing = steering_path.read_text() if steering_path.exists() else ""
        steering_path.write_text(existing + f"\n## §op {utcnow()}\n{text}\n")

    def _active_or_last_run_dir(self) -> Path | None:
        if self._handle is not None and self._handle.run_dir is not None:
            return self._handle.run_dir
        return self._last_run_dir
