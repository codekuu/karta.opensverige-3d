"""Child processes for the app page: starting one, reading it and stopping what it started."""

from __future__ import annotations

import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Mapping
from pathlib import Path

from rutor.api import MESSAGE_CHARS
from rutor.app_jobs import Line, Run, split, start_process, start_rutor

PYTHON = sys.executable
GONE_SECONDS = 10.0


def child(script: str, extra_env: Mapping[str, str] | None = None) -> Run:
    return start_process([PYTHON, "-c", script], extra_env or {})


def alive(pid: int) -> bool:
    """Whether a process with this pid still exists. Works on both families of system."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Somebody else owns it, so it is there.
        return True
    return True


def gone(pid: int, seconds: float = GONE_SECONDS) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


class Reader(threading.Thread):
    """Reads one run on another thread, so a test can wait for one line at a time."""

    def __init__(self, run: Run) -> None:
        super().__init__(daemon=True)
        self._run = run
        self._items: queue.Queue[Line | None] = queue.Queue()

    def run(self) -> None:
        try:
            for line in self._run.lines():
                self._items.put(line)
        finally:
            self._items.put(None)

    def next(self, timeout: float = 10.0) -> Line:
        item = self._items.get(timeout=timeout)
        if item is None:
            raise AssertionError("the child closed its output before that line")
        return item

    def rest(self, timeout: float = 10.0) -> list[Line]:
        got: list[Line] = []
        while True:
            item = self._items.get(timeout=timeout)
            if item is None:
                return got
            got.append(item)


class SplitTests(unittest.TestCase):
    def test_a_prefixed_line_is_its_word_and_the_rest(self) -> None:
        self.assertEqual(split("claimed: stockholm-norr 74_20 expires X"), Line("claimed", "stockholm-norr 74_20 expires X"))
        self.assertEqual(split("logged in: klang"), Line("logged in", "klang"))

    def test_a_line_without_a_prefix_is_text(self) -> None:
        self.assertEqual(split("encode window 2/4"), Line("", "encode window 2/4"))
        self.assertEqual(split('File "x.py", line 3: boom'), Line("", 'File "x.py", line 3: boom'))

    def test_everything_after_the_first_colon_is_kept(self) -> None:
        self.assertEqual(split("url: https://a/b#c"), Line("url", "https://a/b#c"))
        self.assertEqual(split("error: refused: taken: someone has it"), Line("error", "refused: taken: someone has it"))

    def test_markup_and_long_lines_become_one_short_line(self) -> None:
        line = split("<b>x</b>" * 200)
        self.assertLessEqual(len(line.text), MESSAGE_CHARS)
        self.assertNotIn("\n", line.text)
        self.assertNotIn("\r", line.text)
        self.assertEqual(split("a: 1\r\n"), Line("a", "1"))


class RunTests(unittest.TestCase):
    def test_lines_arrive_while_the_child_runs(self) -> None:
        run = child("import time\nprint('a: 1', flush=True)\ntime.sleep(30)\n")
        self.addCleanup(run.stop)
        reader = Reader(run)
        reader.start()
        started = time.monotonic()
        self.assertEqual(reader.next(timeout=5.0), Line("a", "1"))
        self.assertLess(time.monotonic() - started, 5.0)

    def test_the_exit_code_is_the_childs(self) -> None:
        run = child("import sys\nsys.exit(5)\n")
        self.assertEqual(run.wait(), 5)

    def test_stop_ends_the_child_and_its_children(self) -> None:
        # The child names itself in the line, so the test knows both pids without
        # reaching inside Run, whose only job is to start, read and stop.
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "kid.pid"
            script = (
                "import os, pathlib, subprocess, sys, time\n"
                "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"pathlib.Path({str(marker)!r}).write_text(str(kid.pid))\n"
                "print('ready: ' + str(os.getpid()), flush=True)\n"
                "time.sleep(60)\n"
            )
            run = child(script)
            self.addCleanup(run.stop)
            reader = Reader(run)
            reader.start()
            ready = reader.next()
            self.assertEqual(ready.kind, "ready")
            me = int(ready.text)
            self.assertTrue(alive(me))
            kid = int(marker.read_text())
            started = time.monotonic()
            run.stop()
            self.assertLess(time.monotonic() - started, 5.0)
            self.assertTrue(gone(me))
            self.assertTrue(gone(kid))

    def test_stop_twice_and_after_the_end_does_nothing(self) -> None:
        run = child("import sys\nsys.exit(0)\n")
        self.assertEqual(run.wait(), 0)
        run.stop()
        run.stop()
        self.assertEqual(run.wait(), 0)

    def test_added_variables_reach_the_child_and_not_this_process(self) -> None:
        script = "import os\nprint('x: ' + os.environ['X_PROV'], flush=True)\n"
        run = child(script, {"X_PROV": "1"})
        self.addCleanup(run.stop)
        reader = Reader(run)
        reader.start()
        self.assertEqual(reader.next(), Line("x", "1"))
        self.assertEqual(run.wait(), 0)
        self.assertNotIn("X_PROV", os.environ)

    def test_stderr_arrives_on_the_same_pipe(self) -> None:
        run = child("import sys\nprint('error: boom', file=sys.stderr, flush=True)\nsys.exit(2)\n")
        self.addCleanup(run.stop)
        reader = Reader(run)
        reader.start()
        self.assertEqual(reader.rest(), [Line("error", "boom")])
        self.assertEqual(run.wait(), 2)

    def test_start_rutor_runs_this_python_with_the_module(self) -> None:
        run = start_rutor(["--version"], {})
        self.addCleanup(run.stop)
        reader = Reader(run)
        reader.start()
        lines = [reader.next()]
        lines.extend(reader.rest())
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].text.startswith("rutor "), lines[0])
        self.assertEqual(run.wait(), 0)


if __name__ == "__main__":
    unittest.main()
