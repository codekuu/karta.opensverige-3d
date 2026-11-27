#!/usr/bin/env python3
"""Build Pixelbygget cells one after another, by itself, until it is told to stop.

    .venv/bin/python tools/build_cells.py stockholm-norr                  # start where rutor claim --next would
    .venv/bin/python tools/build_cells.py stockholm-norr 85_65            # 85_65, then down the column, then on
    .venv/bin/python tools/build_cells.py stockholm-norr --until 07:00    # take no new cell after seven

Where it starts:
- A cell a run on this machine claimed and did not finish (it was cut off, a power cut) is
  finished first. A cell the account holds that no run here claimed is the other machine's,
  and is left alone.

Two machines on one login: start one with --part 1/2 and the other with --part 2/2. They split
the sheet like a chessboard, so they never draw the same cell. A builder who is not trusted may
hold one cell at a time; while the other machine holds it (too_many_locks), this one draws ahead,
as it does while five cells wait for review, and sends when the service takes another.
- Then the cell given, and the line from it (down unless --step says otherwise) until the line ends.
- With no cell given, on from the newest cell drawn ahead, else this login's newest cell on the
  sheet. With neither, the cell `rutor claim --next` picks: a free cell beside one on the map.

Then it goes on by itself: each next cell is a free cell beside something already drawn, the
one nearest the cell it built last. So what it draws grows as one piece. It ends at --until,
--count, Ctrl-C, when no cell is free, or when something needs a person.

It runs the same rutor commands a person or an agent runs (claim, render, redraw, upload) and
does what AGENTS.md says where a person would otherwise be asked:

- A taken cell is passed by. Nothing was locked.
- A flat cell (open water, fields, no 3D buildings) is given back and passed by. Its picture is
  kept and named in the summary. It is never drawn with --flat-ok. Five in a row stop the run.
- Five cells waiting for review (a builder who is not trusted yet): it draws the next cells
  ahead, up to --ahead of them, without claiming them. Before each new cell it asks whether the
  service takes another, and if so claims and sends the oldest one drawn ahead. A cell someone
  else claimed meanwhile is dropped. With --ahead full it holds no cell and asks again every
  --review-wait minutes. What is drawn ahead is kept, and the next run sends it first.
- 20 uploads in 24 hours: the drawn cell keeps its lock (48 hours) and the upload is tried again
  every hour.
- A failed quality check, a missing piece on this machine or a refusal it does not know stops
  the run, and the lock is given back.
- Ctrl-C stops the running command and gives the cell back, except while it is being sent.
- On Linux the machine does not sleep while it runs, not even with the lid closed.
- kill, or closing the terminal, stops it as Ctrl-C does.

What it never does: log in, accept terms, touch the Cesium token or the login, set a
PIXELBYGGET_ variable, or hold two cells. Those first steps are a person's, once: see README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli"
STEPS = {"down": (0, 1), "up": (0, -1), "right": (1, 0), "left": (-1, 0)}
# Refusals of a claim that mean "not this cell": the run goes on past it.
PASS_BY = {"taken", "being_removed", "no_cell", "outside"}
# A cell in one of these states is drawn: a free cell beside one is where the map grows.
DRAWN = {"live", "godkand", "vantar"}
NETWORK_TRIES = 3
NETWORK_PAUSE_S = 60
CHILD_GRACE_S = 3
CHILD_STOP_S = 30
SE3_LONGEST_WAIT = timedelta(hours=24)
# The service answers a daily limit with Retry-After: 3600.
UPLOAD_RETRY = timedelta(hours=1)
FLAT_IN_A_ROW = 5

# What a refusal that stops the run means, in a sentence. Nothing was locked by any of them.
LIMITS = {
    "too_many_locks": "you already hold a cell. Finish it or rutor release it",
    "sheet_closed": "the sheet is closed",
    "update_needed": "this clone is older than the sheet needs: update it and install again",
    "bad_token": "the login has stopped working: rutor login, by a person",
    "no_token": "this machine is not logged in: rutor login, by a person",
}

Cell = tuple[int, int]


class Stop(Exception):
    """The run ends here.

    keep_lock: a finished picture keeps its cell, to be sent later.
    normal: the run ended as runs do (--until, --count, nothing free) and nothing needs a person.
    """

    def __init__(self, message: str, keep_lock: bool = False, normal: bool = False) -> None:
        super().__init__(message)
        self.keep_lock = keep_lock
        self.normal = normal


class Log:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("a", encoding="utf-8")

    def line(self, text: str) -> None:
        try:
            print(text, flush=True)
        except OSError:
            pass  # the terminal is gone; the log file still has it
        self._file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


LOG: Log | None = None
# When the run stops taking new cells: --until, as a moment. None runs on.
DEADLINE: datetime | None = None


def say(text: str) -> None:
    line = f"build_cells: {text}"
    if LOG is None:
        print(line, flush=True)
    else:
        LOG.line(line)


def rutor(*args: str, quiet: bool = False) -> tuple[int, str]:
    """Run one rutor command, show its lines as they come, and return its exit code and output."""
    child = subprocess.Popen(
        [sys.executable, "-m", "rutor", *args],
        cwd=CLI,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    lines: list[str] = []
    try:
        assert child.stdout is not None
        for line in child.stdout:
            text = line.rstrip("\n")
            lines.append(text)
            if quiet:
                continue
            if LOG is None:
                print(text, flush=True)
            else:
                LOG.line(text)
        return child.wait(), "\n".join(lines)
    except KeyboardInterrupt:
        # From a terminal, Ctrl-C reached the child too: give it a moment to stop by itself.
        # From kill, or a closed terminal, it did not: then it is told.
        try:
            child.wait(timeout=CHILD_GRACE_S)
        except subprocess.TimeoutExpired:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=CHILD_STOP_S)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        raise


def run_step(*args: str, quiet: bool = False) -> tuple[int, str]:
    """rutor, tried again on exit 4 (network, or a service that is busy): AGENTS.md says try once more."""
    for attempt in range(1, NETWORK_TRIES + 1):
        code, out = rutor(*args, quiet=quiet)
        if code != 4 or attempt == NETWORK_TRIES:
            return code, out
        say(f"the network or the service failed; trying again in {NETWORK_PAUSE_S} s ({attempt}/{NETWORK_TRIES - 1})")
        time.sleep(NETWORK_PAUSE_S)
    raise AssertionError("unreachable")


def past_deadline() -> bool:
    return DEADLINE is not None and datetime.now(timezone.utc) >= DEADLINE


def nap(seconds: float) -> bool:
    """Sleep, but not past --until. False when the deadline came first."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(seconds=seconds)
    if DEADLINE is not None and end > DEADLINE:
        if DEADLINE > now:
            time.sleep((DEADLINE - now).total_seconds())
        return False
    time.sleep(seconds)
    return True


