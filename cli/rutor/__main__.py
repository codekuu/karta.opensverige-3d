"""rutor: claim a cell, render it, redraw it, upload the PNG."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from rutor import __version__
from rutor.errors import RutorError

# _SubParsersAction is not subscriptable at runtime on this Python, so the alias stays open.
Registrar = Callable[..., None]


def _load(sub: argparse._SubParsersAction[argparse.ArgumentParser], module: str) -> None:
    # Imported here so a missing command module cannot break `rutor --version`,
    # and so importing this module never loads torch.
    try:
        imported = __import__(module, fromlist=["register"])
    except ModuleNotFoundError:
        return
    register = getattr(imported, "register")
    register(sub)


def _builtin_registrars() -> list[Registrar]:
    def register_login(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
        _load(sub, "rutor.login")

    def register_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
        _load(sub, "rutor.commands")

    def register_review(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
        _load(sub, "rutor.review")

    return [register_login, register_commands, register_review]


def main(argv: list[str] | None = None, registrars: Sequence[Registrar] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rutor")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")
    chosen = list(registrars) if registrars is not None else _builtin_registrars()
    for register in chosen:
        register(sub)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return 0 if code in (0, None) else 1
    if args.version:
        print(f"rutor {__version__}")
        return 0
    if not getattr(args, "command", None):
        parser.print_usage(sys.stderr)
        return 1
    func = getattr(args, "func", None)
    if func is None:
        parser.print_usage(sys.stderr)
        return 1
    try:
        # A sheet id becomes a folder name and part of a URL, so it is checked once, here.
        if isinstance(getattr(args, "sheet", None), str):
            from rutor.config import sheet_id

            sheet_id(args.sheet)
        return int(func(args))
    except RutorError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    sys.exit(main())
