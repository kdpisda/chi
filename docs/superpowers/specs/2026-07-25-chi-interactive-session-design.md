# Chi Interactive Session — Design (v1)

**Date:** 2026-07-25
**Status:** Approved (KD, 2026-07-25)
**Scope:** Make `chi` an interactive harness in the style of Claude Code / codex / pi / opencode: bare `chi` opens a persistent session where the operator configures providers/models with slash commands, starts runs, watches fleet events stream live, and steers with plain sentences. Supersedes the earlier providers/models-only sketch; the CLI subcommands from v1 remain as the scriptable/agent-facing layer.

## Decisions (KD)

- V1 scope: **shell + pickers + live runs**. The conversational LLM operator layer is the immediate next iteration; this design reserves its seam (free-text input with no active run).
- Frontends: **both** — prompt_toolkit REPL now, Textual TUI later. Therefore all session logic lives in a UI-agnostic engine; frontends only render.
- Config homes: **both** — global `~/.config/chi/config.yaml` defaults plus `--fleet` targeting of a specific fleet.yaml.
- Model catalog: **litellm registry** (offline, includes pricing) + detection of installed vendor CLIs (`claude`, `codex`).
- Picker: **fuzzy** (InquirerPy, prompt_toolkit stack), injectable for tests, non-TTY falls back to flags.

## Architecture

```
chi (bare)            chi <subcommand>
   │                        │ (unchanged v1 CLI + new providers/models verbs)
   ▼                        ▼
tui/repl.py  ──────►  session/engine.py  ◄────── (later) Textual frontend
(prompt_toolkit)         │        │
                         │        └── session/runner.py (RunHandle: start_run in thread,
                         │             on_run_created callback, cooperative stop_event)
                         ├── providers/catalog.py (litellm registry + CLI detection)
                         ├── userconfig.py (~/.config/chi: config.yaml, credentials.env)
                         └── tui/picker.py (fuzzy_select, picker_fn injectable)
```

## Components

**`chi/userconfig.py`** — `UserConfig(enabled_providers: list[str], default_coders: list[CoderCfg])`; `config_dir()` honors `CHI_CONFIG_DIR` (tests) else `~/.config/chi`; `load_user_config()/save_user_config()`; `set_credential(env_var, value)` upserts into `credentials.env` (chmod 600); `load_env()` loads project `.env` first, then `credentials.env` (dotenv never overrides, so project and real env win).

**`chi/providers/catalog.py`** — `list_providers(all_providers=False)` returns `ProviderInfo(key, kind, ready, detail)`: API providers from litellm's `models_by_provider` (featured subset by default), readiness via `litellm.validate_environment` (injectable `validate_fn`); CLI substrates via `shutil.which` (injectable `which_fn`). `list_models(provider_keys)` returns `ModelInfo(id, provider, kind, input_cost_per_m, output_cost_per_m)` with costs from `litellm.model_cost` (injectable `registry`/`cost_map`). Known CLI substrates carry default `cli_subprocess` command templates.

**`chi/tui/picker.py`** — `fuzzy_select(message, choices, multi=False, picker_fn=None)`; default uses InquirerPy fuzzy prompt; raises `PickerUnavailable` on non-TTY without injected picker (callers fall back to printed lists + flags).

**CLI verbs** — `chi providers` (alias `chi vendors`): status table always; interactive fuzzy multiselect sets `enabled_providers`; `--enable a,b`, `--set-key PROVIDER` (hidden prompt → credentials.env; env-var name from validate_environment's missing_keys with a curated fallback map), `--all`. `chi models`: fuzzy multiselect over models of enabled+ready providers plus detected CLIs; selection → coder entries (auto ids; `litellm_loop` for API, `cli_subprocess` + default command for CLIs); writes global `default_coders` or, with `--fleet path`, rewrites that file's `coders:` block preserving other keys; `--pick "m1,m2"` non-interactive.

**Fleet fallback** — `FleetConfig.coders` becomes optional; `resolve_coders(fleet)` returns fleet coders or global `default_coders`, else raises with a message pointing at `/models`. `start_run` uses it.

**Orchestrator additions** — `start_run(..., on_run_created=None, stop_event=None)`: callback fires right after the run row/store exist (lets the session attach its event tailer); `stop_event` checked at the top of each iteration → status `stopped`, task released, `STOP` event with reason `operator`.

**`chi/session/runner.py`** — `RunHandle`: runs `start_run` in a daemon thread; exposes `run_id/run_dir` (set via callback + `ready` event), `summary`, `error`, `alive`, `request_stop()`.

**`chi/session/engine.py`** — UI-agnostic. `submit(text) -> list[str]` routes `/command args` through a registry, free text → steering when a run is active (appends operator directive to the run's `steering.md`), else a hint that conversational mode arrives next. `poll_events() -> list[str]` tails the active run's events table past a cursor and formats ITERATION_START/COMPLETE, RESULT (flags new champions), DEAD_END, WATCHDOG_KILL, BUDGET_BLOCK, STEER_UPDATE, STOP (HEARTBEAT suppressed). Commands v1: `/help /vendors /providers /models /run [fleet] /status /steer <text> /stop /ledger [negative] /champion /quit`. `/run` needs a fleet file (arg or `./fleet.yaml`) for the problem pointer; coders may come from defaults. `/quit` with an active run requires confirmation.

**`chi/tui/repl.py`** — prompt_toolkit `PromptSession` with slash-command completer and `patch_stdout`; a background pump prints `poll_events()` output above the prompt ~1/s; run-completion prints the summary. Bare `chi` (typer callback with `invoke_without_command=True`) launches it.

## Error handling

Picker unavailable → printed list + instruction to use flags. `/run` with a run already active → refuse (one run per session in v1). Fleet/problem validation errors surface as transcript lines, never tracebacks. Engine catches per-command exceptions and renders them as `error:` lines.

## Testing

All behavior at engine/module level with injected fakes (no real terminal, no network): userconfig round-trip under `CHI_CONFIG_DIR`; credential file mode 600 + precedence (project `.env` > credentials); catalog with stubbed registry/validate/which; picker via injected `picker_fn`; `chi models --pick` writes defaults and `--fleet` rewrites only `coders:`; `resolve_coders` precedence and no-coders error; `stop_event` ends a scripted run with status `stopped`; `on_run_created` fires before completion; engine: dispatch, unknown command, `/run`→ events stream from a real scripted run, free-text steering lands in `steering.md` and next iteration's prompt, `/quit` guard. REPL stays thin; manual smoke.

## Deferred

Conversational LLM operator layer (seam: engine free-text handler); Textual frontend over the same engine; multiple concurrent runs per session; `/pause`; key management UI beyond set-key.
