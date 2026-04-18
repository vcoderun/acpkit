from __future__ import annotations as _annotations

import sys
from collections.abc import Sequence

import click

from .runtime import AcpKitError, launch_target, run_target
from .runtime import launch_command as launch_raw_command

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


@cli.command("launch")
@click.argument("target", required=False)
@click.option(
    "-c",
    "--command",
    "raw_command",
    help="Launch a raw server command directly with `toad acp`, for example `python3.11 finance_agent.py`.",
)
@click.option(
    "-p",
    "--path",
    "import_roots",
    multiple=True,
    help="Extra import root for loading the target module. Can be repeated.",
)
def launch_command(
    target: str | None,
    raw_command: str | None,
    import_roots: tuple[str, ...],
) -> None:
    if (target is None) == (raw_command is None):
        raise AcpKitClickError("Provide exactly one of `TARGET` or `--command`.")
    if raw_command is not None and import_roots:
        raise AcpKitClickError("`--path` can only be used when launching a target.")
    try:
        if raw_command is not None:
            exit_code = launch_raw_command(raw_command)
        else:
            assert target is not None
            exit_code = launch_target(target, import_roots=import_roots)
    except AcpKitError as exc:
        raise AcpKitClickError(str(exc)) from exc
    if exit_code != 0:
        raise click.exceptions.Exit(exit_code)


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
