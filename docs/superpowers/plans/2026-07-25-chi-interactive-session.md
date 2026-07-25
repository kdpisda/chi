# Chi Interactive Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bare `chi` opens a persistent interactive session (prompt_toolkit REPL) with `/vendors` `/models` fuzzy pickers, background `/run` streaming live fleet events, and plain-text steering — all logic in a UI-agnostic session engine per `docs/superpowers/specs/2026-07-25-chi-interactive-session-design.md`.

**Architecture:** New modules `chi/userconfig.py`, `chi/providers/catalog.py`, `chi/tui/picker.py`, `chi/session/{runner,engine}.py`, `chi/tui/repl.py`; small extensions to `chi/config.py`, `chi/orchestrator/loop.py`, `chi/cli.py`.

**Tech Stack:** Python ≥3.11, InquirerPy (fuzzy picker; pulls prompt_toolkit), existing deps.

## Global Constraints

Same as the v1 plan (`2026-07-25-chi-v1-phases-0-1.md`): uv + Python 3.12, type hints everywhere, no bare excepts, UTC Z timestamps, commit trailers, `uv run pytest` green per task.

---

### Task 1: userconfig module

**Files:** Create `chi/userconfig.py`, `tests/test_userconfig.py`. Modify `pyproject.toml` (add `inquirerpy>=0.3`).

**Interfaces produced:**
- `config_dir() -> Path` — `$CHI_CONFIG_DIR` else `~/.config/chi`, created on demand.
- `class UserConfig(BaseModel): enabled_providers: list[str] = []; default_coders: list[CoderCfg] = []`
- `load_user_config() -> UserConfig` (missing/invalid file → defaults), `save_user_config(cfg: UserConfig) -> Path` (yaml at `config_dir()/config.yaml`).
- `credentials_path() -> Path`; `set_credential(env_var: str, value: str) -> Path` (upsert `VAR=value` line, chmod 0o600); `load_env(project_dir: Path = Path(".")) -> None` (dotenv: project `.env` first, then credentials — no overrides, so project/env wins).

**Steps:** failing tests → implement → green → commit `feat(userconfig): global config dir, default coders, credentials store`.

Tests: round-trip save/load under monkeypatched `CHI_CONFIG_DIR`; missing file → defaults; `set_credential` creates 600-mode file and upserts (two calls, second replaces value, one line per var); `load_env` precedence: project `.env` var beats credentials var of same name (use monkeypatch.chdir(tmp) + os.environ cleanup).

---

### Task 2: provider/model catalog

**Files:** Create `chi/providers/catalog.py`, `tests/test_catalog.py`.

**Interfaces produced:**
```python
FEATURED_PROVIDERS: list[str]  # anthropic, openai, deepseek, zhipu(ai), gemini, groq, minimax, mistral, xai, openrouter — intersected with registry keys
CLI_SUBSTRATES: dict[str, str]  # {"claude": 'claude -p "Follow the instructions in {prompt_file} exactly." --allowedTools Bash,Edit,Write,Read', "codex": 'codex exec --full-auto "Follow the instructions in {prompt_file} exactly."'}
KEY_ENV_FALLBACK: dict[str, str]  # provider -> API key env var for featured providers
@dataclass ProviderInfo: key: str; kind: str; ready: bool; detail: str
@dataclass ModelInfo: id: str; provider: str; kind: str; input_cost_per_m: float | None; output_cost_per_m: float | None; command: str | None = None
def list_providers(all_providers=False, registry=None, validate_fn=None, which_fn=None) -> list[ProviderInfo]
def list_models(provider_keys: list[str] | None = None, registry=None, cost_map=None, which_fn=None) -> list[ModelInfo]
def key_env_var(provider: str, validate_fn=None) -> str | None
```
`registry` defaults to `litellm.models_by_provider`, `cost_map` to `litellm.model_cost`, `validate_fn` wraps `litellm.validate_environment(model)` (exception → not ready with detail), `which_fn` to `shutil.which`. `list_models` includes CLI substrates (kind `cli`, cost None, `command` set) for every detected CLI when `provider_keys` is None or contains the CLI name.