def refusal(out: str) -> str:
    found = re.search(r"refused: ([a-z_]+)", out)
    return found.group(1) if found else ""


def cheap_hour(out: str) -> datetime | None:
    found = re.search(r"^cheapest_hour_start: (\S+)$", out, flags=re.MULTILINE)
    if not found:
        return None
    try:
        when = datetime.fromisoformat(found.group(1))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def cell_text(cell: Cell) -> str:
    return f"{cell[0]}_{cell[1]}"


def held_locks() -> list[tuple[str, Cell]]:
    code, out = run_step("status")
    if code != 0:
        raise Stop(f"rutor status exited {code}: is this machine logged in? (rutor whoami)")
    found = re.findall(r"^lock: (\S+) (\d+)_(\d+) ", out, flags=re.MULTILINE)
    return [(sheet, (int(col), int(row))) for sheet, col, row in found]


# The cell this machine's run has claimed and not yet sent or given back. A lock the account
# holds is picked up again only when this file names it: on another machine it is that
# machine's cell, half drawn, and two machines must not build one cell.
CLAIM_FILE = "build_cells.claim"


def mark_path(sheet: str) -> Path:
    return ROOT / "work" / sheet / CLAIM_FILE


def mark(sheet: str, cell: Cell) -> None:
    path = mark_path(sheet)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cell_text(cell) + "\n", encoding="utf-8")


