"""The app page: building a cell from a page on this machine, without a terminal."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Iterator, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from rutor import app, app_jobs, config, login, pipeline, se3_price
from rutor.errors import RutorError, network
from rutor.localpage import Refusal
from rutor.renderer import google
from tests.fake_service import FakeService

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


class FakeRun:
    """A child that says what the test scripted, and ends when the test lets it."""

    def __init__(self, lines: list[str], code: int = 0, hold: bool = False) -> None:
        self._first = list(lines)
        self._later: list[str] = []
        self._code = code
        self._hold = hold
        self._lock = threading.Lock()
        self._released = threading.Event()
        self._ended = threading.Event()
        self.stopped = False

    def lines(self) -> Iterator[app_jobs.Line]:
        for text in self._first:
            yield app_jobs.split(text)
        if self._hold:
            # The page has to show what it has before the child says the rest, so the
            # test holds the child here until it has looked.
            while not self._released.is_set() and not self.stopped:
                time.sleep(0.01)
        with self._lock:
            later = list(self._later)
            self._later.clear()
        for text in later:
            yield app_jobs.split(text)
        self._ended.set()

    def say(self, *texts: str) -> None:
        """More lines, read after release(), as a child that prints its last word at the end."""
        with self._lock:
            self._later.extend(texts)

    def release(self) -> None:
        self._released.set()

    def wait(self) -> int:
        # A stop ends the child wherever it is, so wait() must not hang on a reader.
        self._ended.wait(timeout=30.0)
        return -15 if self.stopped else self._code

    def stop(self) -> None:
        self.stopped = True
        self._released.set()
        self._ended.set()


class FakeStart:
    """Stands in for app_jobs.start_rutor: records every start and plays a script per command."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.runs: list[FakeRun] = []
        self._scripts: dict[str, list[tuple[list[str], int, bool]]] = {}

    def script(self, command: str, lines: list[str], code: int = 0, hold: bool = False) -> None:
        self._scripts.setdefault(command, []).append((list(lines), code, hold))

    def __call__(self, argv: list[str], extra_env: Mapping[str, str]) -> FakeRun:
        self.calls.append((list(argv), dict(extra_env)))
        queued = self._scripts.get(argv[0]) if argv else None
        if queued:
            lines, code, hold = queued.pop(0)
        else:
            # No script left means a child with nothing to say that ends at once.
            lines, code, hold = [], 0, False
        run = FakeRun(lines, code=code, hold=hold)
        self.runs.append(run)
        return run


