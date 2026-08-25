"""ShadowOps command-line application."""

import httpx
import typer

from shadowops import __version__

app = typer.Typer(no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the installed ShadowOps version."""
    typer.echo(__version__)


@app.command()
def ping(
    api_url: str = typer.Option("http://127.0.0.1:8000", help="ShadowOps API base URL"),
) -> None:
    """Report the API readiness status."""
    with httpx.Client(trust_env=False) as client:
        response = client.get(f"{api_url.rstrip('/')}/health/ready", timeout=5.0)
    response.raise_for_status()
    typer.echo(response.json()["status"])
