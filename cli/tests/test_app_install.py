"""The app install: the folder the installer makes, and what the page fetches into it."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rutor import app_jobs, config
from rutor.errors import RutorError
from rutor.renderer import google

# A user name with a space and with letters outside ASCII, as a Windows profile may have.
PROFILE = "Åsa Öberg"


class _AppFolder(unittest.TestCase):
    """A folder that stands in for the one the installer makes, under such a profile."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Resolved, as repo_root() is: a temp folder on a Mac is reached through a link.
        self.app = Path(self.tmp.name).resolve() / PROFILE / "AppData" / "Local" / "Pixelbygget"
        (self.app / "cli").mkdir(parents=True)
        root = patch("rutor.config.repo_root", return_value=self.app)
        root.start()
        self.addCleanup(root.stop)
        saved = os.environ.get("PATH")
        os.environ["PATH"] = os.pathsep.join(["/somewhere/bin", "/usr/bin"])
        self.addCleanup(self._restore_path, saved)

    @staticmethod
    def _restore_path(saved: str | None) -> None:
        if saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved

    def install(self, text: str = '{"version": "0.1.0", "lock_sha256": "' + "ab" * 32 + '"}') -> None:
        (self.app / "install.json").write_text(text, encoding="utf-8")

    def node(self) -> Path:
        folder = self.app / "node"
        folder.mkdir(exist_ok=True)
        (folder / "node.exe").write_bytes(b"")
        return folder


class AppHomeTests(_AppFolder):
    def test_a_hand_made_install_is_not_an_app_install(self) -> None:
        # A clone has no install.json, whatever else lies in it.
        self.node()
        self.assertIsNone(config.app_dir())
        self.assertIsNone(config.app_install())
        self.assertEqual(config.tool_env(), {})

    def test_the_installers_folder_is_found_by_its_file(self) -> None:
        self.install()
        self.assertEqual(config.app_dir(), self.app)
        self.assertEqual(config.app_install(), {"version": "0.1.0", "lock_sha256": "ab" * 32})

    def test_a_file_of_another_shape_is_no_install(self) -> None:
        for text in ("[]", '{"version": 3}', '{"version": "0.1.0"}', "not json", "", '{"version": "0.1.0", "lock_sha256": 7}'):
            self.install(text)
            self.assertIsNone(config.app_install(), text)
            self.assertIsNone(config.app_dir(), text)
            self.assertEqual(config.tool_env(), {}, text)

    def test_the_apps_own_node_comes_first_on_the_path(self) -> None:
        self.install()
        folder = self.node()
        found = config.tool_env()
        self.assertEqual(list(found), ["PATH"])
        self.assertEqual(found["PATH"].split(os.pathsep), [str(folder), "/somewhere/bin", "/usr/bin"])
        # Asking does not change this process's own path.
        self.assertEqual(os.environ["PATH"].split(os.pathsep), ["/somewhere/bin", "/usr/bin"])

    def test_a_path_that_already_starts_there_is_not_made_longer(self) -> None:
        self.install()
        folder = self.node()
        # A child of the page was started with tool_env(), and asks again.
        os.environ["PATH"] = config.tool_env()["PATH"]
        self.assertEqual(config.tool_env()["PATH"].split(os.pathsep), [str(folder), "/somewhere/bin", "/usr/bin"])

    def test_no_node_folder_leaves_the_path_alone(self) -> None:
        self.install()
        self.assertEqual(config.tool_env(), {})
        # A folder without node.exe in it is a download that never finished.
        (self.app / "node").mkdir()
        self.assertEqual(config.tool_env(), {})


class ToolEnvIsUsedTests(_AppFolder):
    """The three places that look for Node, or start what does, look in the app's folder first."""

    def setUp(self) -> None:
        super().setUp()
        self.install()
        self.folder = self.node()

    def test_the_node_check_looks_in_the_apps_folder(self) -> None:
        asked: list[tuple[list[str], str]] = []

        def run(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
            asked.append((argv, env["PATH"]))
            return 0, "v24.14.0\n", ""

        with patch("shutil.which", side_effect=lambda name, path=None: f"{(path or '').split(os.pathsep)[0]}/{name}"):
            self.assertIsNone(google.node_missing(run=run))
        self.assertEqual(asked[0][0], [f"{self.folder}/node", "--version"])
        self.assertEqual(asked[0][1].split(os.pathsep)[0], str(self.folder))

    def test_an_environment_handed_in_is_used_as_it_is(self) -> None:
        asked: list[str] = []

        def run(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
            asked.append(env["PATH"])
            return 0, "v24.14.0\n", ""

        self.assertIsNone(google.node_missing(which=lambda name: "/elsewhere/node", run=run, env={"PATH": "/elsewhere"}))
        self.assertEqual(asked, ["/elsewhere"])

    def test_the_render_starts_the_apps_node(self) -> None:
        started: list[tuple[list[str], str]] = []

        def run(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
            started.append((argv, env["PATH"]))
            if argv[1:] == ["--version"]:
                return 0, "v24.14.0\n", ""
            # The render itself is not this test's business: it ends as a failed render.
            return 3, "", "stopped here"

        with (
            patch("shutil.which", side_effect=lambda name, path=None: f"{(path or '').split(os.pathsep)[0]}/{name}"),
            patch.dict(os.environ, {"CESIUM_ION_TOKEN": "a.b.c"}),
            patch("rutor.renderer.google._require_installed"),
            patch("rutor.renderer.google.google_camera", return_value={}),
            self.assertRaises(RutorError),
        ):
            google.render_google({}, 1, 2, self.app / "work" / "x.src.png", run=run)
        self.assertEqual(started[-1][0][0], f"{self.folder}/node")
        self.assertEqual(started[-1][1].split(os.pathsep)[0], str(self.folder))

    def test_a_rutor_child_gets_the_apps_node_on_its_path(self) -> None:
        with patch("rutor.app_jobs.start_process", return_value="run") as start:
            self.assertEqual(app_jobs.start_rutor(["render", "s", "1_2"], {"CESIUM_ION_TOKEN": "a.b.c"}), "run")
        command, env = start.call_args.args
        self.assertEqual(command[1:], ["-m", "rutor", "render", "s", "1_2"])
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(self.folder))
        self.assertEqual(env["CESIUM_ION_TOKEN"], "a.b.c")

    def test_a_hand_made_install_starts_children_as_before(self) -> None:
        (self.app / "install.json").unlink()
        with patch("rutor.app_jobs.start_process", return_value="run") as start:
            app_jobs.start_rutor(["status"], {})
        self.assertEqual(start.call_args.args[1], {})


if __name__ == "__main__":
    unittest.main()
