"""Chi command-line interface."""

import json
from pathlib import Path

import typer

from chi.config import CoderCfg, load_fleet, load_problem
from chi.providers.budgets import BudgetTracker
from chi.providers.catalog import CLI_SUBSTRATES, key_env_var, list_models, list_providers
from chi.providers.llm import ping as _ping_impl
from chi.tui.picker import PickerUnavailable, fuzzy_select
from chi.userconfig import load_env, load_user_config, save_user_config, set_credential

app = typer.Typer(no_args_is_help=True, help="Chi (χ) — autoresearch harness.")


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    """Chi (χ) — autoresearch harness. Bare `chi` opens the interactive session."""
    if ctx.invoked_subcommand is None:
        from chi.tui.repl import run_repl

        load_env()
        run_repl()


@app.command()
def version() -> None:
    """Print the chi version."""
    from chi import __version__

    typer.echo(__version__)


@app.command()
def ping(
    fleet: Path = typer.Option(..., "--fleet", help="Path to fleet.yaml"),
    budget_usd: float = typer.Option(0.10, "--budget-usd", help="Max spend for the ping sweep"),
) -> None:
    """Call every distinct model in the fleet once; print latency/cost/tokens."""
    load_env()
    cfg = load_fleet(fleet)
    models = sorted({c.model for c in cfg.coders})
    rows = _ping_impl(models, BudgetTracker(total_usd=budget_usd))
    failed = False
    for r in rows:
        status_txt = "OK " if r["ok"] else "FAIL"
        failed = failed or not r["ok"]
        typer.echo(
            f"{status_txt} {r['model']}  {r['latency_s']:.2f}s  ${r['cost_usd']:.5f}"
            f"  in={r['tokens_in']} out={r['tokens_out']}  {r['error']}"
        )
    raise typer.Exit(1 if failed else 0)


@app.command()
def validate(path: Path = typer.Argument(..., help="fleet.yaml or a problem directory")) -> None:
    """Validate a fleet.yaml or a problem directory; exit 1 on invalid."""
    try:
        if path.is_dir():
            prob = load_problem(path)
            typer.echo(f"OK problem '{prob.name}' ({len(prob.correctness.seeds)} seeds)")
        else:
            cfg = load_fleet(path)
            typer.echo(f"OK fleet '{cfg.run_name}' ({len(cfg.coders)} coder(s))")
    except Exception as exc:  # surface validation errors as CLI failure
        typer.echo(f"INVALID: {exc}")
        raise typer.Exit(1)


# --- provider / model selection ---------------------------------------------


def _provider_table(all_providers: bool) -> list:
    infos = list_providers(all_providers=all_providers)
    enabled = set(load_user_config().enabled_providers)
    for info in infos:
        mark = "*" if info.key in enabled else " "
        state = "ready" if info.ready else info.detail
        typer.echo(f"{mark} {info.key:<14} {info.kind:<4} {state}")
    return infos


def _providers_impl(
    all_providers: bool = typer.Option(False, "--all"),
    enable: str = typer.Option("", "--enable", help="Comma-separated providers to enable"),
    set_key: str = typer.Option("", "--set-key", help="Provider to store an API key for"),
) -> None:
    """Show provider status; enable providers or store an API key."""
    load_env()
    if set_key:
        env_var = key_env_var(set_key)
        if env_var is None:
            typer.echo(f"unknown provider '{set_key}'")
            raise typer.Exit(1)
        value = typer.prompt(f"{env_var}", hide_input=True)
        path = set_credential(env_var, value)
        typer.echo(f"stored {env_var} in {path}")
        return
    infos = _provider_table(all_providers)
    cfg = load_user_config()
    if enable:
        cfg.enabled_providers = [p.strip() for p in enable.split(",") if p.strip()]
    else:
        try:
            picked = fuzzy_select(
                "Enable providers (tab to multi-select):",
                [(i.key, f"{i.key} [{i.kind}] {'ready' if i.ready else i.detail}")
                 for i in infos],
                multi=True,
            )
            cfg.enabled_providers = picked
        except PickerUnavailable:
            typer.echo("(non-interactive: use --enable a,b or --set-key PROVIDER)")
            return
    save_user_config(cfg)
    typer.echo(f"enabled: {', '.join(cfg.enabled_providers) or '(none)'}")