def unmark(sheet: str) -> None:
    mark_path(sheet).unlink(missing_ok=True)


def marked(sheet: str) -> Cell | None:
    try:
        return parse_cell(mark_path(sheet).read_text(encoding="utf-8").strip())
    except (OSError, argparse.ArgumentTypeError):
        return None


def release(sheet: str, cell: Cell) -> None:
    code, _out = run_step("release", sheet, cell_text(cell))
    if code != 0:
        say(f"could not give {sheet} {cell_text(cell)} back (exit {code}). Run: rutor release {sheet} {cell_text(cell)}")
        return
    unmark(sheet)


def read_grid(sheet: str) -> tuple[list[Cell], set[Cell], dict]:
    """The cells that can be claimed and the cells that are drawn, as the public grid says now."""
    code, out = run_step("grid", sheet, "--json", quiet=True)
    if code != 0:
        raise Stop(f"rutor grid {sheet} exited {code}")
    try:
        body = json.loads(out.strip().splitlines()[-1])
        meta = body["sheet"]
        cols, rows = int(meta["cols"]), int(meta["rows"])
        states = {(int(c[0]), int(c[1])): str(c[2]) for c in body["cells"]}
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise Stop(f"rutor grid {sheet} answered something this script cannot read ({exc})") from exc
    drawn = {cell for cell, state in states.items() if state in DRAWN}
    if meta.get("made") == "grid":
        # A sheet of the tiling lists only the cells someone has claimed: every other cell is free.
        free = [(c, r) for c in range(cols) for r in range(rows) if states.get((c, r), "fri") == "fri"]
    else:
        free = [cell for cell, state in states.items() if state == "fri"]
    return free, drawn, body


def beside(cell: Cell, cells: set[Cell]) -> bool:
    col, row = cell
    return any((col + dc, row + dr) in cells for dc in (-1, 0, 1) for dr in (-1, 0, 1) if dc or dr)


# Two machines on one login: --part 1/2 and --part 2/2 split the sheet like a chessboard, so they
# draw in the same area and never the same cell. None: every cell is this machine's.
PART: tuple[int, int] | None = None


def in_part(cell: Cell) -> bool:
    return PART is None or (cell[0] + cell[1]) % PART[1] == PART[0] - 1


def next_cell(free: list[Cell], drawn: set[Cell], near: Cell, tried: set[Cell]) -> Cell | None:
    """The free cell to build next: beside something drawn if any is, and nearest `near`.

    Ties go to the row above, then the left. Cells this run has tried already are not taken again:
    the public grid can be a minute behind what this run did.
    """
    left = [cell for cell in free if cell not in tried and in_part(cell)]
    if not left:
        return None
    pool = [cell for cell in left if beside(cell, drawn)] or left
    col, row = near
    return min(pool, key=lambda cell: ((cell[0] - col) ** 2 + (cell[1] - row) ** 2, cell[1], cell[0]))


