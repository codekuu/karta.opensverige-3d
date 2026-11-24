"""The app page: building a map cell from a page on this machine, without a terminal.

The checks below only read. They say whether this machine can draw, which tools
are missing, whether the Cesium token works, what the terms are, what the
electricity price advises and whether this copy of the pipeline is current.
The page owns the sentences. These functions return short words.

Below them, `Builder` holds the state of the person's eight steps and runs the
CLI as a child of this process: first the terms, then the GitHub login.
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
        # Kept for the steps that time a child, which a later task adds.
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

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            run = self._run
            thread = self._thread
        if run is not None:
            run.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=15)