app.command("providers")(_providers_impl)
app.command("vendors", hidden=True)(_providers_impl)


def _model_label(info) -> str:
    if info.kind == "cli":
        return f"{info.id} [cli substrate]"
    if info.input_cost_per_m is None:
        return f"{info.id}"
    return (f"{info.id}  ${info.input_cost_per_m:.2f}/M in,"
            f" ${info.output_cost_per_m:.2f}/M out")


def _coders_from_picks(picks: list[str], candidates: list) -> list[CoderCfg]:
    by_id = {c.id: c for c in candidates}
    coders: list[CoderCfg] = []
    for n, pick in enumerate(picks, start=1):
        info = by_id.get(pick)
        if info is None or info.kind == "api":
            coders.append(CoderCfg(id=f"c{n}", model=pick, adapter="litellm_loop"))
        else:
            coders.append(CoderCfg(id=f"c{n}", model=info.id, adapter="cli_subprocess",
                                   command=info.command or CLI_SUBSTRATES.get(info.id)))
    return coders


@app.command()
def models(
    pick: str = typer.Option("", "--pick", help="Comma-separated model ids (non-interactive)"),
    fleet: Path | None = typer.Option(None, "--fleet", help="Write into this fleet.yaml"),
    provider: str = typer.Option("", "--provider", help="Limit to these providers (csv)"),
    all_providers: bool = typer.Option(False, "--all"),
) -> None:
    """Pick coder models (fuzzy) into global defaults or a fleet.yaml."""
    import yaml as _yaml

    load_env()
    if provider:
        keys = [p.strip() for p in provider.split(",") if p.strip()]
    else:
        infos = list_providers(all_providers=all_providers)
        enabled = set(load_user_config().enabled_providers)
        ready = [i.key for i in infos if i.ready]
        keys = [k for k in ready if k in enabled] or ready
    candidates = list_models(keys)
    if not candidates:
        typer.echo("no models available — check `chi providers` (keys/CLIs missing?)")
        raise typer.Exit(1)
    if pick:
        picks = [p.strip() for p in pick.split(",") if p.strip()]
    else:
        try:
            picks = fuzzy_select(
                "Pick coder models (tab to multi-select):",
                [(m.id, _model_label(m)) for m in candidates],
                multi=True,
            )
        except PickerUnavailable:
            for m in candidates:
                typer.echo(_model_label(m))
            typer.echo("(non-interactive: use --pick model1,model2)")
            return
    if not picks:
        typer.echo("nothing selected")
        raise typer.Exit(1)
    coders = _coders_from_picks(picks, candidates)
    if fleet is not None:
        data = _yaml.safe_load(fleet.read_text())
        data["coders"] = [c.model_dump(exclude_none=True) for c in coders]
        fleet.write_text(_yaml.safe_dump(data, sort_keys=False))
        typer.echo(f"wrote {len(coders)} coder(s) to {fleet}")
    else:
        cfg = load_user_config()
        cfg.default_coders = coders
        save_user_config(cfg)
        typer.echo(f"saved {len(coders)} default coder(s) to global config")


# --- agent verbs: the ONLY write path agents get to the store ---------------

from chi.store import events as events_mod  # noqa: E402
from chi.store import ledger as ledger_mod  # noqa: E402
from chi.store import tasks as tasks_mod  # noqa: E402
from chi.store.db import Store  # noqa: E402

task_app = typer.Typer(no_args_is_help=True, help="Task claim/release verbs for agents.")
app.add_typer(task_app, name="task")