**Steps:** failing tests (stub registry `{"anthropic": ["anthropic/claude-x"], "deepseek": [...]}`, fake validate/which) → implement → green → commit `feat(catalog): provider readiness and model catalog from litellm registry + CLI detection`.

---

### Task 3: fuzzy picker wrapper

**Files:** Create `chi/tui/__init__.py`, `chi/tui/picker.py`, `tests/test_picker.py`.

**Interfaces produced:**
```python
class PickerUnavailable(RuntimeError): ...
def fuzzy_select(message: str, choices: list[tuple[str, str]], multi: bool = False, picker_fn=None) -> list[str]
```
`choices` are `(value, label)`. Default path: `sys.stdin.isatty()` false → raise `PickerUnavailable`; else InquirerPy `inquirer.fuzzy` (`multiselect=multi`). Injected `picker_fn(message, choices, multi)` returns list of values. Always returns a list (single-select → one element).

**Steps:** tests with injected picker + non-TTY raise → implement → green → commit `feat(tui): injectable fuzzy picker over InquirerPy`.

---

### Task 4: providers/models CLI verbs + fleet fallback

**Files:** Modify `chi/config.py` (coders optional + `resolve_coders`), `chi/orchestrator/loop.py` (use `resolve_coders`), `chi/cli.py` (providers/vendors/models commands; `load_env` in ping/run). Create `tests/test_cli_catalog_verbs.py`, extend `tests/test_config.py`.

**Interfaces produced:**
- `chi.config.FleetConfig.coders: list[CoderCfg] = []`
- `chi.config.resolve_coders(fleet: FleetConfig) -> list[CoderCfg]` — fleet's, else `load_user_config().default_coders`, else `ValueError("no coders configured — run /models (or chi models) or add coders to fleet.yaml")`.
- CLI: `chi providers [--all] [--enable CSV] [--set-key PROVIDER]` (alias `chi vendors`), `chi models [--pick CSV] [--fleet PATH] [--provider CSV] [--all]`. Interactive paths call `fuzzy_select`; `PickerUnavailable` → print table/list + flag hint, exit 0. `--fleet` rewrite: `yaml.safe_load` file, replace `coders`, `yaml.safe_dump` back. Coder entry construction: API model → `{id: c<N>, model, adapter: litellm_loop}`; CLI → `{id: c<N>, model: <cli>, adapter: cli_subprocess, command: CLI_SUBSTRATES[cli]}`.

**Steps:** failing tests → implement → green → commit `feat(cli): chi providers/vendors + chi models with global defaults and --fleet targeting`.

Tests (CliRunner, monkeypatched catalog fns + `CHI_CONFIG_DIR`): providers table lists stubbed providers with ready flags; `--enable a,b` persists; `--set-key` writes credential via hidden prompt (input="sk-x\n"); models `--pick` writes `default_coders` (API + CLI shapes correct); `--pick --fleet` rewrites only `coders:` preserving `budgets:`; `resolve_coders` precedence + error; `start_run` works with fleet lacking coders when defaults exist (scripted adapter).

---

### Task 5: orchestrator run hooks (on_run_created, stop_event)

**Files:** Modify `chi/orchestrator/loop.py`. Create `tests/test_loop_hooks.py`.

**Interfaces produced:** `start_run(fleet, runs_root=Path("runs"), completion_fn=None, on_run_created: Callable[[str, Path], None] | None = None, stop_event: threading.Event | None = None) -> RunSummary`. Callback fires after the runs row exists, before baseline eval. Stop check at top of each iteration: emit `STOP` event `{"reason": "operator"}`, release task, status `"stopped"`, break (before adapter call).

**Steps:** failing tests → implement → green → commit `feat(orchestrator): run-created callback and cooperative stop for interactive sessions`.

Tests: callback receives run_id/run_dir and fires before summary returns (record order via list); pre-set stop_event → 0 iterations, status `stopped`, task back to `pending`, STOP event present.

