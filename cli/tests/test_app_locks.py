"""A lock is never left with nothing running, and a failure says what it was.

What two reviews of the app page found, each as the run that went wrong.
"""

from __future__ import annotations

import ssl
import unittest
from unittest.mock import patch

from rutor import api, app, config
from rutor.errors import RutorError, network, refused
from rutor.localpage import Refusal
from tests import test_app as base

SHEET = "stockholm-norr"
CELL = "74_20"
CLAIMED = f"claimed: {SHEET} {CELL} expires 2026-11-25T12:00"


class _Jobs(base._OwnHome):
    def _builder(self) -> tuple[app.Builder, base.FakeStart]:
        fake = base.FakeStart()
        for name, answer in (
            ("rutor.app.machine", {"ok": True, "problem": ""}),
            ("rutor.app.tools", {"ready": True, "missing": []}),
            ("rutor.app.cesium", {"set": True, "ok": True, "problem": ""}),
        ):
            patcher = patch(name, return_value=answer)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = 0.0
        builder = app.Builder(fake, clock=lambda: self.now)
        self.addCleanup(builder.shutdown)
        fake.script("login", ["logged in: klang"])
        builder.accept_terms()
        builder.start_login()
        base.wait_until(lambda: builder.login_state()["login"] == "klang")
        return builder, fake

    def _ended(self, builder: app.Builder) -> dict[str, object]:
        base.wait_until(lambda: builder.job_state()["phase"] in ("done", "failed", "stopped") and not builder.busy())
        return builder.job_state()

    def _argvs(self, fake: base.FakeStart) -> list[list[str]]:
        return [argv for argv, _env in fake.calls][1:]


