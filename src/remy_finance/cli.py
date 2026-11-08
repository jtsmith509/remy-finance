"""Command-line entry point (`remy`). Commands are added stage by stage."""

import typer

from remy_finance import __version__

app = typer.Typer(help="REMY Finance command-line interface.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
