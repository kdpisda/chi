"""Chi command-line interface."""

import json
from pathlib import Path

import typer

from chi.config import load_fleet, load_problem
from chi.providers.budgets import BudgetTracker
from chi.providers.llm import ping as _ping_impl

app = typer.Typer(no_args_is_help=True, help="Chi (χ) — autoresearch harness.")


@app.callback()
def _main() -> None:
    """Chi (χ) — autoresearch harness."""


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
    from dotenv import load_dotenv

    load_dotenv()
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
