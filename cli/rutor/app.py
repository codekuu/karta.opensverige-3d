"""The app page: building a map cell from a page on this machine, without a terminal.

The checks below only read. They say whether this machine can draw, which tools
are missing, whether the Cesium token works, what the terms are, what the
electricity price advises and whether this copy of the pipeline is current.
The page owns the sentences. These functions return short words.

Below them, `Builder` holds the state of the person's eight steps and runs the
CLI as a child of this process: first the terms, then the GitHub login, then the
claim, drawing, check and upload of one cell.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from rutor import api, app_jobs, commands, config, login, pipeline, se3_price
from rutor.errors import RutorError
from rutor.localpage import Refusal
from rutor.renderer import google
from rutor.renderer.sources import SHEET_KINDS


# ---- what the page knows before a job


def _platform() -> str:
    return sys.platform


def _posix_ram_bytes() -> int | None:
    sysconf = getattr(os, "sysconf", None)
    if not callable(sysconf):
        return None
    page = sysconf("SC_PAGE_SIZE")
    pages = sysconf("SC_PHYS_PAGES")
    if isinstance(page, int) and isinstance(pages, int) and page > 0 and pages > 0:
        return page * pages
    return None


def _windows_ram_bytes() -> int | None:
    """ullTotalPhys from GlobalMemoryStatusEx. Windows has no sysconf."""
    if sys.platform != "win32":
        return None
    import ctypes

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = (
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    # A zero return means the call was refused, so there is no number.
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) == 0:
        return None
    total = int(status.ullTotalPhys)
    return total if total > 0 else None


def _ram_gb() -> int | None:
    """Installed memory in whole GiB, or None when this system will not say.

    There is no cutoff here. Whether the memory is enough is a later question.
    """
    for reader in (_posix_ram_bytes, _windows_ram_bytes):
        try:
            total = reader()
        except Exception:
            # A call that raises did not answer. The other way may still know.
            continue
        if isinstance(total, int) and total > 0:
            return round(total / 2**30)
    return None


def machine() -> dict[str, object]:
    needed = int(commands.DISK_MIN_CACHED_GB if commands._model_is_cached() else commands.DISK_MIN_GB)
    free = commands._free_gb()
    card = ""
    card_gb = 0
    problem = ""
    # A Mac has no NVIDIA driver to ask, and the redraw cannot run here.
    if _platform() == "darwin":
        problem = "mac"
    else:
        cards = commands._nvidia_cards()
        if not cards:
            problem = "no_card"
        else:
            name, mib = max(cards, key=lambda item: item[1])
            # The name comes from the driver. It is shown, so it is one short line.
            card = api.one_line(name, 60)
            card_gb = round(mib / 1024)
            if mib < commands.GPU_MIN_MIB:
                problem = "small_card"
            elif free < needed:
                problem = "disk"
    return {
        "ok": problem == "",
        "card": card,
        "card_gb": card_gb,
        "free_gb": int(free),
        "needed_gb": needed,
        "ram_gb": _ram_gb(),
        "problem": problem,
    }


def tools(sheet_kind: str) -> dict[str, object]:
    missing: list[str] = []
    if commands._torch_missing() is not None:
        missing.append("torch")
    if commands._bitsandbytes_missing() is not None:
        missing.append("bitsandbytes")
    if commands._render_missing() is not None:
        missing.append("render")
    # An open-data sheet has no Node render, so those checks are not asked.
    if sheet_kind == "google":
        if google.node_missing() is not None:
            missing.append("node")
        if google.renderer_missing() is not None:
            missing.append("google_renderer")
    # The first drawing downloads the model, so a cold cache is not "missing".
    return {"ready": not missing, "model_cached": commands._model_is_cached(), "missing": missing}


def _page_token(candidate: str | None) -> str:
    """The token to judge. A paste, even a blank one, does not fall through."""
    if candidate is not None:
        return candidate.strip()
    set_here = os.environ.get(config.CESIUM_TOKEN_ENV, "").strip()
    if set_here:
        return set_here
    stored = config.read_cesium_token()
    if not stored:
        return ""
    return stored.strip()


def cesium(candidate: str | None = None, ask: google.Asker | None = None) -> dict[str, object]:
    token = _page_token(candidate)
    if not token:
        return {"set": False, "ok": False, "problem": "missing"}
    # A space or a line break inside the token is a bad paste. Cesium is not asked.
    if any(character.isspace() or not character.isprintable() for character in token):
        return {"set": True, "ok": False, "problem": "broken"}
    try:
        # Cesium's own code is not one of the page's words. The token stays here.
        status = (ask or google._ask_ion)(token)[0]
    except RutorError:
        return {"set": True, "ok": False, "problem": "unreachable"}
    if status == 200:
        return {"set": True, "ok": True, "problem": ""}
    if status == 401:
        # Two dots is a token's shape. Any other count was pasted twice or cut.
        problem = "refused" if token.count(".") == 2 else "twice"
        return {"set": True, "ok": False, "problem": problem}
    if status in (403, 404):
        return {"set": True, "ok": False, "problem": "no_access"}
    return {"set": True, "ok": False, "problem": "unreachable"}


def terms() -> list[str]:
    return login.TERMS.split("\n")


def price(
    fetch: Callable[[date], list[se3_price.Slot] | None] = se3_price.fetch_prices,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, str]:
    """What se3_price.main decides, up to evaluate. A price that cannot be read never stops the page."""
    try:
        now = clock() if clock is not None else datetime.now(se3_price.STOCKHOLM)
        now = now.astimezone(se3_price.STOCKHOLM)
        today = fetch(now.date())
        next_date = now.date() + timedelta(days=1)
        tomorrow = fetch(next_date)
        report = se3_price.evaluate(now, today, tomorrow, next_date)
        decision = report.get("decision")
        if decision == "RUN":
            return {"decision": "RUN", "after": ""}
        if decision == "WAIT":
            start = report.get("cheapest_hour_start")
            after = start if isinstance(start, str) else ""
            return {"decision": "WAIT", "after": after}
    except Exception:
        # The price is advice. A site that does not answer must not stop the page.
        return {"decision": "unknown", "after": ""}
    # A decision other than RUN or WAIT is not one the page can show.
    return {"decision": "unknown", "after": ""}


def version(sheet: dict[str, object]) -> dict[str, object]:
    raw = sheet.get("pipeline")
    # The service names the pipeline it wants. What it says is shown, so it is one short line.
    wanted = api.one_line(raw, 40) if isinstance(raw, str) else ""
    mine = pipeline.PIPELINE
    # Nothing said means this copy is current: the service did not ask for another.
    return {"mine": mine, "wanted": wanted, "current": wanted == "" or wanted == mine}


# ---- the eight steps


# What a child prints is data. These are the only shapes the page keeps. A code and a
# login name are printed again in the page and put in a URL, so anything else is dropped.
_CODE_SHAPE = re.compile(r"[A-Za-z0-9-]{1,20}")
_NAME_SHAPE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")

# A browser lets a hidden tab run its timers only about once a minute, so a page that has
# asked nothing for half a minute may be a tab nobody has touched. Two and a half minutes
# without a request is a page that has gone, not one that is asleep.
GONE_SECONDS = 150.0

# Enough lines to see how a drawing went, without a render's whole log in memory.
LINES_KEPT = 20

# The phases in which a job is out and the page must not start another.
_RUNNING = frozenset({"claim", "render", "redraw", "upload"})

# The token goes to the two commands that read it and to no other: claim asks the sheet's
# source before it locks a cell on a Google sheet, and render starts the Google renderer.
_TOKEN_COMMANDS = frozenset({"claim", "render"})

# What a command's exit means to the page. 2 is also how a refusal ends, which the claim
# itself says with its last error line.
_PROBLEM_BY_EXIT = {2: "refused", 3: "machine", 4: "network", 5: "check"}

# How a claim ends when it is certain that no lock was given: the input was wrong (1), the
# service refused (2), or this machine was not ready and the service was never asked (3).
_NO_LOCK_EXITS = frozenset({1, 2, 3})


class Builder:
    """The state of the page's steps, and the children it runs for the person.

    The GitHub login token is never held here. When the page asks the service who the
    person is, it reads the token from the file at that moment, hands it over and lets it
    go, so no answer, log line or exception from here can carry it.

    One lock guards all the state. Nothing that can take time is done while it is held: a
    child start, a network ask and a thread join all happen outside it.
    """

    def __init__(
        self,
        start: app_jobs.Start = app_jobs.start_rutor,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._start = start
        # The page's own clock: what build() counts as "seen", and when a page has gone.
        self._clock = clock
        self._lock = threading.Lock()
        self._terms = False
        self._login = ""
        self._code = ""
        self._url = ""
        self._waiting = False
        self._problem = ""
        self._looked = False
        # Set once the page is closing. Nothing is started after that.
        self._closed = False
        # The login child, only while it is being read. A child that has ended is never
        # stopped: stopping signals its process group, and that number may be reused.
        self._run: app_jobs.Run | None = None
        self._thread: threading.Thread | None = None
        # The job: the cell the page chose, what its commands have said, and the child
        # that is being read right now. A finished child is dropped from `_job_run`.
        self._sheet = ""
        self._col = 0
        self._row = 0
        self._phase = "idle"
        self._detail = ""
        self._job_problem = ""
        self._job_url = ""
        self._held = False
        self._stop_asked = False
        # A claim that ended without saying whether the service gave the lock.
        self._unsure = False
        self._releasing = False
        self._lines: list[str] = []
        self._seen = clock()
        self._job_run: app_jobs.Run | None = None
        self._job_thread: threading.Thread | None = None

    def accept_terms(self) -> None:
        with self._lock:
            self._terms = True

    def terms_accepted(self) -> bool:
        self._remember_stored_login()
        with self._lock:
            # A stored login exists only because the terms were accepted when it was made.
            return self._terms or bool(self._login)

    def start_login(self) -> None:
        self._remember_stored_login()
        with self._lock:
            if not (self._terms or self._login):
                raise Refusal(409, "accept the terms first", "terms")
            # A second press while the code is on screen must not start a second child.
            if self._waiting or self._closed:
                return
            self._code = ""
            self._url = ""
            self._problem = ""
            self._waiting = True
        try:
            run = self._start(["login", "--accept-terms"], {})
        except OSError:
            # The child could not start, which is a login that ended before it began.
            with self._lock:
                self._waiting = False
                self._problem = "expired"
            return
        # The login can last minutes, so the page reads it on its own thread and returns.
        thread = threading.Thread(target=self._read_login, args=(run,), daemon=True)
        with self._lock:
            closed = self._closed
            if not closed:
                self._run = run
            self._thread = thread
        if closed:
            # The page closed while the child was starting. It is not left behind.
            run.stop()
        thread.start()

    def _read_login(self, run: app_jobs.Run) -> None:
        name = ""
        exit_code = 1
        try:
            for line in run.lines():
                # The child prints the ten terms first, and other lines are not the page's.
                if line.kind == "code" and _CODE_SHAPE.fullmatch(line.text):
                    with self._lock:
                        self._code = line.text
                elif line.kind == "url" and line.text == login.DEVICE_URL:
                    # Only GitHub's own address is passed on, so no other page is offered.
                    with self._lock:
                        self._url = line.text
                elif line.kind == "logged in" and _NAME_SHAPE.fullmatch(line.text):
                    name = line.text
                    with self._lock:
                        self._login = name
            exit_code = run.wait()
        except Exception as exc:  # noqa: BLE001 - the last stop on this thread
            # The page must not wait for ever on a reader that broke. The kind of fault goes
            # to the terminal, never its message: this process holds the login.
            print(f"app: {type(exc).__name__} while reading the login", file=sys.stderr)
        with self._lock:
            self._run = None
            self._waiting = False
            self._code = ""
            self._url = ""
            if name:
                self._problem = ""
            else:
                # 4 is the CLI's network code; any other end is a code nobody approved.
                self._problem = "network" if exit_code == 4 else "expired"

    def login_state(self) -> dict[str, object]:
        self._remember_stored_login()
        with self._lock:
            return {
                "login": self._login,
                "code": self._code,
                "url": self._url,
                "waiting": self._waiting,
                "problem": self._problem,
            }

    def _remember_stored_login(self) -> None:
        """Ask the service, on the first look only, who a stored token belongs to."""
        with self._lock:
            if self._looked:
                return
            self._looked = True
        token = config.read_token()
        if not token:
            return
        try:
            _status, me = api.get("/v1/me", token=token)
        except RutorError:
            # As far as the page knows, nobody is logged in. It says nothing more.
            return
        name = me.get("login") if isinstance(me, dict) else None
        if isinstance(name, str) and _NAME_SHAPE.fullmatch(name):
            with self._lock:
                # A name a login child has just read is fresher than the stored token's.
                if not self._login:
                    self._login = name

    # ---- the job: one cell, claimed, drawn and uploaded

    def choose(self, sheet: str, col: int, row: int) -> None:
        """Note the cell the person picked. This takes no lock and starts nothing."""
        with self._lock:
            if self._busy():
                raise Refusal(409, "a cell is being built", "busy")
            self._sheet = sheet
            self._col = col
            self._row = row
            self._phase = "idle"
            self._detail = ""
            self._job_problem = ""
            self._job_url = ""

    def chosen(self) -> dict[str, object] | None:
        with self._lock:
            if not self._sheet:
                return None
            return {"sheet": self._sheet, "col": self._col, "row": self._row}

    def build(self) -> None:
        """Start the job for the chosen cell, on its own thread."""
        with self._lock:
            if self._closed:
                raise Refusal(409, "the page is closing", "busy")
            if self._busy():
                raise Refusal(409, "a cell is being built", "busy")
            sheet, col, row = self._sheet, self._col, self._row
        if not sheet:
            raise Refusal(409, "choose a cell first", "no_cell")
        # The machine, the tools, the login and the token are asked here: they read the
        # disk and, for a stored login, the service. None of that happens under the lock.
        kind = SHEET_KINDS.get(sheet, "")
        if not machine()["ok"]:
            raise Refusal(409, "this machine cannot draw a cell yet", "not_ready")
        if not tools(kind)["ready"]:
            raise Refusal(409, "a tool this build needs is missing", "not_ready")
        if not self.login_state()["login"]:
            raise Refusal(409, "log in before building", "not_ready")
        if kind == "google" and not cesium()["ok"]:
            raise Refusal(409, "the Cesium token does not work", "not_ready")
        with self._lock:
            # The page may have closed, or another tab pressed Rita, meanwhile.
            if self._closed:
                raise Refusal(409, "the page is closing", "busy")
            if self._busy() or (self._sheet, self._col, self._row) != (sheet, col, row):
                # Another tab chose another cell while this one was being checked.
                raise Refusal(409, "a cell is being built", "busy")
            # Starting counts as the page being here, and a new job starts a new report.
            self._seen = self._clock()
            self._stop_asked = False
            self._unsure = False
            self._phase = "claim"
            self._detail = ""
            self._job_problem = ""
            self._job_url = ""
            self._lines = []
            thread = threading.Thread(target=self._run_job, args=(sheet, col, row), daemon=True)
            self._job_thread = thread
        thread.start()

    def stop(self) -> None:
        """End the job and give its lock back. Returns once the lock is out."""
        with self._lock:
            running = self._phase in _RUNNING
            if running:
                self._stop_asked = True
                run = self._job_run
            else:
                run = None
            thread = self._job_thread
        if run is not None:
            # Only the child being read is stopped. A finished one is not touched.
            run.stop()
        if thread is not None and thread is not threading.current_thread():
            # So that when stop() returns, the release has been tried and the report is
            # final. The timeout keeps a child that will not die from hanging the page.
            thread.join(timeout=120)

    def seen(self) -> None:
        with self._lock:
            self._seen = self._clock()

    def tick(self) -> None:
        """Stop a job whose page has not asked for anything for a long time."""
        with self._lock:
            gone = self._phase in _RUNNING and self._clock() - self._seen > GONE_SECONDS
        if gone:
            # A closed tab is not a stopped job: the lock is given back the same way.
            self.stop()

    def job_state(self) -> dict[str, object]:
        with self._lock:
            return {
                "phase": self._phase,
                "detail": self._detail,
                "problem": self._job_problem,
                "url": self._job_url,
                "sheet": self._sheet,
                "col": self._col,
                "row": self._row,
                "held": self._held,
            }

    def job_lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def _busy(self) -> bool:
        # A lock that is being given back is still out, so nothing new may start. Callers
        # hold the lock.
        return self._phase in _RUNNING or self._releasing

    def _run_job(self, sheet: str, col: int, row: int) -> None:
        cell = f"{col}_{row}"
        try:
            problem = self._run_commands(sheet, cell)
        except Exception as exc:  # noqa: BLE001 - the last stop on this thread
            # The page must not wait for ever on a job that broke. The kind of fault goes
            # to the terminal, never its message: a child's output may be in it.
            print(f"app: {type(exc).__name__} while building", file=sys.stderr)
            problem = "failed"
        with self._lock:
            if problem == "":
                # The service has the cell. A stop that came after that changes nothing.
                self._phase = "done"
                self._job_problem = ""
            elif self._stop_asked:
                self._phase = "stopped"
                self._job_problem = ""
            else:
                self._phase = "failed"
                self._job_problem = problem
            # No command runs now, so there is no progress line left to show.
            self._detail = ""
            held = self._held
            unsure = self._unsure and not held
            # A lock that is still out is given back before another job may start.
            self._releasing = held or unsure
        if held or unsure:
            try:
                # A lock that is known is asked back twice. One that only may exist is asked
                # back once, and what the service answers is not held against the page.
                self._release(sheet, cell, tries=2 if held else 1)
            except Exception as exc:  # noqa: BLE001 - the page still has to learn it ended
                print(f"app: {type(exc).__name__} while releasing", file=sys.stderr)
            finally:
                with self._lock:
                    self._releasing = False

    def _run_commands(self, sheet: str, cell: str) -> str:
        """Run the four commands for one cell. "" when the upload went out, else the problem."""
        for command in ("claim", "render", "redraw", "upload"):
            with self._lock:
                if self._stop_asked:
                    # The loop ends here; the job thread reports it as stopped.
                    return "stopped"
                self._phase = command
                self._detail = ""
            env = config.cesium_env() if command in _TOKEN_COMMANDS else {}
            try:
                run = self._start([command, sheet, cell], env)
            except OSError:
                # A child that cannot start is a command that never ran.
                return "failed"
            last_error = ""
            taken_in = False
            with self._lock:
                self._job_run = run
                stop_now = self._stop_asked
            if stop_now:
                # The stop came while the child was starting and found nothing to stop.
                run.stop()
            try:
                for line in run.lines():
                    self._keep(line, detail=True)
                    if command == "claim" and line.kind == "claimed":
                        with self._lock:
                            self._held = True
                    elif command == "upload" and line.kind == "uploaded":
                        # The service has the cell from here on, and the lock is spent.
                        taken_in = True
                        with self._lock:
                            self._held = False
                    elif command == "upload" and line.kind == "url":
                        # Only the map's own address is handed to the page.
                        if line.text.startswith(f"{config.MAP_URL}#"):
                            with self._lock:
                                self._job_url = line.text
                    if line.kind == "error":
                        # The CLI says why it refused in its last error line.
                        last_error = line.text
                exit_code = run.wait()
            except Exception:
                # A child whose output breaks is still up, and a render holds the card.
                run.stop()
                raise
            finally:
                with self._lock:
                    if self._job_run is run:
                        self._job_run = None
            if exit_code == 0 or taken_in:
                with self._lock:
                    if command == "claim":
                        self._held = True
                    elif command == "upload":
                        # The cell waits for review on the map; no lock is out any more.
                        self._held = False
                continue
            if command == "claim" and exit_code not in _NO_LOCK_EXITS:
                # Stopped, cut off by the network or broken: the service may have given the
                # lock without the claim living to say so.
                with self._lock:
                    self._unsure = True
            if exit_code == 2 and last_error.startswith("refused: taken"):
                return "taken"
            return _PROBLEM_BY_EXIT.get(exit_code, "failed")
        return ""

    def _release(self, sheet: str, cell: str, tries: int) -> None:
        """Give the lock back, twice at most: the service ends an abandoned lock by itself."""
        for _attempt in range(tries):
            try:
                run = self._start(["release", sheet, cell], {})
            except OSError:
                continue
            # A release child is never stopped: it ends at once, and its group number may
            # already belong to somebody else's program. It is read so nothing is left.
            for line in run.lines():
                self._keep(line, detail=False)
            if run.wait() == 0:
                with self._lock:
                    self._held = False
                return

    def _keep(self, line: app_jobs.Line, *, detail: bool) -> None:
        """Keep the line for the report, and a progress line for the page."""
        shown = f"{line.kind}: {line.text}" if line.kind else line.text
        with self._lock:
            self._lines.append(shown)
            if len(self._lines) > LINES_KEPT:
                del self._lines[: len(self._lines) - LINES_KEPT]
            if detail and not line.kind:
                self._detail = line.text

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            run = self._run
            thread = self._thread
        if run is not None:
            run.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=15)
        # A job that is drawing is stopped the way the page stops it, lock and all.
        self.stop()
