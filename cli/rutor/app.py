"""The app page: building a map cell from a page on this machine, without a terminal.

The checks below only read. They say whether this machine can draw, which tools
are missing, whether the Cesium token works, what the terms are, what the
electricity price advises and whether this copy of the pipeline is current.
The page owns the sentences. These functions return short words.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta

from rutor import api, commands, config, login, pipeline, se3_price
from rutor.errors import RutorError
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