def claim(sheet: str, cell: Cell | None) -> tuple[str, Cell | None, str]:
    """Claim a cell, or the one rutor claim --next picks. Returns (claimed|pass|waiting|none, cell, detail)."""
    args = ["claim", sheet, cell_text(cell)] if cell else ["claim", sheet, "--next"]
    code, out = run_step(*args)
    if code == 0:
        found = re.search(r"^claimed: \S+ (\d+)_(\d+) ", out, flags=re.MULTILINE)
        if not found:
            raise Stop(f"rutor claim answered without a claimed: line. Check rutor status.")
        claimed = (int(found.group(1)), int(found.group(2)))
        mark(sheet, claimed)
        return "claimed", claimed, ""
    if code != 2:
        raise Stop(f"rutor {' '.join(args)} exited {code}. Nothing was locked.")
    reason = refusal(out)
    if reason in PASS_BY:
        return "pass", cell, out.splitlines()[-1] if out else reason
    if reason in ("too_many_waiting", "too_many_locks"):
        # too_many_locks: the account's one cell is held, by this login's other machine.
        return "waiting", cell, reason
    if reason == "no_free_cell":
        return "none", cell, reason
    why = LIMITS.get(reason, "see the lines above")
    raise Stop(f"the service refused {' '.join(args[1:])} ({reason or 'no reason given'}): {why}. Nothing was locked.")


def draw(sheet: str, cell: Cell, *, cheap: bool, locked: bool) -> tuple[str, str]:
    """Render and redraw a cell. Returns ("drawn", "") or ("flat", detail). Raises Stop.

    locked: this user holds the cell, and a flat one is given back. A cell drawn ahead is held by
    nobody: there is nothing to give back, and it is claimed only when it can be sent.
    """
    name = cell_text(cell)
    code, out = run_step("render", sheet, name)
    if code == 5 and "flat:" in out:
        if locked:
            release(sheet, cell)
        return "flat", f"{'given back; ' if locked else ''}the picture is {ROOT / 'work' / sheet / f'{name}.src.png'}"
    if code != 0:
        raise Stop(f"rutor render {sheet} {name} exited {code}")
    redraw = ["redraw", sheet, name] + (["--wait-for-cheap"] if cheap else [])
    while True:
        code, out = run_step(*redraw)
        if code != 6:
            break
        now = datetime.now(timezone.utc)
        until = cheap_hour(out) or now + timedelta(hours=1)
        until = min(max(until, now), now + SE3_LONGEST_WAIT) + timedelta(minutes=1)
        held = f"{name} stays locked" if locked else f"{name} is drawn ahead"
        say(f"the SE3 price is high; {held}, waiting until {until.astimezone():%Y-%m-%d %H:%M}")
        time.sleep((until - now).total_seconds())
    if code == 5:
        raise Stop(f"{name} failed the quality check (the check: lines above). It is not sent.")
    if code != 0:
        raise Stop(f"rutor redraw {sheet} {name} exited {code}")
    return "drawn", ""


def send(sheet: str, cell: Cell) -> str:
    """Upload a drawn cell this user holds. Returns its url. A Stop here keeps the lock: the cell
    may have reached the service, and it is drawn."""
    name = cell_text(cell)
    while True:
        code, out = run_step("upload", sheet, name)
        if not (code == 2 and refusal(out) == "daily_limit"):
            break
        say(f"20 uploads in 24 hours is the limit for a new builder; trying {name} again in an hour")
        if not nap(UPLOAD_RETRY.total_seconds()):
            raise Stop(
                f"--until came while {name} waited to be sent. It keeps its lock for 48 hours: rutor upload {sheet} {name}",
                keep_lock=True,
                normal=True,
            )
    if code != 0:
        raise Stop(
            f"rutor upload {sheet} {name} exited {code}. It may have reached the service: "
            f"rutor status, then rutor upload {sheet} {name} or rutor release {sheet} {name}",
            keep_lock=True,
        )
    unmark(sheet)
    url = re.search(r"^url: (\S+)", out, flags=re.MULTILINE)
    return url.group(1) if url else ""


