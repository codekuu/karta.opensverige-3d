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

    def __init__(self, table, status="locks: 0", grid=None):
        self.table = {key: list(value) for key, value in table.items()}
        self.status = status
        self.grid = grid if grid is not None else hand_grid(set())
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
    def run_cells(self, table, argv=("stockholm-norr", "85_65", "--count", "1"), status="locks: 0", grid=None, nap=True, marked=None):
        module = load()
        fake = Fake(table, status, grid)
        module.rutor = fake
        module.nap = lambda seconds: nap
        module.time.sleep = lambda seconds: None
        module.NETWORK_PAUSE_S = 0
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        module.ROOT = Path(tmp.name)
        self.mark = Path(tmp.name) / "work" / argv[0] / module.CLAIM_FILE
        if marked:
            self.mark.parent.mkdir(parents=True)
            self.mark.write_text(marked + "\n")
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
        self.assertIn("7 cells built, as asked", out)

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

    def test_a_lock_on_another_sheet_stops_before_anything(self):
        code, calls, out = self.run_cells({}, status="lock: goteborg-norr 12_3 expires x")
        self.assertEqual(code, 1)
        self.assertFalse([c for c in calls if c.split()[0] in ("claim", "render", "release")])
        self.assertIn("you hold goteborg-norr 12_3", out)


    # ---- one machine at a time

    def test_a_lock_no_run_here_claimed_is_left_to_the_machine_that_holds_it(self):
        code, calls, out = self.run_cells({}, status="lock: stockholm-norr 85_65 expires x")
        self.assertEqual(code, 1)
        self.assertFalse([c for c in calls if c.split()[0] in ("claim", "render", "release", "upload")])
        self.assertIn("Another machine may be building it", out)
        code, calls, out = self.run_cells({}, status="lock: stockholm-norr 85_65 expires x", marked="12_3")
        self.assertEqual(code, 1)
        self.assertFalse([c for c in calls if c.split()[0] in ("claim", "render", "release", "upload")])

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
            argv=("stockholm-norr", "85_65", "--count", "2"),
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(claims(calls), ["85_65", "85_66", "85_66", "85_66"])
        self.assertFalse(releases(calls))
        self.assertIn("asking again in 30 min", out)

    def test_until_ends_the_wait_for_review(self):
        code, calls, out = self.run_cells({("claim", "85_66"): [(2, "refused: too_many_waiting: x")]}, argv=("stockholm-norr", "85_65"), nap=False)
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
        free, drawn = module.read_grid("se-n1-e1")
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