def wait_until(what: Callable[[], bool], seconds: float = 5.0) -> None:
    """Wait for the page's own thread to get there. Nothing in these tests sleeps a fixed time."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if what():
            return
        time.sleep(0.01)
    if not what():
        raise AssertionError("the page did not get there in time")


class LoginTests(_OwnHome):
    def _set_env(self, name: str, value: str) -> None:
        saved = os.environ.get(name)
        os.environ[name] = value

        def restore() -> None:
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved

        self.addCleanup(restore)

    def _builder(self, start: FakeStart | None = None) -> app.Builder:
        builder = app.Builder(start if start is not None else FakeStart())
        self.addCleanup(builder.shutdown)
        return builder

    def _me_calls(self, service: FakeService) -> int:
        return len([request for request in service.requests if request["path"] == "/v1/me"])

    def test_no_login_starts_before_the_terms_are_accepted(self) -> None:
        fake = FakeStart()
        builder = self._builder(fake)
        with self.assertRaises(Refusal) as caught:
            builder.start_login()
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "terms")
        self.assertEqual(fake.calls, [])
        self.assertFalse(builder.terms_accepted())

    def test_the_code_and_address_are_shown_while_it_waits(self) -> None:
        fake = FakeStart()
        fake.script(
            "login",
            [
                "1. Building a cell is voluntary and unpaid. Nobody owes you anything for it, and you can stop at any time.",
                "code: ABCD-1234",
                f"url: {login.DEVICE_URL}",
                "waiting: a person opens the url and types the code.",
            ],
            hold=True,
        )
        builder = self._builder(fake)
        builder.accept_terms()
        builder.start_login()
        wait_until(lambda: builder.login_state()["url"] != "")
        state = builder.login_state()
        self.assertTrue(state["waiting"])
        self.assertEqual(state["code"], "ABCD-1234")
        self.assertEqual(state["url"], login.DEVICE_URL)
        self.assertEqual(state["login"], "")
        self.assertEqual(fake.calls, [(["login", "--accept-terms"], {})])

    def test_a_finished_login_names_the_person(self) -> None:
        fake = FakeStart()
        fake.script(
            "login",
            ["code: ABCD-1234", f"url: {login.DEVICE_URL}"],
            hold=True,
        )
        builder = self._builder(fake)
        builder.accept_terms()
        builder.start_login()
        run = fake.runs[0]
        wait_until(lambda: builder.login_state()["url"] != "")
        run.say("logged in: klang")
        run.release()
        wait_until(lambda: not builder.login_state()["waiting"])
        self.assertEqual(
            builder.login_state(),
            {"login": "klang", "code": "", "url": "", "waiting": False, "problem": ""},
        )
        self.assertTrue(builder.terms_accepted())

    def test_a_code_nobody_approved_is_said_so(self) -> None:
        # 4 is the CLI's network code; anything else that is not a login is an expired code.
        for code, problem in ((4, "network"), (3, "expired"), (2, "expired")):
            with self.subTest(code=code):
                fake = FakeStart()
                fake.script("login", ["code: ABCD-1234", f"url: {login.DEVICE_URL}"], code=code)
                builder = self._builder(fake)
                builder.accept_terms()
                builder.start_login()
                wait_until(lambda builder=builder: not builder.login_state()["waiting"])
                state = builder.login_state()
                self.assertEqual(state["problem"], problem)
                self.assertFalse(state["waiting"])
                self.assertEqual(state["code"], "")
                self.assertEqual(state["url"], "")
                self.assertEqual(state["login"], "")

    def test_a_second_press_starts_no_second_login(self) -> None:
        fake = FakeStart()
        fake.script("login", ["code: ABCD-1234"], hold=True)
        builder = self._builder(fake)
        builder.accept_terms()
        builder.start_login()
        builder.start_login()
        self.assertEqual(len(fake.calls), 1)

    def test_a_new_login_can_start_after_one_ended(self) -> None:
        fake = FakeStart()
        fake.script("login", [], code=3)
        builder = self._builder(fake)
        builder.accept_terms()
        builder.start_login()
        wait_until(lambda: not builder.login_state()["waiting"])
        self.assertEqual(builder.login_state()["problem"], "expired")
        fake.script("login", [], hold=True)
        builder.start_login()
        self.assertEqual(len(fake.calls), 2)
        state = builder.login_state()
        self.assertTrue(state["waiting"])
        self.assertEqual(state["problem"], "")
        self.assertEqual(state["code"], "")

    def test_the_address_is_githubs_own_or_nothing(self) -> None:
        for wrong in ("https://evil.test/x", f"{login.DEVICE_URL}/x"):
            with self.subTest(wrong=wrong):
                fake = FakeStart()
                # The code comes second, so a visible code means the address was judged.
                fake.script("login", [f"url: {wrong}", "code: ABCD-1234"], hold=True)
                builder = self._builder(fake)
                builder.accept_terms()
                builder.start_login()
                wait_until(lambda builder=builder: builder.login_state()["code"] != "")
                self.assertEqual(builder.login_state()["url"], "")

    def test_a_code_or_name_of_another_shape_is_not_shown(self) -> None:
        fake = FakeStart()
        fake.script("login", ["code: <b>x</b>", "logged in: <script>"])
        builder = self._builder(fake)
        builder.accept_terms()
        builder.start_login()
        wait_until(lambda: not builder.login_state()["waiting"])
        state = builder.login_state()
        self.assertEqual(state["code"], "")
        self.assertEqual(state["login"], "")
        self.assertEqual(state["problem"], "expired")

    def test_a_stored_login_is_named_at_start(self) -> None:
        service = FakeService()
        self.addCleanup(service.stop)
        self._set_env("PIXELBYGGET_URL", service.start())
        config.write_token("test-token")
        builder = self._builder()
        self.assertEqual(builder.login_state()["login"], "klang")
        self.assertTrue(builder.terms_accepted())
        self.assertEqual(self._me_calls(service), 1)
        # The stored login is asked about once. Every later look is the page's own state.
        builder.login_state()
        self.assertEqual(self._me_calls(service), 1)

    def test_a_stored_login_the_service_refuses_is_nobody(self) -> None:
        service = FakeService()
        self.addCleanup(service.stop)
        self._set_env("PIXELBYGGET_URL", service.start())
        config.write_token("test-token")
        service.fail_next_with(401, b'{"error":"bad_token","message":"no"}', "application/json")
        builder = self._builder()
        self.assertEqual(
            builder.login_state(),
            {"login": "", "code": "", "url": "", "waiting": False, "problem": ""},
        )
        self.assertFalse(builder.terms_accepted())

    def test_the_login_is_in_no_answer(self) -> None:
        service = FakeService()
        self.addCleanup(service.stop)
        self._set_env("PIXELBYGGET_URL", service.start())
        config.write_token("test-token")
        fake = FakeStart()
        fake.script("login", ["code: ABCD-1234", f"url: {login.DEVICE_URL}"], hold=True)
        builder = self._builder(fake)
        builder.login_state()
        builder.accept_terms()
        builder.start_login()
        run = fake.runs[0]
        wait_until(lambda: builder.login_state()["url"] != "")
        run.say("logged in: klang")
        run.release()
        wait_until(lambda: not builder.login_state()["waiting"])
        self.assertNotIn("test-token", json.dumps(builder.login_state()))
        self.assertNotIn("test-token", repr(vars(builder)))

    def test_shutdown_stops_a_waiting_login(self) -> None:
        fake = FakeStart()
        fake.script("login", ["code: ABCD-1234", f"url: {login.DEVICE_URL}"], hold=True)
        builder = self._builder(fake)
        builder.accept_terms()
        builder.start_login()
        run = fake.runs[0]
        wait_until(lambda: builder.login_state()["url"] != "")
        builder.shutdown()
        self.assertTrue(run.stopped)
        self.assertFalse(builder.login_state()["waiting"])

    def test_a_login_that_has_ended_is_never_stopped(self) -> None:
        # Stopping signals the child's whole process group, and the number of a group that
        # has ended may belong to somebody else's program by now.
        fake = FakeStart()
        fake.script("login", ["logged in: klang"])
        builder = self._builder(fake)
        builder.accept_terms()
        builder.start_login()
        wait_until(lambda: builder.login_state()["login"] == "klang" and not builder.login_state()["waiting"])
        builder.shutdown()
        self.assertFalse(fake.runs[0].stopped)

    def test_a_stored_login_counts_as_terms_accepted_at_once(self) -> None:
        service = FakeService()
        self.addCleanup(service.stop)
        self._set_env("PIXELBYGGET_URL", service.start())
        config.write_token("test-token")
        # Asked before anything else: the page draws the terms step from this.
        self.assertTrue(self._builder().terms_accepted())

    def test_no_login_starts_once_the_page_is_closing(self) -> None:
        fake = FakeStart()
        builder = self._builder(fake)
        builder.accept_terms()
        builder.shutdown()
        builder.start_login()
        self.assertEqual(fake.calls, [])
        self.assertFalse(builder.login_state()["waiting"])

    def test_a_login_that_starts_while_the_page_closes_is_stopped(self) -> None:
        builder: app.Builder

        class ClosesMeanwhile(FakeStart):
            def __call__(self, argv: list[str], extra_env: Mapping[str, str]) -> FakeRun:
                run = super().__call__(argv, extra_env)
                # The page closes between the press and the child being up.
                builder.shutdown()
                return run

        fake = ClosesMeanwhile()
        fake.script("login", ["code: ABCD-1234"], hold=True)
        builder = self._builder(fake)
        builder.accept_terms()
        builder.start_login()
        self.assertTrue(fake.runs[0].stopped)
        wait_until(lambda: not builder.login_state()["waiting"])

    def test_a_fault_while_reading_the_login_does_not_leave_it_waiting(self) -> None:
        class Breaks(FakeRun):
            def lines(self) -> Iterator[app_jobs.Line]:
                yield app_jobs.split("code: ABCD-1234")
                raise RuntimeError("secret-1234")

        class StartsBroken(FakeStart):
            def __call__(self, argv: list[str], extra_env: Mapping[str, str]) -> FakeRun:
                self.calls.append((list(argv), dict(extra_env)))
                run = Breaks([])
                run.stop()
                self.runs.append(run)
                return run

        seen = io.StringIO()
        builder = self._builder(StartsBroken())
        builder.accept_terms()
        with contextlib.redirect_stderr(seen):
            builder.start_login()
            wait_until(lambda: not builder.login_state()["waiting"])
        state = builder.login_state()
        self.assertEqual((state["problem"], state["code"]), ("expired", ""))
        # One line for the terminal: the kind of fault, never its message.
        self.assertEqual(seen.getvalue(), "app: RuntimeError while reading the login\n")


if __name__ == "__main__":
    unittest.main()