def _open_run(run_dir: Path) -> tuple[Store, str]:
    """Open a run store; ensure a runs row exists (run_id = directory name)."""
    store = Store.open(run_dir)
    rows = store.query("SELECT run_id FROM runs")
    if rows:
        return store, rows[0]["run_id"]
    run_id = run_dir.name
    from chi.store.db import utcnow

    store.execute(
        "INSERT INTO runs (run_id, problem, fleet_config_json, started_at)"
        " VALUES (?,?,?,?)",
        (run_id, str(run_dir / "workdir"), "{}", utcnow()),
    )
    return store, run_id


@app.command("eval")
def eval_cmd(
    run_dir: Path = typer.Option(..., "--run-dir"),
    agent: str = typer.Option(..., "--agent"),
) -> None:
    """Evaluate the run's workdir candidate; record and print the result."""
    from dataclasses import asdict

    from chi.eval.runner import evaluate

    store, run_id = _open_run(run_dir)
    problem = load_problem(run_dir / "workdir")
    result = evaluate(problem, run_dir / "workdir", store=store, run_id=run_id, agent_id=agent)
    typer.echo(json.dumps(asdict(result)))
    raise typer.Exit(0 if result.correct else 2)


@app.command()
def query(
    text: str = typer.Argument(...),
    run_dir: Path = typer.Option(..., "--run-dir"),
) -> None:
    """Query experiments and the negative ledger."""
    store, run_id = _open_run(run_dir)
    typer.echo(json.dumps(ledger_mod.query_knowledge(store, run_id, text)))


@app.command()
def deadend(
    run_dir: Path = typer.Option(..., "--run-dir"),
    agent: str = typer.Option(..., "--agent"),
    approach_class: str = typer.Option(..., "--approach-class"),
    summary: str = typer.Option(..., "--summary"),
    evidence_json: str = typer.Option("", "--evidence-json"),
    scope: str = typer.Option("", "--scope"),
) -> None:
    """Record a ruled-out approach. Evidence is mandatory — no prose-only dead ends."""
    evidence = json.loads(evidence_json) if evidence_json else {}
    if not evidence:
        typer.echo("REJECTED: dead-end entries require non-empty --evidence-json")
        raise typer.Exit(1)
    store, run_id = _open_run(run_dir)
    neg_id = ledger_mod.add_negative(
        store, run_id, approach_class=approach_class, summary=summary,
        evidence=evidence, authored_by=agent, ruled_out_scope=scope,
    )
    typer.echo(neg_id)


@app.command()
def challenge(
    run_dir: Path = typer.Option(..., "--run-dir"),
    agent: str = typer.Option(..., "--agent"),
    neg_id: str = typer.Option(..., "--neg-id"),
    hypothesis: str = typer.Option(..., "--hypothesis"),
) -> None:
    """File a distinguishing hypothesis against a negative-ledger entry."""
    store, run_id = _open_run(run_dir)
    typer.echo(ledger_mod.add_challenge(store, run_id, neg_id=neg_id, agent_id=agent,
                                        hypothesis=hypothesis))


@app.command()
def heartbeat(
    run_dir: Path = typer.Option(..., "--run-dir"),
    agent: str = typer.Option(..., "--agent"),
) -> None:
    """Record agent liveness."""
    from chi.store.db import utcnow

    store, run_id = _open_run(run_dir)
    store.execute("UPDATE agents SET last_heartbeat_at=? WHERE agent_id=? AND run_id=?",
                  (utcnow(), agent, run_id))
    events_mod.append_event(store, run_id, events_mod.HEARTBEAT, agent_id=agent)
    typer.echo("ok")


@task_app.command("claim")
def task_claim(
    run_dir: Path = typer.Option(..., "--run-dir"),
    agent: str = typer.Option(..., "--agent"),
    lease_seconds: int = typer.Option(900, "--lease-seconds"),
) -> None:
    """Atomically claim the next pending task; prints its id (or NONE)."""
    store, run_id = _open_run(run_dir)
    task_id = tasks_mod.claim_task(store, run_id, agent, lease_seconds)
    typer.echo(task_id or "NONE")
    raise typer.Exit(0 if task_id else 1)


