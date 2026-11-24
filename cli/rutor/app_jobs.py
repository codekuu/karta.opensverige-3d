"""A child process that is read line by line while it runs and can be stopped with its children.

The app page runs the CLI as a child: `python -m rutor render ...`. A render starts Node,
which starts Chrome, so stopping the child is not enough; the whole process group has to go,
or the graphics card stays busy. That is what `stop()` is for.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from rutor.api import one_line

TERM_SECONDS = 10.0

# How long a prefix may be. `error: refused: taken` is a prefix and a message, and a
# sentence that happens to contain ": " is not a prefix at all.
KIND_CHARS = 20

# One or two lower-case words, which is how rutor prints a kind: `claimed`, `logged in`.
_KIND = re.compile(r"[a-z]+(?: [a-z]+)?")

_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class Line:
    """One line of a child's output: what it is about, and the rest of the line."""

    kind: str
    text: str


def split(raw: str) -> Line:
    """Split one line of output into its kind and its text, both safe to show to a person."""
    line = raw.rstrip("\r\n")
    head, separator, rest = line.partition(": ")
    if separator and _KIND.fullmatch(head) and len(head) <= KIND_CHARS:
        return Line(head, one_line(rest))
    return Line("", one_line(line))


class Run(Protocol):
    """A child that is being read. Its output is data, never instructions."""

    def lines(self) -> Iterator[Line]: ...

    def wait(self) -> int: ...

    def stop(self) -> None: ...


Start = Callable[[list[str], Mapping[str, str]], Run]


class _Process:
    """A started child, its pipes, and the right way to end the whole tree of it."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self._process = process
        self._stopping = threading.Lock()
        self._stopped = False
        # The read end of the pipe is closed by whoever owns it at the time: a reader
        # thread while it reads, and the child's own end so a finished child does not
        # leave a file handle behind.
        self._reading_lock = threading.Lock()
        self._reading = False

    def lines(self) -> Iterator[Line]:
        stream = self._process.stdout
        if stream is None:
            return
        with self._reading_lock:
            self._reading = True
        try:
            # Iterating a text pipe hands over each line as it arrives; it ends when the
            # child closes the pipe, which stop() makes happen.
            for raw in stream:
                line = split(raw)
                if not line.kind and not line.text:
                    continue
                yield line
        except ValueError:
            # wait() closed the pipe because nothing was reading it when the child ended.
            return
        finally:
            with self._reading_lock:
                self._reading = False
            if not stream.closed:
                stream.close()

    def wait(self) -> int:
        code = self._process.wait()
        self._close_unread()
        return code

    def _close_unread(self) -> None:
        """Close the read end, unless a reader thread is inside it right now."""
        with self._reading_lock:
            if self._reading:
                return
            stream = self._process.stdout
            if stream is not None and not stream.closed:
                stream.close()

    def stop(self) -> None:
        # A stop from the page's own thread while another thread reads the output ends
        # the output for the reader. A second stop waits for the first and then finds
        # the work already done, so it never returns while the tree is still up.
        with self._stopping:
            if self._stopped:
                return
            self._stopped = True
            self._end_tree()

    def _end_tree(self) -> None:
        pid = self._process.pid
        if _WINDOWS:
            # taskkill /T walks the tree, which is the only way on Windows; its own
            # failure only means the tree was already gone.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        else:
            # The group is signalled even when the child itself has ended, because a
            # grandchild the child started can outlive it.
            _signal_group(pid, signal.SIGTERM)
            try:
                self._process.wait(timeout=TERM_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            # Whatever ignored the polite signal goes now, so the card is free.
            _signal_group(pid, signal.SIGKILL)
        # Returning before the child is reaped would leave a zombie behind.
        self._process.wait()
        self._close_unread()


def _signal_group(pid: int, sig: int) -> None:
    """Signal a child's process group. A group that is already gone is not a fault."""
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        # ProcessLookupError: nothing is left. PermissionError: the group belongs to
        # somebody else, which is not ours to kill and not ours to report.
        pass


def _child_env(extra_env: Mapping[str, str]) -> dict[str, str]:
    """The parent's environment plus the few variables a child needs, never the parent's own."""
    return {**os.environ, "PYTHONUNBUFFERED": "1", **extra_env}


def start_process(command: list[str], extra_env: Mapping[str, str]) -> Run:
    """Start a command as its own process group, with one pipe for both of its outputs."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_child_env(extra_env),
        # A new session makes the child the leader of a group that holds everything it
        # starts, which is what lets stop() end Node and Chrome too. Windows has no
        # sessions, so its own process group is the closest thing.
        # The conditional keeps the Windows-only constant from being read elsewhere.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if _WINDOWS else 0,
        start_new_session=not _WINDOWS,
    )
    return _Process(process)


def start_rutor(argv: list[str], extra_env: Mapping[str, str]) -> Run:
    """Start this same Python running the CLI, so the app page runs the version it ships with."""
    return start_process([sys.executable, "-m", "rutor", *argv], extra_env)
