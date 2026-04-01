from __future__ import annotations as _annotations

import sys
from collections.abc import Sequence

import click

from .runtime import AcpKitError, run_target

__all__ = ("cli", "main")


class AcpKitClickError(click.ClickException):
    exit_code = 2


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """ACP Kit root CLI."""


@cli.command("run")
@click.argument("target")
@click.option(
    "-p",
    "--path",
    "import_roots",
    multiple=True,
    help="Extra import root for loading the target module. Can be repeated.",
)
def run_command(target: str, import_roots: tuple[str, ...]) -> None:
    try:
        run_target(target, import_roots=import_roots)
    except AcpKitError as exc:
        raise AcpKitClickError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    try:
        cli.main(
            args=list(argv) if argv is not None else None,
            prog_name="acpkit",
            standalone_mode=False,
        )
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return exc.exit_code
    except click.exceptions.Exit as exc:
        return exc.exit_code
    return 0
