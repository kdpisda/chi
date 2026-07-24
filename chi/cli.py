"""Chi command-line interface."""

import typer

app = typer.Typer(no_args_is_help=True, help="Chi (χ) — autoresearch harness.")


@app.callback()
def _main() -> None:
    """Chi (χ) — autoresearch harness."""


@app.command()
def version() -> None:
    """Print the chi version."""
    from chi import __version__

    typer.echo(__version__)
