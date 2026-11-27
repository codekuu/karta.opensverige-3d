"""Every branch of tools/build_cells.py against a scripted rutor. No service, no GPU.

    .venv/bin/python tools/test_build_cells.py
"""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("build_cells.py")


def load():
    spec = importlib.util.spec_from_file_location("build_cells", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hand_grid(free, size=(144, 70), area=((80, 90), (60, 70)), drawn="live"):
    """A hand-made sheet: `free` cells are fri, the rest of `area` is drawn, outside it ute."""
    cells = []
    for c in range(*area[0]):
        for r in range(*area[1]):
            cells.append([c, r, "fri" if (c, r) in free else drawn, None, ""])
    return {"sheet": {"id": "stockholm-norr", "cols": size[0], "rows": size[1], "made": "hand"}, "cells": cells}


class Fake:
    """rutor as a table: (command, cell) -> list of (code, out), used in turn; default success."""

    def __init__(self, table, status="locks: 0", grid=None, login="codekuu"):
        self.table = {key: list(value) for key, value in table.items()}
        self.status = status
        self.grid = grid if grid is not None else hand_grid(set())
        self.login = login
        self.calls = []

    def __call__(self, *args, quiet=False):
        self.calls.append(" ".join(args))
        command = args[0]
        if command == "preflight":
            return self.table.get(("preflight", None), [(0, "check: gpu ok")])[0]
        if command == "status":
            return 0, self.status
        if command == "grid":
            return 0, json.dumps(self.grid)
        if command == "whoami":
            return 0, f"login: {self.login}\ntrusted: no\napproved: 0"
        cell = args[2]
        queue = self.table.get((command, cell))
        if queue:
            result = queue.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        if command == "claim":
            return 0, f"claimed: {args[1]} {cell} expires 2026-11-29T00:00:00Z\nurl: https://map/{cell}"
        if command == "upload":
            return 0, f"uploaded: x {cell} state vantar\nurl: https://map/{cell}"
        return 0, f"{command}: ok"


def claims(calls):
    return [c.split()[2] for c in calls if c.startswith("claim")]


def releases(calls):
    return [c.split()[2] for c in calls if c.startswith("release")]


def uploads(calls):
    return [c.split()[2] for c in calls if c.startswith("upload")]


class BuildCellsTests(unittest.TestCase):
    def run_cells(self, table, argv=("stockholm-norr", "85_65", "--count", "1"), status="locks: 0", grid=None, nap=True, marked=None, ahead=(), login="codekuu"):
        module = load()
        fake = Fake(table, status, grid, login)
        module.rutor = fake
        module.nap = lambda seconds: nap
        module.time.sleep = lambda seconds: None
        module.NETWORK_PAUSE_S = 0
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        module.ROOT = Path(tmp.name)
        self.mark = Path(tmp.name) / "work" / argv[0] / module.CLAIM_FILE
        self.ahead_file = Path(tmp.name) / "work" / argv[0] / module.AHEAD_FILE
        self.mark.parent.mkdir(parents=True, exist_ok=True)
        if marked:
            self.mark.write_text(marked + "\n")
        for name in ahead:
            (self.mark.parent / f"{name}.png").write_bytes(b"png")
            (self.mark.parent / f"{name}.manifest.json").write_text('{"check": "ok"}')
        if ahead:
            self.ahead_file.write_text("".join(f"{name}\n" for name in ahead))
        out = io.StringIO()
        with redirect_stdout(out):
            code = module.main(list(argv))
        return code, fake.calls, out.getvalue()

    # ---- the first line, then on by itself

    def test_down_the_column_then_on_to_the_nearest_free_cell_beside_the_drawn(self):
        free = {(85, 65), (85, 66), (85, 67), (85, 68), (85, 69), (84, 69), (86, 68), (81, 61)}
        code, calls, out = self.run_cells(
            {("claim", "85_70"): [(2, "error: refused: no_cell: No such cell.")]},
            argv=("stockholm-norr", "85_65", "--count", "7"),
            grid=hand_grid(free),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["85_65", "85_66", "85_67", "85_68", "85_69", "85_70", "84_69", "86_68"])
        self.assertIn("next: 84_69, the free cell beside the drawn ones nearest 85_69", out)
        self.assertIn("7 cells sent, as asked", out)

    def test_a_taken_and_a_flat_cell_on_the_line_are_passed_by_and_the_line_goes_on(self):
        code, calls, out = self.run_cells(
            {
                ("claim", "85_66"): [(2, "error: refused: taken: 85_66 is locked by someone")],
                ("render", "85_67"): [(5, "flat: 3% of this cell rises above the ground")],
            },
            argv=("stockholm-norr", "85_65", "--count", "2"),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["85_65", "85_66", "85_67", "85_68"])
        self.assertEqual(releases(calls), ["85_67"])
        self.assertEqual(uploads(calls), ["85_65", "85_68"])
        self.assertNotIn("--flat-ok", " ".join(calls))

    def test_with_no_cell_it_starts_where_claim_next_picks(self):
        code, calls, out = self.run_cells(
            {("claim", "--next"): [(0, "claimed: stockholm-norr 85_65 expires x\nurl: u")]},
            argv=("stockholm-norr", "--count", "2"),
            grid=hand_grid({(85, 65), (85, 66), (88, 61)}),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["--next", "85_66"])
        self.assertEqual(uploads(calls), ["85_65", "85_66"])

    def test_a_cell_claim_next_picked_but_someone_took_is_asked_again(self):
        code, calls, out = self.run_cells(
            {("claim", "--next"): [(2, "refused: taken: x 1_1 is locked"), (0, "claimed: stockholm-norr 85_65 expires x")]},
            argv=("stockholm-norr", "--count", "1"),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["--next", "--next"])

    def test_nothing_free_is_a_normal_end(self):
        code, calls, out = self.run_cells(
            {("claim", "85_70"): [(2, "refused: no_cell: x")]},
            argv=("stockholm-norr", "85_69"),
            grid=hand_grid({(85, 69)}),
        )
        self.assertEqual(code, 0, out)
        self.assertIn("no free cell is left on stockholm-norr", out)
        code, calls, out = self.run_cells({("claim", "--next"): [(2, "refused: no_free_cell: none")]}, argv=("stockholm-norr",))
        self.assertEqual(code, 0, out)
        self.assertIn("no cell is free on stockholm-norr", out)

    def test_up_turns_to_the_nearest_at_row_zero(self):
        code, calls, out = self.run_cells({}, argv=("s", "84_1", "--step", "up", "--count", "3"), grid=hand_grid({(84, 1), (84, 0), (83, 0)}, area=((80, 90), (0, 5))))
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["84_1", "84_0", "83_0"])

    # ---- picking up a cell from before

    def test_a_held_start_cell_is_finished_without_a_claim_then_the_line_goes_on(self):
        code, calls, out = self.run_cells({}, argv=("stockholm-norr", "85_65", "--count", "2"), status="lock: stockholm-norr 85_65 expires 2026-11-29T00:00:00Z", marked="85_65")
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["85_66"])
        self.assertEqual(uploads(calls), ["85_65", "85_66"])

    def test_a_held_other_cell_is_finished_first_then_the_start_is_claimed(self):
        code, calls, out = self.run_cells({}, argv=("stockholm-norr", "85_65", "--count", "3"), status="lock: stockholm-norr 12_3 expires 2026-11-29T00:00:00Z", marked="12_3")
        self.assertEqual(code, 0, out)
        self.assertEqual(uploads(calls), ["12_3", "85_65", "85_66"])
        self.assertEqual(claims(calls), ["85_65", "85_66"])

    def test_a_held_cell_with_no_start_goes_on_beside_it(self):
        code, calls, out = self.run_cells(
            {}, argv=("stockholm-norr", "--count", "2"), status="lock: stockholm-norr 85_65 expires x", grid=hand_grid({(85, 66), (89, 61)}), marked="85_65"
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["85_66"])
        self.assertEqual(uploads(calls), ["85_65", "85_66"])

    def test_a_lock_on_another_sheet_is_left_alone(self):
        code, calls, out = self.run_cells({}, status="lock: goteborg-norr 12_3 expires x")
        self.assertEqual(code, 0, out)
        self.assertIn("goteborg-norr 12_3 is held, but not by this machine", out)
        self.assertNotIn("12_3", releases(calls))

    # ---- one machine at a time

    def test_a_lock_no_run_here_claimed_is_left_to_the_machine_that_holds_it(self):
        # The other machine holds 85_66. This one never claims, draws or gives it back.
        code, calls, out = self.run_cells({}, argv=("stockholm-norr", "85_65", "--count", "2"), status="lock: stockholm-norr 85_66 expires x")
        self.assertEqual(code, 0, out)
        self.assertIn("left to the other one", out)
        self.assertNotIn("85_66", claims(calls) + releases(calls) + uploads(calls))
        self.assertEqual(uploads(calls), ["85_65", "85_67"])

    def test_while_the_other_machine_holds_the_one_cell_this_one_draws_ahead(self):
        locks = '(2, "error: refused: too_many_locks: You already hold as many cells as you may.")'
        code, calls, out = self.run_cells(
            {("claim", "85_65"): [eval(locks), (0, "claimed: s 85_65 expires x")]},
            argv=("stockholm-norr", "85_65", "--count", "1"),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["85_65", "85_65"])
        self.assertEqual([c.split()[2] for c in calls if c.startswith("render")], ["85_65"])
        self.assertEqual(uploads(calls), ["85_65"])

    def test_two_parts_split_the_sheet_like_a_chessboard(self):
        module = load()
        free = [(c, r) for c in range(4) for r in range(4)]
        module.PART = (1, 2)
        one = {module.next_cell([cell], set(), (0, 0), set()) for cell in free} - {None}
        module.PART = (2, 2)
        two = {module.next_cell([cell], set(), (0, 0), set()) for cell in free} - {None}
        self.assertEqual(len(one), 8)
        self.assertEqual(len(two), 8)
        self.assertFalse(one & two)

    def test_the_mark_is_cleared_when_the_cell_is_sent(self):
        # That it is written on the claim shows below, where the cell keeps its lock.
        code, calls, out = self.run_cells({})
        self.assertEqual(code, 0, out)
        self.assertEqual(uploads(calls), ["85_65"])
        self.assertFalse(self.mark.exists())

    def test_the_mark_stays_when_the_cell_keeps_its_lock(self):
        code, calls, out = self.run_cells({("upload", "85_65"): [KeyboardInterrupt()]})
        self.assertEqual(code, 1)
        self.assertEqual(self.mark.read_text().strip(), "85_65")
        code, calls, out = self.run_cells({("upload", "85_65"): [(1, "error: something")]})
        self.assertEqual(self.mark.read_text().strip(), "85_65")

    def test_the_mark_goes_when_the_cell_is_given_back(self):
        code, calls, out = self.run_cells({("render", "85_65"): [(3, "error: no")]})
        self.assertEqual(releases(calls), ["85_65"])
        self.assertFalse(self.mark.exists())

    def test_a_mark_with_no_lock_behind_it_is_cleared(self):
        code, calls, out = self.run_cells({("claim", "85_65"): [(2, "refused: sheet_closed: closed")]}, marked="40_40")
        self.assertFalse(self.mark.exists())

    # ---- waiting

    def test_five_waiting_waits_and_asks_again_holding_nothing(self):
        code, calls, out = self.run_cells(
            {("claim", "85_66"): [(2, "refused: too_many_waiting: x"), (2, "refused: too_many_waiting: x"), (0, "claimed: s 85_66 expires x")]},
            argv=("stockholm-norr", "85_65", "--count", "2", "--ahead", "0"),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["85_65", "85_66", "85_66", "85_66"])
        self.assertFalse(releases(calls))
        self.assertIn("asking again in 30 min", out)

    def test_until_ends_the_wait_for_review(self):
        code, calls, out = self.run_cells({("claim", "85_66"): [(2, "refused: too_many_waiting: x")]}, argv=("stockholm-norr", "85_65", "--ahead", "0"), nap=False)
        self.assertEqual(code, 0, out)
        self.assertIn("past --until; five cells still wait for review", out)

    def test_the_daily_limit_waits_with_the_drawn_cell_locked(self):
        code, calls, out = self.run_cells({("upload", "85_65"): [(2, "refused: daily_limit: 20"), (0, "uploaded: x\nurl: u")]})
        self.assertEqual(code, 0, out)
        self.assertEqual(uploads(calls), ["85_65", "85_65"])
        self.assertFalse(releases(calls))
        code, calls, out = self.run_cells({("upload", "85_65"): [(2, "refused: daily_limit: 20")]}, nap=False)
        self.assertEqual(code, 0, out)
        self.assertFalse(releases(calls))
        self.assertIn("rutor upload stockholm-norr 85_65", out)

    def test_a_cheap_hour_is_waited_for_with_the_lock(self):
        code, calls, out = self.run_cells(
            {("redraw", "85_65"): [(6, "cheapest_hour_start: 2000-01-01T02:00:00+00:00"), (0, "check: ok")]},
            argv=("stockholm-norr", "85_65", "--count", "1", "--cheap"),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(calls.count("redraw stockholm-norr 85_65 --wait-for-cheap"), 2)
        self.assertFalse(releases(calls))


    # ---- drawing ahead while five wait for review

    def renders(self, calls):
        return [c.split()[2] for c in calls if c.startswith("render")]

    def test_while_five_wait_it_draws_ahead_and_sends_each_when_a_slot_opens(self):
        code, calls, out = self.run_cells(
            {
                ("claim", "85_66"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review."), (2, "error: refused: too_many_waiting: Five of your cells are waiting for review."), (0, "claimed: s 85_66 expires x")],
                ("claim", "85_67"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review.")],
            },
            argv=("stockholm-norr", "85_65", "--count", "3"),
        )
        self.assertEqual(code, 0, out)
        # 85_66 and 85_67 are drawn once each, ahead, and sent later without drawing again.
        self.assertEqual(self.renders(calls), ["85_65", "85_66", "85_67"])
        self.assertEqual(uploads(calls), ["85_65", "85_66", "85_67"])
        self.assertEqual(claims(calls), ["85_65", "85_66", "85_66", "85_67", "85_66", "85_67"])
        self.assertFalse(releases(calls))
        self.assertIn("drawing 85_66 ahead", out)
        self.assertIn("sent: stockholm-norr 85_66, drawn ahead", out)
        self.assertFalse(self.ahead_file.exists())

    def test_after_each_cell_drawn_ahead_it_asks_whether_one_can_be_sent(self):
        code, calls, out = self.run_cells(
            {("claim", "85_66"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review.")] * 3, ("claim", "85_67"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review.")], ("claim", "85_68"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review.")]},
            argv=("stockholm-norr", "85_65", "--count", "2", "--ahead", "2"),
            nap=False,
        )
        # 85_66 is asked for before each new cell: after 85_66 was drawn, and after 85_67.
        self.assertEqual(claims(calls), ["85_65", "85_66", "85_66", "85_67", "85_66", "85_68"])
        self.assertEqual(self.renders(calls), ["85_65", "85_66", "85_67"])
        self.assertIn("2 drawn ahead already", out)
        self.assertEqual(self.ahead_file.read_text().split(), ["85_66", "85_67"])
        self.assertIn("drawn ahead, not sent yet: 85_66, 85_67", out)

    def test_a_full_queue_waits_without_drawing_more(self):
        code, calls, out = self.run_cells(
            {("claim", "85_66"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review."), (2, "error: refused: too_many_waiting: Five of your cells are waiting for review."), (0, "claimed: s 85_66 expires x")], ("claim", "85_67"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review.")]},
            argv=("stockholm-norr", "85_65", "--count", "2", "--ahead", "1"),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(self.renders(calls), ["85_65", "85_66"])
        self.assertEqual(uploads(calls), ["85_65", "85_66"])
        self.assertIn("1 drawn ahead already; asking again in 30 min", out)

    def test_a_cell_drawn_ahead_that_someone_claimed_is_dropped(self):
        code, calls, out = self.run_cells(
            {("claim", "85_66"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review."), (2, "error: refused: taken: stockholm-norr 85_66 is locked by someone")]},
            argv=("stockholm-norr", "85_65", "--count", "2"),
        )
        self.assertEqual(code, 0, out)
        self.assertNotIn("85_66", uploads(calls))
        self.assertIn("dropped: stockholm-norr 85_66", out)
        self.assertEqual(uploads(calls), ["85_65", "85_67"])

    def test_a_cell_drawn_ahead_is_never_given_back_or_sent_unclaimed(self):
        code, calls, out = self.run_cells(
            {("claim", "85_66"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review.")], ("render", "85_66"): [(3, "error: Cesium ion quota is used up")]},
            argv=("stockholm-norr", "85_65", "--count", "3"),
        )
        self.assertEqual(code, 1)
        self.assertFalse(releases(calls))
        self.assertEqual(uploads(calls), ["85_65"])
        code, calls, out = self.run_cells(
            {("claim", "85_66"): [(2, "error: refused: too_many_waiting: Five of your cells are waiting for review.")], ("render", "85_66"): [(5, "flat: 0% of this cell rises above the ground")], ("claim", "85_67"): [(2, "refused: no_cell: x")]},
            argv=("stockholm-norr", "85_65", "--count", "2"),
            grid=hand_grid(set()),
        )
        self.assertFalse(releases(calls))
        self.assertIn("85_66 flat", out)

    def test_a_start_cell_that_is_drawn_ahead_already_is_not_drawn_again(self):
        wait = (2, "error: refused: too_many_waiting: Five of your cells are waiting for review.")
        code, calls, out = self.run_cells(
            {("claim", "71_11"): [wait] * 5, ("claim", "71_12"): [wait] * 5, ("claim", "75_15"): [wait] * 5, ("claim", "71_13"): [wait] * 5},
            argv=("stockholm-norr", "71_11", "--ahead", "3"),
            ahead=("75_15", "71_11"),
            nap=False,
        )
        self.assertNotIn("71_11", [c.split()[2] for c in calls if c.startswith("render")])
        self.assertIn("71_11 is drawn ahead already", out)
        self.assertEqual([c.split()[2] for c in calls if c.startswith("render")], ["71_12"])
        self.assertEqual(self.ahead_file.read_text().split(), ["75_15", "71_11", "71_12"])

    def test_the_next_run_sends_what_this_one_drew_ahead_first(self):
        code, calls, out = self.run_cells({}, argv=("stockholm-norr", "--count", "2"), ahead=("85_66", "85_67"))
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["85_66", "85_67"])
        self.assertEqual(uploads(calls), ["85_66", "85_67"])
        self.assertFalse(self.renders(calls))
        self.assertIn("going on from 85_67", out)
        self.assertFalse(self.ahead_file.exists())

    def test_a_queued_cell_whose_files_are_gone_or_failed_is_forgotten(self):
        module = load()
        tmp = Path(tempfile.mkdtemp())
        module.ROOT = tmp
        folder = tmp / "work" / "s"
        folder.mkdir(parents=True)
        (folder / module.AHEAD_FILE).write_text("85_66\n85_67\n")
        (folder / "85_67.png").write_bytes(b"png")
        (folder / "85_67.manifest.json").write_text('{"check": "fail"}')
        self.assertEqual(module.load_ahead("s"), [])

    def test_a_held_cell_that_was_drawn_ahead_is_only_sent(self):
        code, calls, out = self.run_cells(
            {}, argv=("stockholm-norr", "--count", "1"), status="lock: stockholm-norr 85_66 expires x", marked="85_66", ahead=("85_66",)
        )
        self.assertEqual(code, 0, out)
        self.assertFalse(self.renders(calls))
        self.assertEqual(uploads(calls), ["85_66"])
        self.assertFalse([c for c in calls if c.startswith("claim")])

    def test_with_no_cell_it_goes_on_from_this_logins_newest_cell(self):
        grid = hand_grid({(75, 15), (75, 14), (80, 61)}, area=((70, 90), (10, 70)))
        for c in grid["cells"]:
            if (c[0], c[1]) in ((75, 16), (75, 20)):
                c[2], c[3] = "vantar", "codekuu"
                c[4] = "2026-11-27T18:00:00Z" if c[1] == 16 else "2026-11-27T17:00:00Z"
        code, calls, out = self.run_cells({}, argv=("stockholm-norr", "--count", "1"), grid=grid)
        self.assertEqual(code, 0, out)
        self.assertIn("going on from 75_16", out)
        self.assertEqual(claims(calls), ["75_15"])

    # ---- stops

    def test_a_failed_check_is_not_sent_and_the_cell_is_given_back(self):
        code, calls, out = self.run_cells({("redraw", "85_65"): [(5, "check: tiles too flat")]})
        self.assertEqual(code, 1)
        self.assertFalse(uploads(calls))
        self.assertEqual(releases(calls), ["85_65"])

    def test_a_render_that_fails_gives_the_cell_back(self):
        code, calls, out = self.run_cells({("render", "85_65"): [(3, "error: Cesium ion quota is used up")]})
        self.assertEqual(code, 1)
        self.assertEqual(releases(calls), ["85_65"])

    def test_the_network_is_tried_again_then_given_up(self):
        code, calls, out = self.run_cells({("render", "85_65"): [(4, "e"), (4, "e"), (0, "rendered")]})
        self.assertEqual(code, 0, out)
        self.assertEqual(calls.count("render stockholm-norr 85_65"), 3)
        code, calls, out = self.run_cells({("render", "85_65"): [(4, "e"), (4, "e"), (4, "e")]})
        self.assertEqual(code, 1)
        self.assertEqual(releases(calls), ["85_65"])

    def test_an_upload_that_fails_otherwise_keeps_the_cell(self):
        code, calls, out = self.run_cells({("upload", "85_65"): [(1, "error: something")]})
        self.assertEqual(code, 1)
        self.assertFalse(releases(calls))

    def test_an_unknown_refusal_stops_with_nothing_locked(self):
        code, calls, out = self.run_cells({("claim", "85_65"): [(2, "refused: sheet_closed: closed")]})
        self.assertEqual(code, 1)
        self.assertFalse(releases(calls))
        self.assertIn("the sheet is closed", out)

    def test_ctrl_c_in_the_render_gives_the_cell_back(self):
        code, calls, out = self.run_cells({("render", "85_65"): [KeyboardInterrupt()]})
        self.assertEqual(code, 1)
        self.assertEqual(releases(calls), ["85_65"])
        self.assertIn("stopped with Ctrl-C", out)

    def test_ctrl_c_while_sending_keeps_the_cell(self):
        code, calls, out = self.run_cells({("upload", "85_65"): [KeyboardInterrupt()]})
        self.assertEqual(code, 1)
        self.assertFalse(releases(calls))

    def test_a_failed_preflight_claims_nothing(self):
        code, calls, out = self.run_cells({("preflight", None): [(3, "check: cesium token missing")]})
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["preflight --sheet stockholm-norr"])

    def test_five_flat_cells_in_a_row_stop_the_run(self):
        flat = (5, "flat: 0% of this cell rises above the ground")
        table = {("render", f"85_{r}"): [flat] for r in range(60, 70)}
        code, calls, out = self.run_cells(table, argv=("stockholm-norr", "85_60"))
        self.assertEqual(code, 1, out)
        self.assertEqual(len(releases(calls)), 5)
        self.assertIn("5 flat cells in a row", out)

    # ---- the picking itself

    def test_next_prefers_a_cell_beside_something_drawn(self):
        module = load()
        free = [(10, 10), (5, 6)]
        self.assertEqual(module.next_cell(free, {(11, 11)}, (5, 5), set()), (10, 10))
        self.assertEqual(module.next_cell(free, set(), (5, 5), set()), (5, 6))
        self.assertIsNone(module.next_cell(free, set(), (5, 5), {(10, 10), (5, 6)}))

    def test_ties_go_to_the_row_above_then_the_left(self):
        module = load()
        drawn = {(5, 5)}
        self.assertEqual(module.next_cell([(6, 5), (4, 5), (5, 6), (5, 4)], drawn, (5, 5), set()), (5, 4))
        self.assertEqual(module.next_cell([(6, 5), (4, 5)], drawn, (5, 5), set()), (4, 5))

    def test_a_sheet_of_the_tiling_counts_every_cell_without_a_row_as_free(self):
        module = load()
        module.rutor = Fake({}, grid={"sheet": {"cols": 3, "rows": 2, "made": "grid"}, "cells": [[0, 0, "vantar", "x", ""], [1, 0, "fri", None, ""]]})
        free, drawn, _body = module.read_grid("se-n1-e1")
        self.assertEqual(sorted(free), [(0, 1), (1, 0), (1, 1), (2, 0), (2, 1)])
        self.assertEqual(drawn, {(0, 0)})

    def test_until_is_the_next_time_the_clock_shows_it(self):
        module = load()
        when = module.parse_until("07:00")
        now = datetime.now(timezone.utc)
        self.assertTrue(now < when <= now + timedelta(days=1))
        with self.assertRaises(Exception):
            module.parse_until("25:00")


if __name__ == "__main__":
    unittest.main()