class UploadWhoseAnswerWasLostTests(_Jobs):
    def test_a_cell_the_service_took_in_is_done_and_holds_no_lock(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", [CLAIMED])
        fake.script("render", [f"rendered: work/{SHEET}/{CELL}.src.png"])
        fake.script("redraw", ["check: ok"])
        # The picture reached the service, and the answer never reached the machine.
        fake.script("upload", ["error: could not reach the service (TimeoutError)"], code=4)
        fake.script("release", ["error: refused: already_uploaded: That cell is already uploaded and cannot be released."], code=2)
        builder.choose(SHEET, 74, 20)
        builder.build()
        state = self._ended(builder)
        self.assertEqual((state["phase"], state["problem"], state["held"]), ("done", "", False))
        self.assertEqual(self._argvs(fake).count(["release", SHEET, CELL]), 1)

    def test_the_next_cell_is_claimed_afterwards(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", [CLAIMED])
        fake.script("upload", [], code=4)
        fake.script("release", ["error: refused: already_uploaded: no"], code=2)
        builder.choose(SHEET, 74, 20)
        builder.build()
        self._ended(builder)
        fake.script("claim", [f"claimed: {SHEET} 75_20 expires 2026-11-25T12:00"])
        fake.script("upload", [f"uploaded: {SHEET} 75_20 state vantar"])
        builder.choose(SHEET, 75, 20)
        builder.build()
        state = self._ended(builder)
        self.assertEqual((state["phase"], state["held"]), ("done", False))
        self.assertIn(["claim", SHEET, "75_20"], self._argvs(fake))

    def test_a_lock_the_service_says_is_not_ours_is_forgotten(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", [CLAIMED])
        fake.script("render", [], code=3)
        fake.script("release", ["error: refused: not_yours: That cell is not yours."], code=2)
        builder.choose(SHEET, 74, 20)
        builder.build()
        state = self._ended(builder)
        self.assertEqual((state["phase"], state["held"]), ("failed", False))
        self.assertEqual(self._argvs(fake).count(["release", SHEET, CELL]), 1)

    def test_stop_lets_an_upload_end_by_itself(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", [CLAIMED])
        fake.script("upload", [], hold=True)
        builder.choose(SHEET, 74, 20)
        builder.build()
        base.wait_until(lambda: builder.job_state()["phase"] == "upload")
        upload = fake.runs[-1]
        upload.say(f"uploaded: {SHEET} {CELL} state vantar")
        stopper = base.threading.Thread(target=builder.stop)
        stopper.start()
        # The stop is asked for, and the child is left to say how the upload went.
        base.time.sleep(0.05)
        self.assertFalse(upload.stopped)
        upload.release()
        stopper.join(timeout=10)
        state = self._ended(builder)
        self.assertEqual((state["phase"], state["held"]), ("done", False))
        self.assertNotIn(["release", SHEET, CELL], self._argvs(fake))


class ClaimThatMayHaveBeenGivenTests(_Jobs):
    def test_a_lock_that_may_exist_is_kept_when_the_release_gets_no_answer(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", ["error: could not reach the service"], code=4)
        fake.script("release", ["error: could not reach the service"], code=4)
        builder.choose(SHEET, 74, 20)
        builder.build()
        state = self._ended(builder)
        self.assertEqual((state["phase"], state["problem"], state["held"]), ("failed", "network", True))
        # And the next job asks it back, by its own name, before it claims.
        fake.script("release", [f"released: {SHEET} {CELL}"])
        fake.script("claim", [f"claimed: {SHEET} 75_20 expires 2026-11-25T12:00"])
        fake.script("upload", [f"uploaded: {SHEET} 75_20 state vantar"])
        builder.choose(SHEET, 75, 20)
        builder.build()
        self.assertEqual(self._ended(builder)["phase"], "done")
        argvs = self._argvs(fake)
        self.assertLess(argvs.index(["release", SHEET, CELL], 2), argvs.index(["claim", SHEET, "75_20"]))

    def test_a_claim_that_crashed_is_not_taken_as_no_lock(self) -> None:
        builder, fake = self._builder()
        # A traceback ends a command with 1, which says nothing about what the service did.
        fake.script("claim", ["Traceback (most recent call last):"], code=1)
        fake.script("release", ["error: refused: not_yours: no"], code=2)
        builder.choose(SHEET, 74, 20)
        builder.build()
        state = self._ended(builder)
        self.assertEqual(state["held"], False)
        self.assertIn(["release", SHEET, CELL], self._argvs(fake))

    def test_a_claim_that_said_why_it_ended_asks_nothing_back(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", ["error: the cell must be col_row"], code=1)
        builder.choose(SHEET, 74, 20)
        builder.build()
        self._ended(builder)
        self.assertNotIn(["release", SHEET, CELL], self._argvs(fake))


class LockFromBeforeTests(_Jobs):
    def test_a_lock_left_by_an_app_that_died_is_found_and_given_back(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", ["error: refused: too_many_locks: You already hold as many cells as you may."], code=2)
        builder.choose(SHEET, 75, 20)
        with patch("rutor.app.my_locks", return_value=[(SHEET, CELL)]):
            builder.build()
            state = self._ended(builder)
        self.assertEqual((state["phase"], state["problem"], state["held"]), ("failed", "old_lock", True))
        # Försök igen: the old cell is released, and then this one is claimed.
        fake.script("release", [f"released: {SHEET} {CELL}"])
        fake.script("claim", [f"claimed: {SHEET} 75_20 expires 2026-11-25T12:00"])
        fake.script("upload", [f"uploaded: {SHEET} 75_20 state vantar"])
        builder.build()
        self.assertEqual(self._ended(builder)["phase"], "done")
        argvs = self._argvs(fake)
        self.assertLess(argvs.index(["release", SHEET, CELL]), argvs.index(["claim", SHEET, "75_20"], 1))

    def test_no_lock_to_be_found_is_said_as_before(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", ["error: refused: too_many_locks: no"], code=2)
        builder.choose(SHEET, 75, 20)
        with patch("rutor.app.my_locks", return_value=[]):
            builder.build()
            state = self._ended(builder)
        self.assertEqual((state["problem"], state["held"]), ("locks", False))

    def test_my_locks_reads_them_from_the_service(self) -> None:
        with (
            patch("rutor.config.read_token", return_value="t"),
            patch("rutor.api.get", return_value=(200, {"login": "klang", "locks": [{"sheet": SHEET, "col": 74, "row": 20}, {"sheet": "../x", "col": 1, "row": 1}, "junk"]})),
        ):
            self.assertEqual(app.my_locks(), [(SHEET, CELL)])
        with patch("rutor.config.read_token", return_value="t"), patch("rutor.api.get", side_effect=network("down")):
            self.assertEqual(app.my_locks(), [])


class WhatWentWrongTests(_Jobs):
    def _fails_with(self, command: str, lines: list[str], code: int) -> str:
        builder, fake = self._builder()
        for earlier in ("claim", "render", "redraw", "upload"):
            if earlier == command:
                break
            fake.script(earlier, [CLAIMED] if earlier == "claim" else [])
        fake.script(command, lines, code=code)
        builder.choose(SHEET, 74, 20)
        builder.build()
        return str(self._ended(builder)["problem"])

    def test_the_daily_limit_is_not_a_try_again(self) -> None:
        self.assertEqual(self._fails_with("upload", ["error: refused: daily_limit: New builders may upload 20 cells a day."], 2), "daily")

    def test_an_old_pipeline_is_named(self) -> None:
        self.assertEqual(self._fails_with("upload", ["error: refused: wrong_pipeline: This sheet is drawn with pipeline x."], 2), "old_app")

    def test_a_used_up_quota_is_not_the_network(self) -> None:
        self.assertEqual(self._fails_with("render", ["error: the Cesium ion quota is used up for now (429)"], 4), "quota")

    def test_a_token_refused_in_the_middle_is_named(self) -> None:
        self.assertEqual(self._fails_with("render", ["error: Cesium ion refused the token (401)"], 3), "token")

    def test_any_other_refusal_stays_a_refusal(self) -> None:
        self.assertEqual(self._fails_with("upload", ["error: refused: lock_lost: Your lock ended."], 2), "refused")


class StoredLoginTests(base._OwnHome):
    def test_a_service_that_did_not_answer_is_asked_again_later(self) -> None:
        config.write_token("test-token")
        self.now = 0.0
        builder = app.Builder(base.FakeStart(), clock=lambda: self.now)
        self.addCleanup(builder.shutdown)
        with patch("rutor.api.get", side_effect=network("down")) as asked:
            self.assertEqual(builder.login_state()["login"], "")
            # Not on every look: the page asks for its state every other second.
            builder.login_state()
            self.assertEqual(asked.call_count, 1)
        self.now += app.LOGIN_RETRY_SECONDS + 1
        with patch("rutor.api.get", return_value=(200, {"login": "klang", "locks": []})):
            self.assertEqual(builder.login_state()["login"], "klang")

    def test_a_refusal_is_an_answer_and_is_not_asked_again(self) -> None:
        config.write_token("test-token")
        self.now = 0.0
        builder = app.Builder(base.FakeStart(), clock=lambda: self.now)
        self.addCleanup(builder.shutdown)
        with patch("rutor.api.get", side_effect=refused("refused: bad_token: no")) as asked:
            builder.login_state()
            self.now += app.LOGIN_RETRY_SECONDS + 1
            builder.login_state()
            self.assertEqual(asked.call_count, 1)


class OldPipelineTests(unittest.TestCase):
    def test_no_cell_is_drawn_by_an_app_the_sheet_no_longer_takes(self) -> None:
        with self.assertRaises(Refusal) as caught:
            app.refuse_old_pipeline({"pipeline": "2099.01-z"})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "old_app"))
        # A sheet that names this copy's pipeline, or none, is drawn.
        app.refuse_old_pipeline({"pipeline": app.pipeline.PIPELINE})
        app.refuse_old_pipeline({})


class NetworkFaultTests(unittest.TestCase):
    def test_a_broken_connection_is_a_network_error_whatever_its_kind(self) -> None:
        for fault in (ssl.SSLError("bad record"), OSError("unreachable")):
            with self.subTest(fault=type(fault).__name__), patch("rutor.api._open", side_effect=fault):
                with self.assertRaises(RutorError) as caught:
                    api.get("/v1/me", token="t")
                self.assertEqual(caught.exception.code, 4)



class LoginWaitTests(unittest.TestCase):
    def _poll(self, answers: list[object]) -> str:
        from rutor import login

        left = list(answers)

        def http(url: str, fields: dict[str, str]) -> dict[str, object]:
            del url, fields
            answer = left.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            assert isinstance(answer, dict)
            return answer

        return login.poll_for_token("id", "device", 1, lambda _seconds: None, http=http)

    def test_a_blip_in_the_network_does_not_end_the_wait(self) -> None:
        answers = [network("down"), {"error": "authorization_pending"}, network("down"), {"access_token": "t"}]
        self.assertEqual(self._poll(answers), "t")

    def test_a_network_that_stays_down_ends_it_as_the_network(self) -> None:
        with self.assertRaises(RutorError) as caught:
            self._poll([network("down")] * 10)
        self.assertEqual(caught.exception.code, 4)

    def test_a_timeout_while_asking_github_is_the_network(self) -> None:
        from rutor import login

        with patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            with self.assertRaises(RutorError) as caught:
                login.github_http("https://github.com/login/device/code", {"client_id": "x"})
        self.assertEqual(caught.exception.code, 4)


class LoginDeniedTests(base._OwnHome):
    def test_a_login_the_person_said_no_to_is_not_called_too_slow(self) -> None:
        fake = base.FakeStart()
        fake.script("login", ["error: refused: access_denied: the GitHub login was denied"], code=2)
        builder = app.Builder(fake)
        self.addCleanup(builder.shutdown)
        builder.accept_terms()
        builder.start_login()
        base.wait_until(lambda: builder.login_state()["waiting"] is False)
        self.assertEqual(builder.login_state()["problem"], "denied")


class ServiceBusyTests(unittest.TestCase):
    def _answer(self, status: int, body: bytes) -> RutorError:
        with patch("rutor.api._exchange", return_value=(status, body)):
            with self.assertRaises(RutorError) as caught:
                api.get("/v1/me", token="t")
        return caught.exception

    def test_try_again_later_is_not_a_refusal(self) -> None:
        for status, body in (
            (429, b'{"error":"rate_limited","message":"Too many requests."}'),
            (500, b'{"error":"internal","message":"Something broke."}'),
            (503, b'{"error":"github_down","message":"GitHub did not answer."}'),
        ):
            with self.subTest(status=status):
                self.assertEqual(self._answer(status, body).code, 4)

    def test_a_limit_of_the_persons_own_is_a_refusal(self) -> None:
        said = self._answer(429, b'{"error":"daily_limit","message":"New builders may upload 20 cells a day."}')
        self.assertEqual(said.code, 2)
        self.assertTrue(said.message.startswith("refused: daily_limit"))


class ReportScrubTests(unittest.TestCase):
    def _scrub(self, text: str, home: str, secrets: list[str] | None = None) -> str:
        from pathlib import PureWindowsPath

        with (
            patch("rutor.app.Path.home", return_value=PureWindowsPath(home)),
            patch("rutor.app.getpass.getuser", return_value=PureWindowsPath(home).name),
            patch("rutor.app._secrets", return_value=secrets or []),
        ):
            return app._scrub(text)

    def test_a_short_windows_path_does_not_carry_the_name(self) -> None:
        said = self._scrub(r"cannot open C:\Users\GUSTAF~1\AppData\Local\Temp\x.png", r"C:\Users\Gustaf Garnow")
        self.assertNotIn("GUSTAF", said)

    def test_a_home_written_as_an_address_does_not_carry_the_name(self) -> None:
        said = self._scrub("at file:///C:/Users/Gustaf%20Garnow/AppData/x.js:3", r"C:\Users\Gustaf Garnow")
        self.assertNotIn("Gustaf", said)
        self.assertNotIn("Garnow", said)

    def test_a_token_cut_short_is_taken_out_too(self) -> None:
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "a" * 300
        said = self._scrub(f"token={token[:120]}… failed", r"C:\Users\ebba", [token])
        self.assertNotIn("eyJhbGciOiJIUzI1NiIs", said)


class DiskForAHalfDoneFetchTests(unittest.TestCase):
    def _machine(self, free: float, left: float | None) -> dict[str, object]:
        with (
            patch("rutor.app._platform", return_value="win32"),
            patch("rutor.commands._nvidia_cards", return_value=[("RTX 5070", 12282)]),
            patch("rutor.commands._nvidia_driver", return_value="616.56"),
            patch("rutor.commands._free_gb", return_value=free),
            patch("rutor.commands._model_is_cached", return_value=False),
            patch("rutor.app_install.left_to_fetch_gb", return_value=left),
        ):
            return app.machine()

    def test_what_is_already_fetched_is_not_asked_for_again(self) -> None:
        # 27 GB free at the start, the stack fetched and unpacked, 20 GB free now and 12 GB
        # left to fetch. The fixed 25 said "the disk is too full" and the fetch could not go on.
        found = self._machine(free=20.0, left=12.4)
        self.assertEqual((found["ok"], found["problem"], found["needed_gb"]), (True, "", 17))

    def test_too_little_for_what_is_left_is_still_a_no(self) -> None:
        found = self._machine(free=10.0, left=12.4)
        self.assertEqual((found["ok"], found["problem"]), (False, "disk"))

    def test_an_install_made_by_hand_keeps_its_rule(self) -> None:
        found = self._machine(free=20.0, left=None)
        self.assertEqual((found["ok"], found["problem"], found["needed_gb"]), (False, "disk", 25))


class SecondRoundTests(_Jobs):
    """What a second reader found in the first round's fixes."""

    def test_a_lost_answer_and_a_release_nobody_answered_is_still_a_cell_that_went_in(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", [CLAIMED])
        fake.script("upload", [], code=4)
        # The network is down for the release too, which is the likely case: it runs in the
        # same second as the upload that lost its answer.
        fake.script("release", [], code=4)
        fake.script("release", [], code=4)
        builder.choose(SHEET, 74, 20)
        builder.build()
        state = self._ended(builder)
        self.assertEqual((state["phase"], state["problem"], state["held"]), ("failed", "network", True))
        claims = self._argvs(fake).count(["claim", SHEET, CELL])
        # Försök igen. The service says what became of the cell, and it is not claimed again.
        fake.script("release", ["error: refused: already_uploaded: no"], code=2)
        builder.build()
        state = self._ended(builder)
        self.assertEqual((state["phase"], state["problem"], state["held"]), ("done", "", False))
        self.assertEqual(self._argvs(fake).count(["claim", SHEET, CELL]), claims)

    def test_the_page_is_not_told_failed_before_it_is_told_done(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", [CLAIMED])
        fake.script("upload", [], code=4)
        fake.script("release", [], code=2, hold=True)
        builder.choose(SHEET, 74, 20)
        builder.build()
        base.wait_until(lambda: ["release", SHEET, CELL] in self._argvs(fake))
        # While the service is asked what became of the cell, the job is still the upload.
        self.assertEqual(builder.job_state()["phase"], "upload")
        fake.runs[-1].say("error: refused: already_uploaded: no")
        fake.runs[-1].release()
        self.assertEqual(self._ended(builder)["phase"], "done")

    def test_a_stop_during_the_upload_says_that_the_upload_is_finishing(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", [CLAIMED])
        fake.script("upload", [], hold=True)
        builder.choose(SHEET, 74, 20)
        builder.build()
        base.wait_until(lambda: builder.job_state()["phase"] == "upload")
        self.assertIs(builder.job_state()["finishing"], False)
        stopper = base.threading.Thread(target=builder.stop)
        stopper.start()
        base.wait_until(lambda: builder.job_state()["finishing"] is True)
        fake.runs[-1].say(f"uploaded: {SHEET} {CELL} state vantar")
        fake.runs[-1].release()
        stopper.join(timeout=10)
        state = self._ended(builder)
        self.assertEqual((state["phase"], state["finishing"]), ("done", False))

    def test_a_lock_from_before_is_named_and_is_not_given_back_for_another_cell(self) -> None:
        builder, fake = self._builder()
        fake.script("claim", ["error: refused: too_many_locks: no"], code=2)
        builder.choose(SHEET, 75, 20)
        with patch("rutor.app.my_locks", return_value=[("goteborg-norr", "3_4")]):
            builder.build()
            state = self._ended(builder)
        self.assertEqual((state["problem"], state["old"]), ("old_lock", "goteborg-norr 3_4"))
        # It may be a cell the person is drawing in a terminal. Only pressing again, for
        # the cell that was refused, gives it back: choosing another cell forgets it.
        builder.choose(SHEET, 76, 20)
        self.assertEqual((builder.job_state()["held"], builder.job_state()["old"]), (False, ""))
        fake.script("claim", [f"claimed: {SHEET} 76_20 expires 2026-11-25T12:00"])
        fake.script("upload", [f"uploaded: {SHEET} 76_20 state vantar"])
        builder.build()
        self._ended(builder)
        self.assertNotIn(["release", "goteborg-norr", "3_4"], self._argvs(fake))

    def test_a_service_that_closed_is_a_no_and_not_the_network(self) -> None:
        with patch("rutor.api._exchange", return_value=(503, b'{"error":"closed","message":"Pixelbygget is closed for the moment."}')):
            with self.assertRaises(RutorError) as caught:
                api.get("/v1/me", token="t")
        self.assertEqual(caught.exception.code, 2)


class SecondRoundLoginTests(base._OwnHome):
    def test_a_slow_service_does_not_hold_the_page_for_long_or_often(self) -> None:
        config.write_token("test-token")
        self.now = 0.0
        builder = app.Builder(base.FakeStart(), clock=lambda: self.now)
        self.addCleanup(builder.shutdown)

        def slow(path: str, token: str | None = None, timeout: float | None = None) -> tuple[int, object]:
            del path, token
            # The page waits a few seconds for this, not the half minute a command may take.
            self.assertEqual(timeout, app.LOGIN_ASK_SECONDS)
            self.now += 40.0
            raise network("slow")

        with patch("rutor.api.get", side_effect=slow) as asked:
            builder.login_state()
            # The pause is counted from when the ask ended, so a slow service is not asked
            # again by the very next look.
            builder.login_state()
            builder.terms_accepted()
            self.assertEqual(asked.call_count, 1)


class SecondRoundDiskTests(DiskForAHalfDoneFetchTests):
    def test_a_finished_app_install_needs_no_more_than_one_made_by_hand(self) -> None:
        found = self._machine_cached(free=3.0, left=0.0)
        self.assertEqual((found["ok"], found["needed_gb"]), (True, 2))

    def _machine_cached(self, free: float, left: float | None) -> dict[str, object]:
        with (
            patch("rutor.app._platform", return_value="win32"),
            patch("rutor.commands._nvidia_cards", return_value=[("RTX 5070", 12282)]),
            patch("rutor.commands._nvidia_driver", return_value="616.56"),
            patch("rutor.commands._free_gb", return_value=free),
            patch("rutor.commands._model_is_cached", return_value=True),
            patch("rutor.app_install.left_to_fetch_gb", return_value=left),
        ):
            return app.machine()