@task_app.command("release")
def task_release(
    run_dir: Path = typer.Option(..., "--run-dir"),
    task_id: str = typer.Option(..., "--task-id"),
    to_status: str = typer.Option("pending", "--to-status"),
) -> None:
    """Release a task back to the pool (or to a terminal status)."""
    store, run_id = _open_run(run_dir)
    tasks_mod.release_task(store, run_id, task_id, status=to_status)
    typer.echo("ok")


# --- operator verbs ---------------------------------------------------------


@app.command()
def run(
    fleet_path: Path = typer.Argument(..., help="Path to fleet.yaml"),
    runs_root: Path = typer.Option(Path("runs"), "--runs-root"),
) -> None:
    """Start a run and print the summary as JSON."""
    from dataclasses import asdict

    from chi.orchestrator.loop import start_run

    load_env()
    summary = start_run(load_fleet(fleet_path), runs_root=runs_root)
    out = asdict(summary)
    out["run_dir"] = str(out["run_dir"])
    typer.echo(json.dumps(out, indent=2))


@app.command()
def status(run_dir: Path = typer.Argument(...)) -> None:
    """Print the run row and the last 10 events."""
    store, run_id = _open_run(run_dir)
    row = store.query("SELECT * FROM runs WHERE run_id=?", (run_id,))[0]
    typer.echo(json.dumps(dict(row)))
    for event in store.query(
        "SELECT * FROM events WHERE run_id=? ORDER BY event_id DESC LIMIT 10", (run_id,)
    ):
        typer.echo(json.dumps(dict(event)))


@app.command()
def champion(
    run_dir: Path = typer.Argument(...),
    export: Path | None = typer.Option(None, "--export"),
) -> None:
    """Print the champion experiment; optionally export the current candidate."""
    store, run_id = _open_run(run_dir)
    problem = load_problem(run_dir / "workdir")
    champ = ledger_mod.champion(store, run_id, problem.score.direction)
    if champ is None:
        typer.echo("no champion yet")
        raise typer.Exit(1)
    typer.echo(json.dumps({"code_hash": champ["code_hash"],
                           "score_value": champ["score_value"]}))
    if export is not None:
        import shutil as _shutil

        _shutil.copy(run_dir / "workdir" / problem.candidate, export)


@app.command("ledger")
def ledger_cmd(
    run_dir: Path = typer.Argument(...),
    negative: bool = typer.Option(False, "--negative"),
) -> None:
    """Print experiments (or the negative ledger) as JSON lines."""
    store, run_id = _open_run(run_dir)
    table = "negative_ledger" if negative else "experiments"
    for row in store.query(f"SELECT * FROM {table} WHERE run_id=? ORDER BY ts", (run_id,)):
        typer.echo(json.dumps(dict(row)))


@app.command()
def steer(
    run_dir: Path = typer.Argument(...),
    text: str = typer.Argument(...),
) -> None:
    """Append an operator directive to the run's steering.md."""
    from chi.store.db import utcnow as _utcnow

    path = run_dir / "steering.md"
    existing = path.read_text() if path.exists() else ""
    path.write_text(existing + f"\n## §op {_utcnow()}\n{text}\n")
    typer.echo("ok")


@app.command()
def msg(
    run_dir: Path = typer.Option(..., "--run-dir"),
    agent: str = typer.Option(..., "--agent"),
    type_: str = typer.Option("STATUS", "--type"),
    payload_json: str = typer.Option("{}", "--payload-json"),
) -> None:
    """Post a typed message event to the bus."""
    store, run_id = _open_run(run_dir)
    events_mod.append_event(store, run_id, type_, agent_id=agent,
                            payload=json.loads(payload_json))
    typer.echo("ok")