---

### Task 6: session runner + engine

**Files:** Create `chi/session/__init__.py`, `chi/session/runner.py`, `chi/session/engine.py`, `tests/test_session_engine.py`.

**Interfaces produced:**
```python
class RunHandle:
    def __init__(self, fleet: FleetConfig, runs_root: Path, completion_fn=None): ...
    def start(self) -> None
    ready: threading.Event; run_id: str | None; run_dir: Path | None
    summary: RunSummary | None; error: str | None
    @property def alive(self) -> bool
    def request_stop(self) -> None
class SessionEngine:
    def __init__(self, runs_root: Path = Path("runs"), picker_fn=None): ...
    def submit(self, text: str) -> list[str]
    def poll_events(self) -> list[str]
    def has_active_run(self) -> bool
    COMMANDS: registered {"/help", "/vendors", "/providers", "/models", "/run", "/status", "/steer", "/stop", "/ledger", "/champion", "/quit"}
    quit_requested: bool
```
Engine details: `/run [path]` → path arg else `./fleet.yaml`; refuse if active; build `RunHandle`, start, wait `ready` (timeout 10s), open a reader `Store` on run_dir, cursor = 0. `poll_events` → query `events WHERE event_id > cursor`, format (skip HEARTBEAT; RESULT lines mark improvements vs best-seen score using problem direction from the fleet's problem config); when handle finished, append summary line and clear active state. Free text: active run → append `\n## §op <utcnow>\n<text>\n` to `<run_dir>/steering.md`, return confirmation; idle → hint line. `/steer <text>` same as free text but explicit. `/stop` → `request_stop()`. `/quit` → if active run, return warning requiring `/stop` first; else set `quit_requested`. `/status`, `/ledger [negative]`, `/champion` read the active or most recent run store. Every command handler wrapped: exceptions → `error: <msg>` line.

**Steps:** failing tests → implement → green → commit `feat(session): UI-agnostic engine with background runs, live event tail, text steering`.

Tests (scripted adapter fleets in tmp dirs): `/help` lists commands; unknown `/x` → error line; `/run` + wait for completion (poll loop with timeout) → events include ITERATION_COMPLETE and STOP, summary line present; free text during run lands in steering.md (use a 3-iteration scripted run and submit text between polls; assert file contains the directive); `/run` while active refused; `/stop` ends early with status stopped; `/quit` guard when active; `/champion` prints champion after run.

---

### Task 7: REPL frontend + bare `chi` launch + README

**Files:** Create `chi/tui/repl.py`, `tests/test_repl_launch.py`. Modify `chi/cli.py` (`invoke_without_command=True` → launch REPL), `README.md`.

**Interfaces produced:** `chi.tui.repl.run_repl(engine: SessionEngine | None = None) -> None` — prompt_toolkit `PromptSession` (`chi> `), `WordCompleter` over engine command names, `patch_stdout()`, background daemon thread printing `poll_events()` every second, loop until `engine.quit_requested` or EOF/KeyboardInterrupt (with active-run warning). CLI callback: `ctx.invoked_subcommand is None` → `load_env()`, `run_repl()`.

**Steps:** implement; unit test that the completer includes all engine commands and that `run_repl` exits immediately with a fake input session (inject a `prompt_fn` returning `/quit` — add optional `prompt_fn` param for testability); full suite green; manual smoke `chi` in a terminal; update README (interactive session section, slash commands table); commit `feat(repl): interactive chi session — bare chi launches the harness UI`.

---

## Plan self-review notes

Spec coverage: engine/REPL/runner/pickers/catalog/userconfig/CLI verbs/orchestrator hooks/fleet fallback all have tasks; error handling embedded (Task 3 non-TTY, Task 6 wrapping); conversational layer + Textual explicitly deferred. Type consistency: `CoderCfg` reused from config; `RunHandle` consumed only by engine; `fuzzy_select` signature identical in Tasks 3/4/6.
