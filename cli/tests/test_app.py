"""The app page: building a cell from a page on this machine, without a terminal."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from rutor import app, config, login, pipeline, se3_price
from rutor.errors import RutorError, network
from rutor.renderer import google

# Shaped like a Cesium ion token, which has two dots. It opens nothing.
CESIUM = "a.b.c"


class _OwnHome(unittest.TestCase):
    """A home folder of the test's own, and no Cesium token in the environment."""

    def setUp(self) -> None:
        self._saved = {name: os.environ.get(name) for name in ("PIXELBYGGET_HOME", "CESIUM_ION_TOKEN")}
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PIXELBYGGET_HOME"] = self.tmp.name
        os.environ.pop("CESIUM_ION_TOKEN", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tmp.cleanup()


class CesiumTokenTests(_OwnHome):
    def test_a_written_token_is_read_back(self) -> None:
        self.assertIsNone(config.read_cesium_token())
        config.write_cesium_token(CESIUM)
        self.assertEqual(config.cesium_token_path(), Path(self.tmp.name) / "cesium-token")
        self.assertEqual(config.read_cesium_token(), CESIUM)
        # A pasted token often ends in a line break. It is not part of the token.
        config.write_cesium_token(f"  {CESIUM}\n")
        self.assertEqual(config.read_cesium_token(), CESIUM)
        self.assertEqual(config.cesium_token_path().read_bytes(), CESIUM.encode())

    @unittest.skipIf(os.name == "nt", "Windows does not report POSIX file modes")
    def test_the_token_file_is_the_owners_alone(self) -> None:
        config.write_cesium_token(CESIUM)
        self.assertEqual(stat.S_IMODE(config.cesium_token_path().stat().st_mode), 0o600)
        # A file that was there already, and readable by others, is closed too.
        os.chmod(config.cesium_token_path(), 0o644)
        config.write_cesium_token(CESIUM)
        self.assertEqual(stat.S_IMODE(config.cesium_token_path().stat().st_mode), 0o600)

    def test_the_stored_token_is_handed_on_only_when_none_is_set(self) -> None:
        self.assertEqual(config.cesium_env(), {})
        config.write_cesium_token(CESIUM)
        self.assertEqual(config.cesium_env(), {"CESIUM_ION_TOKEN": CESIUM})
        # The person's own variable wins: the child inherits it, and nothing is added.
        os.environ["CESIUM_ION_TOKEN"] = "x.y.z"
        self.assertEqual(config.cesium_env(), {})
        # A variable that is set to nothing is not a token.
        os.environ["CESIUM_ION_TOKEN"] = "  "
        self.assertEqual(config.cesium_env(), {"CESIUM_ION_TOKEN": CESIUM})

    def test_handing_the_token_on_does_not_set_it_here(self) -> None:
        config.write_cesium_token(CESIUM)
        config.cesium_env()
        self.assertNotIn("CESIUM_ION_TOKEN", os.environ)

    def test_an_empty_token_is_not_written(self) -> None:
        for empty in ("", "   ", "\n\t"):
            with self.assertRaises(RutorError) as caught:
                config.write_cesium_token(empty)
            self.assertEqual(caught.exception.code, 1)
        self.assertFalse(config.cesium_token_path().exists())
        # And one that is there stays as it was.
        config.write_cesium_token(CESIUM)
        with self.assertRaises(RutorError):
            config.write_cesium_token(" ")
        self.assertEqual(config.read_cesium_token(), CESIUM)

    def test_the_variable_is_the_one_the_renderer_reads(self) -> None:
        self.assertEqual(config.CESIUM_TOKEN_ENV, google.TOKEN_ENV)


class ChecksTests(_OwnHome):
    def _machine(
        self,
        cards: list[tuple[str, int]],
        *,
        free: float = 257.4,
        cached: bool = False,
        platform: str = "linux",
    ) -> dict[str, object]:
        with (
            patch("rutor.app._platform", return_value=platform),
            patch("rutor.commands._nvidia_cards", return_value=cards),
            patch("rutor.commands._free_gb", return_value=free),
            patch("rutor.commands._model_is_cached", return_value=cached),
        ):
            return app.machine()

    def test_a_mac_is_a_no_before_anything_else(self) -> None:
        with (
            patch("rutor.app._platform", return_value="darwin"),
            patch("rutor.commands._nvidia_cards", side_effect=AssertionError("the driver was asked")),
            patch("rutor.commands._free_gb", return_value=257.4),
            patch("rutor.commands._model_is_cached", return_value=False),
        ):
            found = app.machine()
        self.assertFalse(found["ok"])
        self.assertEqual(found["problem"], "mac")
        self.assertEqual(found["card"], "")
        self.assertEqual(found["card_gb"], 0)
        self.assertEqual(found["free_gb"], 257)
        self.assertEqual(found["needed_gb"], 25)

    def test_no_card_is_a_no(self) -> None:
        found = self._machine([], platform="win32")
        self.assertFalse(found["ok"])
        self.assertEqual(found["problem"], "no_card")
        self.assertEqual(found["card"], "")
        self.assertEqual(found["card_gb"], 0)

    def test_a_six_gb_card_is_named_with_its_size(self) -> None:
        found = self._machine([("GeForce GTX 1060", 6144)])
        self.assertFalse(found["ok"])
        self.assertEqual(found["problem"], "small_card")
        self.assertEqual(found["card"], "GeForce GTX 1060")
        self.assertEqual(found["card_gb"], 6)

    def test_a_twelve_gb_card_with_room_is_a_yes(self) -> None:
        found = self._machine([("GeForce RTX 5070", 12227)], free=257.4, cached=False)
        self.assertTrue(found["ok"])
        self.assertEqual(found["problem"], "")
        self.assertEqual(found["card_gb"], 12)
        self.assertEqual(found["free_gb"], 257)
        self.assertEqual(found["needed_gb"], 25)

    def test_the_largest_card_is_the_one_that_counts(self) -> None:
        found = self._machine([("GeForce GT 710", 2048), ("GeForce RTX 5070", 12227)])
        self.assertTrue(found["ok"])
        self.assertEqual(found["card"], "GeForce RTX 5070")

    def test_too_little_disk_is_a_no(self) -> None:
        tight = self._machine([("GeForce RTX 5070", 12227)], free=9.0, cached=False)
        self.assertEqual(tight["problem"], "disk")
        self.assertEqual(tight["needed_gb"], 25)
        self.assertFalse(tight["ok"])
        cached = self._machine([("GeForce RTX 5070", 12227)], free=9.0, cached=True)
        self.assertTrue(cached["ok"])
        self.assertEqual(cached["problem"], "")
        self.assertEqual(cached["needed_gb"], 2)

    def test_a_card_name_from_the_driver_is_one_short_line(self) -> None:
        found = self._machine([("<b>x</b>\n" * 50, 12227)])
        card = found["card"]
        self.assertIsInstance(card, str)
        self.assertLessEqual(len(str(card)), 60)
        self.assertNotIn("\n", str(card))

    def test_memory_is_a_number_or_nothing(self) -> None:
        ram = app.machine()["ram_gb"]
        if ram is None:
            return
        self.assertIsInstance(ram, int)
        self.assertNotIsInstance(ram, bool)
        self.assertGreater(ram, 0)

    def test_missing_tools_are_named(self) -> None:
        with (
            patch("rutor.commands._torch_missing", return_value="no"),
            patch("rutor.commands._bitsandbytes_missing", return_value=None),
            patch("rutor.commands._render_missing", return_value=None),
            patch("rutor.commands._model_is_cached", return_value=False),
            patch("rutor.renderer.google.node_missing", return_value="no"),
            patch("rutor.renderer.google.renderer_missing", return_value=None),
        ):
            found = app.tools("google")
        self.assertEqual(found, {"ready": False, "model_cached": False, "missing": ["torch", "node"]})

    def test_an_open_data_sheet_needs_no_node(self) -> None:
        with (
            patch("rutor.renderer.google.node_missing", side_effect=AssertionError("node was asked")),
            patch("rutor.renderer.google.renderer_missing", side_effect=AssertionError("renderer was asked")),
            patch("rutor.commands._torch_missing", return_value=None),
            patch("rutor.commands._bitsandbytes_missing", return_value=None),
            patch("rutor.commands._render_missing", return_value=None),
            patch("rutor.commands._model_is_cached", return_value=True),
        ):
            found = app.tools("open")
        self.assertTrue(found["ready"])
        self.assertEqual(found["missing"], [])
        self.assertTrue(found["model_cached"])

    def test_cesium_answers_are_words_the_page_knows(self) -> None:
        def at(status: int, code: str = "") -> Mock:
            return Mock(return_value=(status, code))

        self.assertEqual(app.cesium("a.b.c", ask=at(200)), {"set": True, "ok": True, "problem": ""})
        self.assertEqual(
            app.cesium("a.b.c", ask=at(401, "INVALID_TOKEN")),
            {"set": True, "ok": False, "problem": "refused"},
        )
        self.assertEqual(
            app.cesium("a.b.ca.b.c", ask=at(401, "INVALID_TOKEN")),
            {"set": True, "ok": False, "problem": "twice"},
        )
        self.assertEqual(app.cesium("a.b.c", ask=at(404)), {"set": True, "ok": False, "problem": "no_access"})
        self.assertEqual(app.cesium("a.b.c", ask=at(403)), {"set": True, "ok": False, "problem": "no_access"})
        self.assertEqual(app.cesium("a.b.c", ask=at(500)), {"set": True, "ok": False, "problem": "unreachable"})

        down = Mock(side_effect=network("x"))
        self.assertEqual(app.cesium("a.b.c", ask=down), {"set": True, "ok": False, "problem": "unreachable"})

        broken = Mock(return_value=(200, ""))
        self.assertEqual(app.cesium("a.b c", ask=broken), {"set": True, "ok": False, "problem": "broken"})
        broken.assert_not_called()

        absent = Mock(return_value=(200, ""))
        self.assertEqual(app.cesium(ask=absent), {"set": False, "ok": False, "problem": "missing"})
        absent.assert_not_called()

    def test_cesium_judges_the_stored_token_when_none_is_given(self) -> None:
        config.write_cesium_token("a.b.c")
        asked: list[str] = []

        def ask(token: str) -> tuple[int, str]:
            asked.append(token)
            return 200, ""

        found = app.cesium(ask=ask)
        self.assertEqual(asked, ["a.b.c"])
        self.assertEqual(found, {"set": True, "ok": True, "problem": ""})
        os.environ["CESIUM_ION_TOKEN"] = "x.y.z"
        asked.clear()
        app.cesium(ask=ask)
        self.assertEqual(asked, ["x.y.z"])

    def test_the_token_is_in_no_answer(self) -> None:
        secret = "a.b.c-secret"

        def shows(candidate: str, ask: Mock, problem: str) -> None:
            result = app.cesium(candidate, ask=ask)
            self.assertEqual(result["problem"], problem)
            self.assertNotIn(secret, json.dumps(result))

        shows(secret, Mock(return_value=(200, "")), "")
        shows(secret, Mock(return_value=(401, "INVALID_TOKEN")), "refused")
        shows(f"{secret}.{secret}", Mock(return_value=(401, "INVALID_TOKEN")), "twice")
        shows(secret, Mock(return_value=(404, "")), "no_access")
        shows(secret, Mock(return_value=(403, "")), "no_access")
        shows(secret, Mock(return_value=(500, "")), "unreachable")
        shows(secret, Mock(side_effect=network(secret)), "unreachable")
        quiet = Mock(side_effect=AssertionError("asked"))
        shows(f"{secret} {secret}", quiet, "broken")
        quiet.assert_not_called()

    def test_the_terms_are_the_ten_lines(self) -> None:
        lines = app.terms()
        self.assertEqual(len(lines), 10)
        self.assertTrue(lines[0].startswith("1. Building a cell is voluntary"))
        self.assertTrue(lines[9].startswith("10. "))
        self.assertEqual("\n".join(lines), login.TERMS)

    def test_a_price_that_cannot_be_read_is_unknown(self) -> None:
        def fetch(day: date) -> list[se3_price.Slot] | None:
            del day
            raise OSError("down")

        self.assertEqual(app.price(fetch=fetch), {"decision": "unknown", "after": ""})

    def test_a_high_price_says_when_it_is_low(self) -> None:
        now = datetime(2026, 11, 21, 12, 0, tzinfo=se3_price.STOCKHOLM)
        tomorrow = now.date() + timedelta(days=1)

        def quarters(day: date, sek_at: Callable[[datetime], float]) -> list[se3_price.Slot]:
            start = datetime(day.year, day.month, day.day, tzinfo=se3_price.STOCKHOLM)
            slots: list[se3_price.Slot] = []
            for quarter in range(24 * 4):
                begin = start + timedelta(minutes=15 * quarter)
                slots.append(se3_price.Slot(begin, begin + timedelta(minutes=15), sek_at(begin)))
            return slots

        def fetch_high(day: date) -> list[se3_price.Slot]:
            def sek_at(begin: datetime) -> float:
                if day == tomorrow and begin.hour == 3:
                    return 0.02
                return 1.20

            return quarters(day, sek_at)

        waited = app.price(fetch=fetch_high, clock=lambda: now)
        cheap = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 3, tzinfo=se3_price.STOCKHOLM)
        self.assertEqual(waited, {"decision": "WAIT", "after": cheap.isoformat()})

        def fetch_flat(day: date) -> list[se3_price.Slot]:
            return quarters(day, lambda _begin: 0.40)

        self.assertEqual(app.price(fetch=fetch_flat, clock=lambda: now), {"decision": "RUN", "after": ""})

    def test_an_old_pipeline_is_not_current(self) -> None:
        old = app.version({"pipeline": "2026.12-a"})
        self.assertFalse(old["current"])
        self.assertEqual(old["wanted"], "2026.12-a")
        self.assertEqual(old["mine"], pipeline.PIPELINE)
        self.assertTrue(app.version({"pipeline": pipeline.PIPELINE})["current"])
        blank = app.version({})
        self.assertTrue(blank["current"])
        self.assertEqual(blank["wanted"], "")


if __name__ == "__main__":
    unittest.main()
