"""rutor: claim a cell, render it, redraw it, upload the PNG."""

from __future__ import annotations

import argparse
import os
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


def _open_pipes() -> None:
    """Make every line leave when it is printed, as UTF-8.

    An agent runs rutor through a pipe. Python holds a pipe's output back until the program
    ends, so `rutor login` would wait for an approval of a code nobody had been shown. And a
    Windows pipe is not UTF-8, so the terms would arrive garbled or not at all.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", line_buffering=True)
            except (OSError, ValueError):
                pass


def _reader_stopped() -> int:
    """Whoever read the output stopped reading, as `| head` does. That is not a fault.

    Standard output is pointed at nothing, so that Python does not report the closed pipe
    once more when it flushes on the way out.
    """
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass
    return 0


def main(argv: list[str] | None = None, registrars: Sequence[Registrar] | None = None) -> int:
    _open_pipes()
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
        try:
            print(f"rutor {__version__}")
        except BrokenPipeError:
            return _reader_stopped()
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
    except BrokenPipeError:
        return _reader_stopped()


if __name__ == "__main__":
    sys.exit(main())