def build(sheet: str, cell: Cell, *, cheap: bool, drawn_already: bool = False) -> tuple[str, str]:
    """Draw and send a cell this user holds. Returns (built|flat, detail). Raises Stop.

    drawn_already: the cell was drawn ahead and only waits to be sent.
    """
    name = cell_text(cell)
    sending = False
    try:
        if not drawn_already:
            what, detail = draw(sheet, cell, cheap=cheap, locked=True)
            if what == "flat":
                return "flat", detail
        sending = True
        return "built", send(sheet, cell)
    except Stop as stop:
        if not stop.keep_lock:
            release(sheet, cell)
        raise
    except KeyboardInterrupt:
        if sending:
            say(f"stopped while {name} was being sent: it is kept. rutor status says whether it arrived.")
        else:
            say(f"stopped: giving {name} back")
            release(sheet, cell)
        raise


# Cells drawn ahead of a claim, oldest first: drawn and checked while five cells waited for
# review, to be claimed and sent as soon as the service takes another. Kept on disk, so the next
# run sends what this one drew.
AHEAD_FILE = "build_cells.ahead"


def ahead_path(sheet: str) -> Path:
    return ROOT / "work" / sheet / AHEAD_FILE


def ready_to_send(sheet: str, cell: Cell) -> bool:
    """The picture and a manifest that passed the check are both there."""
    folder = ROOT / "work" / sheet
    name = cell_text(cell)
    try:
        manifest = json.loads((folder / f"{name}.manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (folder / f"{name}.png").is_file() and isinstance(manifest, dict) and manifest.get("check") == "ok"


def load_ahead(sheet: str) -> list[Cell]:
    try:
        lines = ahead_path(sheet).read_text(encoding="utf-8").split()
    except OSError:
        return []
    cells: list[Cell] = []
    for line in lines:
        try:
            cell = parse_cell(line)
        except argparse.ArgumentTypeError:
            continue
        if cell not in cells and ready_to_send(sheet, cell):
            cells.append(cell)
    return cells


def save_ahead(sheet: str, cells: list[Cell]) -> None:
    path = ahead_path(sheet)
    if not cells:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(cell_text(cell) + "\n" for cell in cells), encoding="utf-8")


def send_ahead(sheet: str, ahead: list[Cell]) -> tuple[list[str], list[str]]:
    """Claim and send cells drawn ahead, oldest first, for as long as the service takes them.

    Returns (sent, passed by). A cell someone else claimed meanwhile is dropped from the queue.
    """
    sent: list[str] = []
    passed: list[str] = []
    for cell in list(ahead):
        name = cell_text(cell)
        say(f"--- {sheet} {name}: drawn ahead; can it be sent now? ---")
        what, _cell, detail = claim(sheet, cell)
        if what == "waiting":
            say("not yet: five of your cells still wait for review")
            break
        if what != "claimed":
            ahead.remove(cell)
            save_ahead(sheet, ahead)
            passed.append(f"{name} drawn ahead, but {detail}")
            say(f"dropped: {sheet} {name}, drawn ahead, can no longer be claimed")
            continue
        what, url = build(sheet, cell, cheap=False, drawn_already=True)
        ahead.remove(cell)
        save_ahead(sheet, ahead)
        sent.append(f"{name} {url}")
        say(f"sent: {sheet} {name}, drawn ahead, now waiting for review")
    return sent, passed


def whoami() -> str:
    code, out = run_step("whoami")
    found = re.search(r"^login: (\S+)", out, flags=re.MULTILINE)
    return found.group(1) if code == 0 and found else ""


def latest_own_cell(sheet: str, login: str) -> Cell | None:
    """The cell of this login on the sheet that changed last: where an earlier run left off."""
    if not login:
        return None
    _free, _drawn, body = read_grid(sheet)
    own = [c for c in body.get("cells", []) if len(c) > 4 and c[3] == login and str(c[2]) in DRAWN | {"last"}]
    if not own:
        return None
    newest = max(own, key=lambda c: str(c[4] or ""))
    return int(newest[0]), int(newest[1])


def parse_cell(text: str) -> Cell:
    found = re.fullmatch(r"(\d+)_(\d+)", text)
    if not found:
        raise argparse.ArgumentTypeError(f"a cell is col_row, like 85_65: {text!r}")
    return int(found.group(1)), int(found.group(2))


def parse_part(text: str) -> tuple[int, int]:
    found = re.fullmatch(r"(\d+)/(\d+)", text)
    if not found or not 1 <= int(found.group(1)) <= int(found.group(2)):
        raise argparse.ArgumentTypeError(f"--part is like 1/2: {text!r}")
    return int(found.group(1)), int(found.group(2))


def parse_until(text: str) -> datetime:
    """HH:MM, local time: the next time the clock shows it."""
    found = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not found or int(found.group(1)) > 23 or int(found.group(2)) > 59:
        raise argparse.ArgumentTypeError(f"--until is a time like 07:00: {text!r}")
    now = datetime.now().astimezone()
    when = now.replace(hour=int(found.group(1)), minute=int(found.group(2)), second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    return when.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    global LOG, DEADLINE, PART
    parser = argparse.ArgumentParser(description="Build cells one after another, by itself, until told to stop.")
    parser.add_argument("sheet")
    parser.add_argument("cell", type=parse_cell, nargs="?", help="where to start, like 85_65 (default: on from your newest cell, or where rutor claim --next would)")
    parser.add_argument("--step", choices=sorted(STEPS), default="down", help="from the cell given, the way the first line goes (default down)")
    parser.add_argument("--until", type=parse_until, help="take no new cell after this local time, like 07:00")
    parser.add_argument("--count", type=int, default=0, help="send at most this many cells (default: no limit)")
    parser.add_argument("--ahead", type=int, default=5, metavar="N", help="while five cells wait for review, draw up to N more ahead, to send as reviews let them (default 5, 0 to only wait)")
    parser.add_argument("--review-wait", type=int, default=30, metavar="MIN", help="minutes between asks while five cells wait for review and nothing is left to draw ahead (default 30)")
    parser.add_argument("--cheap", action="store_true", help="redraw only in a cheap SE3 hour, waiting with the lock held")
    parser.add_argument("--part", type=parse_part, help="with two machines on one login: 1/2 on one, 2/2 on the other, so they never draw the same cell")
    args = parser.parse_args(argv)
    PART = args.part

    LOG = Log(ROOT / "work" / "logs" / f"build_cells-{datetime.now():%Y%m%d-%H%M%S}.log")
    DEADLINE = args.until
    say(f"log: {LOG.path}")
    if DEADLINE is not None:
        say(f"no new cell after {DEADLINE.astimezone():%Y-%m-%d %H:%M}")
    sheet = args.sheet
    built: list[str] = []
    passed: list[str] = []
    tried: set[Cell] = set()
    mine: set[Cell] = set()
    ahead: list[Cell] = load_ahead(sheet)
    save_ahead(sheet, ahead)
    tried |= set(ahead)
    mine |= set(ahead)
    # The cell to claim next. None: let rutor claim --next pick.
    target: Cell | None = args.cell
    # Where the next free cell is looked for: the cell built last, or the start.
    near: Cell | None = args.cell
    # The first line's direction, while the line lasts.
    line = STEPS[args.step] if args.cell else None
    flat_run = 0
    ended, normal = "", True

    # The next cell is chosen when it is needed: after what was drawn ahead has had its chance.
    choose_next = False

    def next_target() -> Cell | None:
        free, drawn, _body = read_grid(sheet)
        assert near is not None
        found = next_cell(free, drawn | mine, near, tried)
        if found is not None:
            say(f"next: {cell_text(found)}, the free cell beside the drawn ones nearest {cell_text(near)}")
        return found

    try:
        code, _out = run_step("preflight", "--sheet", sheet)
        if code != 0:
            raise Stop("preflight has a line that says missing (above). Fix it first; nothing was claimed.")
        locks = held_locks()
        here = marked(sheet)
        resume: Cell | None = here if (sheet, here) in locks else None
        for held_sheet, held_cell in locks:
            if (held_sheet, held_cell) != (sheet, resume):
                # Claimed by no run here: the other machine's. It is never drawn here.
                say(f"{held_sheet} {cell_text(held_cell)} is held, but not by this machine: left to the other one")
                if held_sheet == sheet:
                    tried.add(held_cell)
        if resume is None:
            unmark(sheet)  # a mark with no lock behind it: the lock ran out, or was given back by hand
        if ahead:
            say(f"drawn ahead from before, to send first: {', '.join(cell_text(c) for c in ahead)}")
        if args.cell is None:
            # Go on from where the last run left off: its newest cell drawn ahead, else this
            # login's newest cell on the sheet. With neither, rutor claim --next picks.
            near = ahead[-1] if ahead else latest_own_cell(sheet, whoami())
            if near is not None:
                say(f"going on from {cell_text(near)}")
                choose_next = True

        while True:
            if args.count and len(built) >= args.count:
                raise Stop(f"{args.count} cells sent, as asked", normal=True)
            if past_deadline():
                raise Stop("it is past --until", normal=True)

            # After this cell, is the target done with? Not when a held cell went before it.
            target_done = True
            cell: Cell | None = None
            drawn_ahead_now = False
            if resume is not None:
                cell, resume = resume, None
                target_done = target is None or cell == target
                say(f"--- {sheet} {cell_text(cell)}: still yours from an earlier run, finished first ---")
            else:
                # First what was drawn ahead, while the service takes it.
                if ahead:
                    sent_now, passed_now = send_ahead(sheet, ahead)
                    built += sent_now
                    passed += passed_now
                    if args.count and len(built) >= args.count:
                        continue
                if choose_next:
                    found = next_target()
                    if found is None:
                        if not ahead:
                            raise Stop(f"no free cell is left on {sheet}", normal=True)
                        say(f"no free cell is left to draw; {len(ahead)} drawn ahead still to send. Asking again in {args.review_wait} min.")
                        if not nap(args.review_wait * 60):
                            raise Stop("it is past --until", normal=True)
                        continue
                    target, choose_next = found, False
                say(f"--- {sheet} {cell_text(target) if target else '(the cell rutor claim --next picks)'} ---")
                what, cell, detail = claim(sheet, target)
                if what == "waiting":
                    if target is not None and len(ahead) < args.ahead:
                        say(f"five of your cells wait for review: drawing {cell_text(target)} ahead, to send when one is reviewed")
                        cell = target
                        drawn_ahead_now = True
                    else:
                        full = f"{len(ahead)} drawn ahead already; " if ahead else ""
                        say(f"five of your cells wait for review; {full}asking again in {args.review_wait} min. Nothing is locked meanwhile.")
                        if not nap(args.review_wait * 60):
                            raise Stop("it is past --until; five cells still wait for review", normal=True)
                        continue
                elif what == "none":
                    raise Stop(f"no cell is free on {sheet}", normal=True)
                elif what == "pass":
                    if cell is None:
                        say("the cell rutor claim --next picked was taken meanwhile; asking again in a minute")
                        if not nap(60):
                            raise Stop("it is past --until", normal=True)
                        continue
                    tried.add(cell)
                    passed.append(f"{cell_text(cell)}: {detail}")
                    say(f"passed by: {sheet} {cell_text(cell)}")
                    if line and refusal(detail) in ("no_cell", "outside"):
                        line = None  # the line has reached the edge, or ground the sheet does not offer
                    cell = None

            if cell is not None:
                tried.add(cell)
                if drawn_ahead_now:
                    what, detail = draw(sheet, cell, cheap=args.cheap, locked=False)
                elif cell in ahead:
                    what, detail = build(sheet, cell, cheap=args.cheap, drawn_already=True)
                    ahead.remove(cell)
                    save_ahead(sheet, ahead)
                else:
                    what, detail = build(sheet, cell, cheap=args.cheap)
                if what in ("built", "drawn"):
                    mine.add(cell)
                    near = cell
                    flat_run = 0
                    if what == "built":
                        built.append(f"{cell_text(cell)} {detail}")
                        say(f"built: {sheet} {cell_text(cell)}, waiting for review")
                    else:
                        ahead.append(cell)
                        save_ahead(sheet, ahead)
                        say(f"drawn ahead: {sheet} {cell_text(cell)}, sent when the service takes another ({len(ahead)} waiting to be sent)")
                else:
                    passed.append(f"{cell_text(cell)} flat: {detail}")
                    say(f"passed by: {sheet} {cell_text(cell)} (flat)")
                    flat_run += 1
                    if flat_run >= FLAT_IN_A_ROW:
                        raise Stop(f"{FLAT_IN_A_ROW} flat cells in a row: little stands up around here. Start somewhere else.")
                    if near is None:
                        near = cell
            if not target_done or (args.count and len(built) >= args.count):
                continue

            # Where next: along the first line while it lasts, then beside what is drawn.
            if line and target is not None:
                step = (target[0] + line[0], target[1] + line[1])
                # Past the other machine's cells: the one it holds, and with --part, its half.
                while step[0] >= 0 and step[1] >= 0 and (step in tried or not in_part(step)):
                    step = (step[0] + line[0], step[1] + line[1])
                if step[0] >= 0 and step[1] >= 0:
                    target = step
                    continue
                line = None
            if near is None:
                target = None  # nothing built or tried near anything yet: rutor claim --next picks
                continue
            choose_next = True
    except Stop as stop:
        ended, normal = str(stop), stop.normal
    except KeyboardInterrupt:
        ended, normal = "stopped with Ctrl-C", False

    say("=== summary ===")
    for text in built:
        say(f"built: {text}")
    for text in passed:
        say(f"passed by: {text}")
    if ahead:
        say(f"drawn ahead, not sent yet: {', '.join(cell_text(c) for c in ahead)}. The next run sends them first.")
    say(f"stopped: {ended}")
    if built:
        say("A person reviews each cell before it goes on the map. rutor status lists open locks only.")
    LOG.close()
    return 0 if normal else 1


AWAKE_WHAT = "sleep:idle:handle-lid-switch"


def stay_awake() -> subprocess.Popen[bytes] | None:
    """Keep the machine from sleeping, even with the lid closed, for as long as this process lives.

    systemd-inhibit holds the block while `tail --pid` waits for this process to end, so the block
    ends with the run however it ends, a kill included. It runs in a session of its own, so a
    Ctrl-C does not end it before this run has given its cell back. Where it is not allowed or
    not there, the run goes on without it.
    """
    if not sys.platform.startswith("linux"):
        return None
    tool, tail = shutil.which("systemd-inhibit"), shutil.which("tail")
    if tool is None or tail is None:
        return None
    block = [tool, f"--what={AWAKE_WHAT}", "--who=build_cells", "--why=building map cells", "--mode=block"]
    if subprocess.run([*block, "true"], capture_output=True, check=False).returncode != 0:
        return None
    return subprocess.Popen(
        [*block, tail, f"--pid={os.getpid()}", "-f", "/dev/null"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_like_ctrl_c(signum: int, frame: object) -> None:
    """kill and a closed terminal stop the run as Ctrl-C does: the cell is given back."""
    raise KeyboardInterrupt


if __name__ == "__main__":
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop_like_ctrl_c)
    awake = stay_awake()
    if awake is not None:
        print("build_cells: this machine will not sleep, not even with the lid closed, until the run ends", flush=True)
    try:
        sys.exit(main())
    finally:
        if awake is not None:
            awake.terminate()
