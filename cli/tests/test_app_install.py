"""The app install: the folder the installer makes, and what the page fetches into it."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest.mock import patch

from rutor import app_install, app_jobs, config, pipeline
from rutor.errors import RutorError
from rutor.localpage import Refusal
from rutor.renderer import google
from tests.test_app import FakeRun, wait_until

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

    def test_a_file_written_by_windows_with_its_mark_in_front_is_read(self) -> None:
        # Windows tools often put a byte order mark first in a UTF-8 file.
        text = '{"version": "0.1.0", "lock_sha256": "' + "ab" * 32 + '"}'
        (self.app / "install.json").write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
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
        self.assertEqual(sorted(found), ["PATH", "PUPPETEER_CACHE_DIR"])
        self.assertEqual(found["PATH"].split(os.pathsep), [str(folder), "/somewhere/bin", "/usr/bin"])
        # The renderer's Chrome is kept in the app's folder too, so that removing the app
        # removes it, and nothing of the app is left in the person's home folder.
        self.assertEqual(found["PUPPETEER_CACHE_DIR"], str(self.app / "chrome"))
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


# ---- the install job


def node_zip(members: Mapping[str, bytes] | None = None) -> bytes:
    """A zip shaped like Node's: one folder at the top, node.exe and npm inside it."""
    if members is None:
        members = {
            "node-v24.14.0-win-x64/node.exe": b"node",
            "node-v24.14.0-win-x64/node_modules/npm/bin/npm-cli.js": b"npm",
        }
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return held.getvalue()


class Children:
    """Stands in for app_jobs.start_process: records each start and plays what the test scripted.

    A child is named by what it is: "pip", "npm" or "model".
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []
        self.runs: dict[str, list[FakeRun]] = {}
        self._scripts: dict[str, list[tuple[list[str], int, bool]]] = {}
        # Called as a child starts, with its name and argv: a test makes the files a real
        # child would have made, or looks at the disk at exactly that moment.
        self.on_start: Callable[[str, list[str], dict[str, str]], None] | None = None

    @staticmethod
    def name(argv: list[str]) -> str:
        if argv[1:4] == ["-m", "pip", "install"]:
            return "pip"
        if "ci" in argv and argv[1].endswith("npm-cli.js"):
            return "npm"
        if argv[1] == "-c" and pipeline.MODEL_ID in argv:
            return "model"
        raise AssertionError(f"a child nobody expected: {argv}")

    def script(self, name: str, lines: list[str], code: int = 0, hold: bool = False) -> None:
        self._scripts.setdefault(name, []).append((list(lines), code, hold))

    def __call__(self, argv: list[str], extra_env: Mapping[str, str]) -> FakeRun:
        name = self.name(argv)
        self.calls.append((name, list(argv), dict(extra_env)))
        queued = self._scripts.get(name)
        lines, code, hold = queued.pop(0) if queued else ([], 0, False)
        run = FakeRun(lines, code=code, hold=hold)
        self.runs.setdefault(name, []).append(run)
        if self.on_start is not None:
            self.on_start(name, list(argv), dict(extra_env))
        return run

    def names(self) -> list[str]:
        return [name for name, _argv, _env in self.calls]


class Fetches:
    """Stands in for the download: writes what the test says a server would send."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, Path, int]] = []
        # Bytes after which the connection drops, once.
        self.drop_after: int | None = None

    def __call__(self, url: str, target: Path, progress: Callable[[int], None]) -> None:
        have = target.stat().st_size if target.exists() else 0
        self.calls.append((url, target, have))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as out:
            rest = self.body[have:]
            if self.drop_after is not None:
                cut, self.drop_after = self.drop_after, None
                out.write(rest[:cut])
                out.flush()
                progress(have + len(rest[:cut]))
                raise urllib.error.URLError("the connection dropped")
            out.write(rest)
        progress(len(self.body))


class _Install(_AppFolder):
    """An app install on a disk with room, with a lock, a renderer folder and an empty model cache."""

    LOCK = "torch==2.11.0+cu128 --hash=sha256:" + "cd" * 32 + "\n"

    def setUp(self) -> None:
        super().setUp()
        self.zip = node_zip()
        (self.app / "requirements-app.lock").write_text(self.LOCK, encoding="utf-8")
        self.install(json.dumps({"version": "0.1.0", "lock_sha256": hashlib.sha256(self.LOCK.encode()).hexdigest()}))
        python = self.app / "python"
        python.mkdir()
        (python / "python.exe").write_bytes(b"")
        self.render = self.app / "cli" / "google-render"
        self.render.mkdir()
        (self.render / "package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")
        (self.render / "render.mjs").write_text("", encoding="utf-8")
        self.hub = Path(self.tmp.name).resolve() / PROFILE / ".cache" / "huggingface" / "hub"
        self.manifest_path = Path(self.tmp.name) / "app_install.json"
        self.manifest()
        for target, value in (
            ("rutor.app_install.MANIFEST", self.manifest_path),
            ("rutor.app_install._python", lambda: python / "python.exe"),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environ = patch.dict(os.environ, {"HF_HUB_CACHE": str(self.hub)})
        environ.start()
        self.addCleanup(environ.stop)
        self.children = Children()
        self.children.on_start = self.as_a_real_child
        self.fetches = Fetches(self.zip)
        self.free = 300.0
        self.installer = app_install.Installer(self.children, self.fetches, lambda: self.free)

    def manifest(self, *, stack: int = 4000, renderer: int = 3000, model: int = 2000, sha256: str | None = None) -> None:
        self.manifest_path.write_text(
            json.dumps(
                {
                    "downloads": {
                        "node": {
                            "version": "24.14.0",
                            "url": "https://nodejs.org/dist/v24.14.0/node-v24.14.0-win-x64.zip",
                            "sha256": sha256 or hashlib.sha256(self.zip).hexdigest(),
                            "size": len(self.zip),
                        }
                    },
                    "sizes": {"stack": stack, "renderer": renderer, "model": model},
                }
            ),
            encoding="utf-8",
        )

    def as_a_real_child(self, name: str, argv: list[str], env: dict[str, str]) -> None:
        if name == "npm":
            for part in (("cesium", "Build", "Cesium", "Cesium.js"), ("puppeteer", "package.json")):
                made = self.render.joinpath("node_modules", *part)
                made.parent.mkdir(parents=True, exist_ok=True)
                made.write_text("", encoding="utf-8")
        if name == "model":
            folder = "models--" + pipeline.MODEL_ID.replace("/", "--")
            (self.hub / folder / "snapshots" / argv[-3]).mkdir(parents=True, exist_ok=True)

    def model_blobs(self, size: int) -> None:
        blobs = self.hub / ("models--" + pipeline.MODEL_ID.replace("/", "--")) / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        (blobs / "abc.incomplete").write_bytes(b"\0" * size)

    def state(self) -> dict[str, object]:
        return self.installer.state()


class InstallJobTests(_Install):
    def test_the_phases_run_in_order(self) -> None:
        self.assertEqual(self.state()["phase"], "idle")
        self.installer.run()
        self.assertEqual(self.children.names(), ["pip", "npm", "model"])
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("done", ""))
        python = str(self.app / "python" / "python.exe")
        _name, pip, pip_env = self.children.calls[0]
        self.assertEqual(
            pip,
            [
                python, "-m", "pip", "install", "--require-hashes", "--no-input",
                "--only-binary", ":all:",
                "-r", str(self.app / "requirements-app.lock"),
                "--extra-index-url", "https://download.pytorch.org/whl/cu128",
            ],
        )  # fmt: skip
        # What pip keeps while it works lies in the app's own folder, under the odd name too.
        self.assertEqual(pip_env["PIP_CACHE_DIR"], str(self.app / "pip-cache"))
        self.assertIn(PROFILE, pip_env["TEMP"])
        _name, npm, npm_env = self.children.calls[1]
        node = self.app / "node"
        self.assertEqual(npm[:3], [str(node / "node.exe"), str(node / "node_modules" / "npm" / "bin" / "npm-cli.js"), "ci"])
        self.assertEqual(npm[npm.index("--prefix") + 1], str(self.render))
        self.assertEqual(npm_env["PATH"].split(os.pathsep)[0], str(node))
        _name, model, _env = self.children.calls[2]
        self.assertEqual(model[0], python)
        self.assertEqual(
            model[-4:],
            [pipeline.MODEL_ID, pipeline.PINS["model_revision"], pipeline.LORA_ID, pipeline.PINS["lora_revision"]],
        )
        # Node was fetched once, from the pinned address, and unpacked without its top folder.
        self.assertEqual([url for url, _target, _have in self.fetches.calls], ["https://nodejs.org/dist/v24.14.0/node-v24.14.0-win-x64.zip"])
        self.assertEqual((node / "node.exe").read_bytes(), b"node")
        self.assertEqual((node / "node_modules" / "npm" / "bin" / "npm-cli.js").read_bytes(), b"npm")
        self.assertEqual(self.state()["done_gb"], self.state()["total_gb"])

    def test_wheels_the_installer_brought_are_looked_in(self) -> None:
        # diffusers is pinned by commit and has no wheel on any index. The installer
        # carries one, built from that commit, and the lock names its hash.
        (self.app / "wheels").mkdir()
        self.installer.run()
        pip = self.children.calls[0][1]
        self.assertEqual(pip[pip.index("--find-links") + 1], str(self.app / "wheels"))
        # Nothing is ever built on the person's machine: a build would fetch build tools
        # that no hash of ours covers.
        self.assertEqual(pip[pip.index("--only-binary") + 1], ":all:")

    def test_the_renderers_chrome_is_fetched_into_the_apps_folder(self) -> None:
        self.installer.run()
        _name, _npm, npm_env = self.children.calls[1]
        self.assertEqual(npm_env["PUPPETEER_CACHE_DIR"], str(self.app / "chrome"))

    def test_the_fetch_is_whole_only_when_every_phase_ended_well(self) -> None:
        # tools() can say that PyTorch, Node and the renderer are there while the model is
        # half fetched. The page moves on only when the job says it is whole as well.
        self.assertIs(self.state()["whole"], False)
        self.children.script("model", ["ended: ConnectError"], code=4)
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "network"))
        self.assertIs(self.state()["whole"], False)
        self.installer.run()
        self.assertEqual(self.state()["phase"], "done")
        self.assertIs(self.state()["whole"], True)
        # It is on the disk, not in memory: an app that is started again still knows.
        again = app_install.Installer(Children(), self.fetches, lambda: self.free)
        self.assertIs(again.state()["whole"], True)

    def test_a_renderer_that_stopped_half_way_is_not_whole(self) -> None:
        # npm had made the folders the renderer check looks for when Chrome's download was cut.
        self.children.script("npm", ["npm error code ECONNRESET"], code=1)
        self.installer.run()
        self.assertIsNone(google.renderer_missing(self.render))
        self.assertIs(self.state()["whole"], False)
        self.installer.run()
        self.assertEqual(self.children.names(), ["pip", "npm", "npm", "model"])
        self.assertIs(self.state()["whole"], True)

    def test_a_newer_lock_or_another_model_is_not_whole(self) -> None:
        self.installer.run()
        self.assertIs(self.state()["whole"], True)
        with patch.dict(pipeline.PINS, {"model_revision": "f" * 40}):
            self.assertIs(app_install.Installer(Children(), self.fetches, lambda: self.free).state()["whole"], False)
        lock = self.LOCK + "numpy==2.4.6 --hash=sha256:" + "ef" * 32 + "\n"
        (self.app / "requirements-app.lock").write_text(lock, encoding="utf-8")
        self.install(json.dumps({"version": "0.2.0", "lock_sha256": hashlib.sha256(lock.encode()).hexdigest()}))
        self.assertIs(app_install.Installer(Children(), self.fetches, lambda: self.free).state()["whole"], False)

    def test_a_download_that_ends_early_without_a_fault_is_the_network(self) -> None:
        # A connection that closes early reads as the end of the file, not as an error.
        def fetch(url: str, target: Path, progress: Callable[[int], None]) -> None:
            have = target.stat().st_size if target.exists() else 0
            target.parent.mkdir(parents=True, exist_ok=True)
            end = len(self.zip) if have else len(self.zip) // 2
            with target.open("ab") as out:
                out.write(self.zip[have:end])
            progress(end)

        self.installer = app_install.Installer(self.children, fetch, lambda: self.free)
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "network"))
        self.assertEqual((self.app / "downloads" / "node.zip").stat().st_size, len(self.zip) // 2)
        self.installer.run()
        self.assertEqual(self.state()["phase"], "done")

    def test_children_write_in_utf8_whatever_the_machine_uses(self) -> None:
        # pip writes a pipe in the machine's own code page. A path under "Åsa Öberg" would
        # arrive broken, and the fault report could not take her name out of it.
        self.installer.run()
        for name, _argv, env in self.children.calls:
            if name in ("pip", "model"):
                self.assertEqual((env.get("PYTHONUTF8"), env.get("PYTHONIOENCODING")), ("1", "utf-8"), name)

    def test_the_bundled_wheel_is_put_in_again_at_every_stack_install(self) -> None:
        # The wheel of diffusers has the same version at every commit, so pip would call a
        # newer one already installed.
        (self.app / "wheels").mkdir()
        lock = self.LOCK + "diffusers==0.42.0.dev0 --hash=sha256:" + "aa" * 32 + "\n"
        (self.app / "requirements-app.lock").write_text(lock, encoding="utf-8")
        self.install(json.dumps({"version": "0.1.0", "lock_sha256": hashlib.sha256(lock.encode()).hexdigest()}))
        self.installer.run()
        self.assertEqual(self.children.names(), ["pip", "pip", "npm", "model"])
        second = self.children.calls[1][1]
        for flag in ("--require-hashes", "--force-reinstall", "--no-deps", "--no-index"):
            self.assertIn(flag, second)
        self.assertEqual(second[second.index("--find-links") + 1], str(self.app / "wheels"))
        only = Path(second[second.index("-r") + 1])
        self.assertEqual(self.state()["phase"], "done")
        self.assertFalse(only.exists())

    def test_npm_keeps_its_cache_in_the_apps_folder_and_takes_it_away(self) -> None:
        self.installer.run()
        _name, _npm, env = self.children.calls[1]
        self.assertEqual(env["npm_config_cache"], str(self.app / "npm-cache"))
        self.assertFalse((self.app / "npm-cache").exists())

    def test_the_shipped_manifest_is_one_this_code_reads(self) -> None:
        shipped = Path(app_install.__file__).with_name("app_install.json")
        manifest = app_install.load_manifest(shipped)
        self.assertTrue(manifest.node.url.startswith("https://nodejs.org/dist/"))
        self.assertGreater(manifest.total(), 10_000_000_000)

    def test_nothing_is_left_behind_after_the_fetch(self) -> None:
        self.installer.run()
        for left in ("pip-cache", "tmp", "downloads", "node.partial"):
            self.assertFalse((self.app / left).exists(), left)

    def test_a_phase_that_is_already_there_is_skipped(self) -> None:
        self.installer.run()
        self.assertEqual(len(self.fetches.calls), 1)
        again = app_install.Installer(self.children, self.fetches, lambda: self.free)
        again.run()
        # Node, the stack and the renderer are there. The model child is the one that knows
        # whether the model is whole, so it is always asked.
        self.assertEqual(self.children.names(), ["pip", "npm", "model", "model"])
        self.assertEqual(len(self.fetches.calls), 1)
        self.assertEqual(again.state()["phase"], "done")

    def test_a_node_folder_of_another_version_is_fetched_again(self) -> None:
        self.installer.run()
        (self.app / "node" / app_install.NODE_MARKER).write_text("22.1.0", encoding="utf-8")
        self.installer.run()
        self.assertEqual(len(self.fetches.calls), 2)

    def test_a_new_lock_installs_the_stack_again(self) -> None:
        self.installer.run()
        lock = self.LOCK + "numpy==2.4.6 --hash=sha256:" + "ef" * 32 + "\n"
        (self.app / "requirements-app.lock").write_text(lock, encoding="utf-8")
        self.install(json.dumps({"version": "0.2.0", "lock_sha256": hashlib.sha256(lock.encode()).hexdigest()}))
        self.installer.run()
        self.assertEqual(self.children.names().count("pip"), 2)

    def test_a_phase_can_be_asked_for_again(self) -> None:
        # The page asks tools() after a run. When a tool is still missing, the phase that
        # brings it is done again, whatever the job itself believes.
        self.installer.run()
        self.installer.run(redo=("stack", "renderer"))
        self.assertEqual(self.children.names(), ["pip", "npm", "model", "pip", "npm", "model"])

    def test_a_failed_download_goes_on_where_it_stopped(self) -> None:
        self.fetches.drop_after = 50
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "network"))
        self.assertEqual(self.children.names(), ["pip"])
        target = self.fetches.calls[0][1]
        self.assertEqual(target.stat().st_size, 50)
        self.assertFalse((self.app / "node").exists())
        self.installer.run()
        # The same file, which still held what had arrived.
        self.assertEqual([(call[1], call[2]) for call in self.fetches.calls], [(target, 0), (target, 50)])
        self.assertEqual(self.state()["phase"], "done")
        # The stack was not installed twice.
        self.assertEqual(self.children.names(), ["pip", "npm", "model"])

    def test_a_file_that_changed_upstream_is_refused(self) -> None:
        self.manifest(sha256="12" * 32)
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "changed"))
        self.assertFalse(self.fetches.calls[0][1].exists())
        self.assertFalse((self.app / "node").exists())
        self.assertFalse((self.app / "node.partial").exists())
        self.assertEqual(self.children.names(), ["pip"])

    def test_a_file_larger_than_pinned_is_refused_while_it_arrives(self) -> None:
        targets: list[Path] = []

        def fetch(url: str, target: Path, progress: Callable[[int], None]) -> None:
            targets.append(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.zip + b"more")
            progress(len(self.zip) + 4)
            # A server that never stops sending would fill the disk before any hash is checked.
            raise AssertionError("the download went on past the pinned size")

        self.installer = app_install.Installer(self.children, fetch, lambda: self.free)
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "changed"))
        self.assertFalse(targets[0].exists())
        self.assertFalse((self.app / "node").exists())

    def test_a_file_of_the_right_size_and_the_wrong_bytes_is_refused(self) -> None:
        self.fetches.body = bytes(len(self.zip))
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "changed"))
        self.assertFalse(self.fetches.calls[0][1].exists())
        self.assertFalse((self.app / "node").exists())

    def test_a_lock_that_is_not_the_installers_is_refused(self) -> None:
        (self.app / "requirements-app.lock").write_text("evil==1.0\n", encoding="utf-8")
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "changed"))
        self.assertEqual(self.children.names(), [])

    def test_a_member_that_leaves_the_folder_is_refused(self) -> None:
        for members in (
            {"node/node.exe": b"node", "node/../../evil.txt": b"x"},
            {"node/node.exe": b"node", "/evil.txt": b"x"},
            {"node/node.exe": b"node", "other/evil.txt": b"x"},
            {"node/node.exe": b"node", "node/C:/evil.txt": b"x"},
            {"node/node.exe": b"node", "node\\..\\..\\evil.txt": b"x"},
        ):
            with self.subTest(members=sorted(members)):
                self.zip = node_zip(members)
                self.fetches = Fetches(self.zip)
                self.manifest()
                installer = app_install.Installer(Children(), self.fetches, lambda: self.free)
                installer.run()
                self.assertEqual((installer.state()["phase"], installer.state()["problem"]), ("failed", "failed"))
                self.assertFalse((self.app / "node").exists())
                self.assertFalse((self.app / "node.partial").exists())
                self.assertEqual(sorted(path.name for path in self.app.rglob("evil.txt")), [])
                self.assertFalse((self.app.parent / "evil.txt").exists())

    def test_a_full_disk_is_said_before_it_starts(self) -> None:
        self.manifest(model=20_000_000_000)
        self.free = 5.0
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "disk"))
        self.assertEqual(self.children.names(), [])
        self.assertEqual(self.fetches.calls, [])

    def test_a_full_disk_during_pip_is_said_as_disk(self) -> None:
        self.children.script(
            "pip",
            ["ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device"],
            code=1,
        )
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "disk"))
        self.assertEqual(self.children.names(), ["pip"])
        # A half-installed stack is not marked as there.
        self.installer.run()
        self.assertEqual(self.children.names()[:2], ["pip", "pip"])

    def test_what_a_child_says_names_the_problem(self) -> None:
        cases = (
            ("pip", "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE.", 1, "changed"),
            ("pip", "NewConnectionError: Failed to establish a new connection: getaddrinfo failed", 1, "network"),
            ("pip", "ERROR: something nobody has seen", 2, "failed"),
            ("npm", "npm error code EINTEGRITY", 1, "changed"),
            ("npm", "npm error code ENOSPC", 1, "disk"),
            ("npm", "npm error code ETIMEDOUT", 1, "network"),
            ("model", "ended: ConnectError", 4, "network"),
            ("model", "ended: OSError", 6, "disk"),
            ("model", "ended: ValueError", 1, "failed"),
        )
        for name, line, code, problem in cases:
            with self.subTest(child=name, line=line):
                children = Children()
                children.on_start = self.as_a_real_child
                children.script(name, [line], code=code)
                installer = app_install.Installer(children, self.fetches, lambda: self.free)
                installer.run()
                self.assertEqual((installer.state()["phase"], installer.state()["problem"]), ("failed", problem))
                self.assertEqual(children.names()[-1], name)

    def test_a_disk_that_filled_is_said_as_disk_whatever_the_child_said(self) -> None:
        self.children.script("pip", ["Traceback (most recent call last):"], code=1)
        self.children.on_start = lambda name, argv, env: setattr(self, "free", 0.2)
        self.installer.run()
        self.assertEqual(self.state()["problem"], "disk")

    def test_stop_ends_the_running_child(self) -> None:
        self.children.script("pip", ["Collecting torch"], hold=True)
        thread = threading.Thread(target=self.installer.run, daemon=True)
        thread.start()
        wait_until(lambda: bool(self.children.runs.get("pip")) and self.state()["phase"] == "stack")
        self.installer.stop()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.children.runs["pip"][0].stopped)
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("stopped", ""))
        self.assertEqual(self.children.names(), ["pip"])

    def test_stop_ends_a_download(self) -> None:
        def fetch(url: str, target: Path, progress: Callable[[int], None]) -> None:
            self.installer.stop()
            progress(10)
            raise AssertionError("the download went on after a stop")

        self.installer = app_install.Installer(self.children, fetch, lambda: self.free)
        self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("stopped", ""))
        self.assertEqual(self.children.names(), ["pip"])

    def test_a_second_run_at_the_same_time_is_refused(self) -> None:
        self.children.script("pip", [], hold=True)
        thread = threading.Thread(target=self.installer.run, daemon=True)
        thread.start()
        wait_until(lambda: bool(self.children.runs.get("pip")))
        with self.assertRaises(Refusal) as caught:
            self.installer.run()
        self.assertEqual(caught.exception.code, "busy")
        self.installer.stop()
        thread.join(timeout=10)
        self.assertEqual(self.children.names(), ["pip"])

    def test_pip_is_never_run_without_hashes(self) -> None:
        self.installer.run()
        self.assertIn("--require-hashes", self.children.calls[0][1])
        real_lock = Path(__file__).resolve().parents[2] / "requirements-app.lock"
        if not real_lock.is_file():
            self.skipTest("requirements-app.lock is written by Task 7")
        self.assertNotIn("git+", real_lock.read_text(encoding="utf-8"))

    def test_nothing_is_installed_into_a_hand_made_install(self) -> None:
        (self.app / "install.json").unlink()
        with self.assertRaises(Refusal) as caught:
            self.installer.run()
        self.assertEqual(caught.exception.code, "not_an_app")
        self.assertEqual(self.children.names(), [])
        self.assertEqual(self.fetches.calls, [])
        self.assertEqual(
            self.state(), {"phase": "idle", "done_gb": 0.0, "total_gb": 0.0, "problem": "", "whole": False}
        )

    def test_nothing_is_installed_into_another_python(self) -> None:
        # install.json copied into a clone does not make it an app install: pip would
        # install into whatever Python the person runs.
        with patch("rutor.app_install._python", lambda: Path(self.tmp.name) / "venv" / "bin" / "python"):
            with self.assertRaises(Refusal) as caught:
                self.installer.run()
        self.assertEqual(caught.exception.code, "not_an_app")
        self.assertEqual(self.children.names(), [])

    def test_progress_is_what_is_on_the_disk(self) -> None:
        self.assertEqual(self.state()["total_gb"], (4000 + len(self.zip) + 3000 + 2000) / 1e9)
        self.assertEqual(self.state()["done_gb"], 0.0)
        self.model_blobs(600)
        fresh = app_install.Installer(self.children, self.fetches, lambda: self.free)
        self.assertEqual(fresh.state()["done_gb"], 600 / 1e9)
        self.assertIs(fresh.state()["whole"], False)
        self.assertEqual(self.children.names(), [])
        # More on the disk than the pinned size is never shown as more than all of it.
        self.model_blobs(5000)
        fresh = app_install.Installer(self.children, self.fetches, lambda: self.free)
        self.assertEqual(fresh.state()["done_gb"], 2000 / 1e9)

    def test_progress_counts_model_files_wherever_the_hub_library_puts_them(self) -> None:
        # On a Windows that cannot make links, a finished file is moved into snapshots.
        snapshot = self.hub / ("models--" + pipeline.MODEL_ID.replace("/", "--")) / "snapshots" / "abc" / "vae"
        snapshot.mkdir(parents=True)
        (snapshot / "model.safetensors").write_bytes(b"\0" * 700)
        self.assertEqual(self.state()["done_gb"], 700 / 1e9)

    def test_progress_counts_the_renderers_chrome_too(self) -> None:
        # Chrome is most of what the renderer fetches, and it lands beside node_modules.
        (self.render / "node_modules" / "cesium").mkdir(parents=True)
        (self.render / "node_modules" / "cesium" / "a.js").write_bytes(b"\0" * 300)
        (self.app / "chrome" / "chrome" / "win64").mkdir(parents=True)
        (self.app / "chrome" / "chrome" / "win64" / "chrome.exe").write_bytes(b"\0" * 900)
        self.assertEqual(self.state()["done_gb"], 1200 / 1e9)

    def test_progress_counts_what_pip_has_fetched(self) -> None:
        seen: list[object] = []

        def look(name: str, argv: list[str], env: dict[str, str]) -> None:
            self.as_a_real_child(name, argv, env)
            if name == "pip":
                Path(env["TEMP"], "pip-unpack-1").mkdir(parents=True)
                Path(env["TEMP"], "pip-unpack-1", "torch.whl").write_bytes(b"\0" * 1000)
                seen.append(self.installer.state()["done_gb"])

        self.children.on_start = look
        self.installer.run()
        self.assertEqual(seen, [1000 / 1e9])

    def test_the_last_lines_are_kept_for_a_report(self) -> None:
        self.children.script("pip", [f"Collecting package-{number}" for number in range(40)], code=1)
        self.installer.run()
        lines = self.installer.lines()
        self.assertEqual(len(lines), 20)
        self.assertEqual(lines[-1], "Collecting package-39")

    def test_a_pin_that_is_empty_fetches_no_model(self) -> None:
        with patch.dict(pipeline.PINS, {"model_revision": ""}):
            self.installer.run()
        self.assertEqual((self.state()["phase"], self.state()["problem"]), ("failed", "failed"))
        self.assertNotIn("model", self.children.names())


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = io.BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FetchTests(unittest.TestCase):
    URL = "https://nodejs.org/dist/v24.14.0/node-v24.14.0-win-x64.zip"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / PROFILE / "downloads" / "node.zip"
        self.requests: list[dict[str, str]] = []

    def opener(self, response: _Response) -> Callable[..., _Response]:
        def open_url(request: object, timeout: float = 0) -> _Response:
            self.requests.append({key.lower(): value for key, value in request.header_items()})  # type: ignore[attr-defined]
            return response

        return open_url

    def test_a_fresh_download_is_written_and_counted(self) -> None:
        seen: list[int] = []
        app_install.fetch_url(self.URL, self.target, seen.append, opener=self.opener(_Response(200, b"abcdef")))
        self.assertEqual(self.target.read_bytes(), b"abcdef")
        self.assertEqual(seen[-1], 6)
        self.assertNotIn("range", self.requests[0])

    def test_a_download_goes_on_from_what_is_there(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"abc")
        seen: list[int] = []
        response = _Response(206, b"def", {"Content-Range": "bytes 3-5/6"})
        app_install.fetch_url(self.URL, self.target, seen.append, opener=self.opener(response))
        self.assertEqual(self.requests[0]["range"], "bytes=3-")
        self.assertEqual(self.target.read_bytes(), b"abcdef")
        self.assertEqual(seen[-1], 6)

    def test_a_server_that_starts_over_is_written_from_the_start(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"abc")
        app_install.fetch_url(self.URL, self.target, lambda n: None, opener=self.opener(_Response(200, b"abcdef")))
        self.assertEqual(self.target.read_bytes(), b"abcdef")

    def test_a_part_from_another_place_in_the_file_is_not_joined_on(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"abc")
        response = _Response(206, b"xyz", {"Content-Range": "bytes 0-2/6"})
        with self.assertRaises(OSError):
            app_install.fetch_url(self.URL, self.target, lambda n: None, opener=self.opener(response))
        self.assertEqual(self.target.read_bytes(), b"abc")

    def test_only_https_is_fetched(self) -> None:
        for url in ("http://nodejs.org/x.zip", "file:///etc/passwd", "ftp://nodejs.org/x.zip"):
            with self.assertRaises(OSError):
                app_install.fetch_url(url, self.target, lambda n: None, opener=self.opener(_Response(200, b"x")))
        self.assertEqual(self.requests, [])
        self.assertFalse(self.target.exists())

    def test_a_redirect_to_another_host_is_not_followed(self) -> None:
        follow = app_install._KnownHosts("nodejs.org")
        request = urllib.request.Request(self.URL)
        for elsewhere in ("https://evil.test/node.zip", "http://nodejs.org/node.zip", "https://nodejs.org.evil.test/x"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                follow.redirect_request(request, io.BytesIO(), 302, "Found", {}, elsewhere)  # type: ignore[arg-type]
            caught.exception.close()
        for allowed in ("https://nodejs.org/other.zip", "https://objects.githubusercontent.com/x"):
            followed = follow.redirect_request(request, io.BytesIO(), 302, "Found", {}, allowed)  # type: ignore[arg-type]
            self.assertEqual(followed.full_url, allowed)  # type: ignore[union-attr]


def _load_lock_tool() -> object:
    """tools/make_app_lock.py as a module. It is run by hand on a Windows machine."""
    import importlib.machinery
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "tools" / "make_app_lock.py"
    loader = importlib.machinery.SourceFileLoader("make_app_lock", str(path))
    spec = importlib.util.spec_from_loader("make_app_lock", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class LockToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = _load_lock_tool()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / PROFILE / "wheels"
        self.folder.mkdir(parents=True)

    def put(self, name: str, body: bytes = b"wheel") -> str:
        (self.folder / name).write_bytes(body)
        return hashlib.sha256(body).hexdigest()

    def test_a_wheel_is_named_by_its_file(self) -> None:
        pin = self.tool.wheel_pin  # type: ignore[attr-defined]
        self.assertEqual(pin("torch-2.11.0+cu128-cp311-cp311-win_amd64.whl"), ("torch", "2.11.0+cu128"))
        self.assertEqual(pin("huggingface_hub-2.4.0-py3-none-any.whl"), ("huggingface-hub", "2.4.0"))
        self.assertEqual(pin("PyYAML-6.0.3-cp311-cp311-win_amd64.whl"), ("pyyaml", "6.0.3"))
        self.assertEqual(pin("typing_extensions-4.15.0-1-py3-none-any.whl"), ("typing-extensions", "4.15.0"))
        for odd in ("torch.whl", "torch-2.11.0.tar.gz", "a-b.whl"):
            with self.assertRaises(ValueError):
                pin(odd)

    def test_every_file_becomes_one_line_with_its_hash(self) -> None:
        torch = self.put("torch-2.11.0+cu128-cp311-cp311-win_amd64.whl", b"torch")
        numpy = self.put("numpy-2.4.6-cp311-cp311-win_amd64.whl", b"numpy")
        lines, size, left_out = self.tool.lock_lines(self.folder)  # type: ignore[attr-defined]
        self.assertEqual(lines, [f"numpy==2.4.6 --hash=sha256:{numpy}", f"torch==2.11.0+cu128 --hash=sha256:{torch}"])
        self.assertEqual(size, 10)
        self.assertEqual(left_out, [])

    def test_the_source_of_diffusers_is_left_to_the_installer(self) -> None:
        self.put("numpy-2.4.6-cp311-cp311-win_amd64.whl")
        self.put("diffusers-0.42.0.dev0.zip", b"source")
        lines, size, left_out = self.tool.lock_lines(self.folder)  # type: ignore[attr-defined]
        self.assertEqual(len(lines), 1)
        self.assertEqual(left_out, ["diffusers-0.42.0.dev0.zip"])
        # Its size is not part of what the page fetches.
        self.assertEqual(size, 5)

    def test_anything_else_that_is_not_a_wheel_stops_the_lock(self) -> None:
        self.put("numpy-2.4.6-cp311-cp311-win_amd64.whl")
        self.put("somepackage-1.0.tar.gz")
        with self.assertRaises(ValueError) as caught:
            self.tool.lock_lines(self.folder)  # type: ignore[attr-defined]
        self.assertIn("somepackage-1.0.tar.gz", str(caught.exception))

    def test_two_versions_of_one_package_stop_the_lock(self) -> None:
        self.put("numpy-2.4.6-cp311-cp311-win_amd64.whl")
        self.put("numpy-2.4.5-cp311-cp311-win_amd64.whl", b"older")
        with self.assertRaises(ValueError):
            self.tool.lock_lines(self.folder)  # type: ignore[attr-defined]

    def test_pip_is_asked_to_download_and_nothing_else(self) -> None:
        argv = self.tool.download_command(self.folder, Path("requirements.txt"))  # type: ignore[attr-defined]
        self.assertEqual(argv[1:4], ["-m", "pip", "download"])
        self.assertNotIn("install", argv)
        self.assertIn("torch==2.11.0+cu128", argv)
        self.assertIn("torchvision==0.26.0+cu128", argv)
        self.assertEqual(argv[argv.index("--dest") + 1], str(self.folder))
        self.assertEqual(argv[argv.index("--extra-index-url") + 1], app_install.TORCH_INDEX)
        self.assertNotIn("--constraint", argv)

    def test_the_versions_of_a_working_install_can_be_held(self) -> None:
        # requirements.txt pins what the redraw names. What those packages need in turn is
        # held to what a machine that draws today has, from its own `pip freeze`.
        held = self.folder.parent / "constraints.txt"
        argv = self.tool.download_command(self.folder, Path("requirements.txt"), held)  # type: ignore[attr-defined]
        self.assertEqual(argv[argv.index("--constraint") + 1], str(held))


class InstallerFilesTests(unittest.TestCase):
    """What the installer may and may not do, read off the files that build it."""

    ROOT = Path(__file__).resolve().parents[2]

    def read(self, *parts: str) -> str:
        return self.ROOT.joinpath(*parts).read_text(encoding="utf-8")

    def test_it_never_asks_for_administrator_rights(self) -> None:
        script = self.read("installer", "pixelbygget.iss")
        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertNotIn("PrivilegesRequiredOverridesAllowed", script.replace("; PrivilegesRequiredOverridesAllowed", ""))
        self.assertIn(r"DefaultDirName={localappdata}\Pixelbygget", script)
        # Nothing outside the person's own folder: no machine-wide registry, no PATH.
        for forbidden in ("HKLM", "[Registry]", "ChangesEnvironment", "{commonpf", "{sys}", "{win}"):
            self.assertNotIn(forbidden, script)

    def test_the_shortcuts_start_the_app_without_a_terminal(self) -> None:
        script = self.read("installer", "pixelbygget.iss")
        icons = script[script.index("[Icons]") : script.index("[Run]")]
        self.assertEqual(icons.count(r'Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\start.pyw"""'), 2)
        self.assertIn(r"{autoprograms}\Pixelbygget", icons)
        self.assertIn(r"{autodesktop}\Pixelbygget", icons)

    def test_the_installer_script_is_plain_ascii(self) -> None:
        # Inno Setup reads a file without a byte order mark in the machine's own code page.
        for name in ("pixelbygget.iss", "build.ps1"):
            self.assertTrue(self.read("installer", name).isascii(), name)

    def test_what_the_build_fetches_is_pinned(self) -> None:
        build = self.read("installer", "build.ps1")
        import re

        pins = dict(re.findall(r"^\$(\w+Sha256) = '([0-9a-f]{64})'$", build, re.MULTILINE))
        self.assertEqual(sorted(pins), ["DiffusersSha256", "PythonSha256"])
        self.assertIn("cpython-3.11.", build)
        self.assertIn("x86_64-pc-windows-msvc-install_only.tar.gz", build)
        for url in re.findall(r"^\$\w+Url = ['\"]([^'\"]+)['\"]$", build, re.MULTILINE):
            self.assertTrue(url.startswith("https://github.com/"), url)
        # Each fetch goes through the one function that checks the hash before anything reads the file.
        self.assertEqual(build.count("Invoke-WebRequest"), 1)
        self.assertEqual(build.count("Get-Pinned -Url"), 2)

    def test_the_build_makes_the_wheel_of_the_commit_the_requirements_pin(self) -> None:
        import re

        pinned = re.search(r"diffusers\.git@([0-9a-f]{40})", self.read("requirements.txt"))
        assert pinned is not None
        self.assertIn(f"$DiffusersCommit = '{pinned.group(1)}'", self.read("installer", "build.ps1"))

    def test_the_workflow_only_ever_makes_a_draft(self) -> None:
        workflow = self.read(".github", "workflows", "installer.yml")
        self.assertEqual(workflow.count("gh release create"), 1)
        self.assertIn("--draft", workflow[workflow.index("gh release create") :])
        for forbidden in ("gh release edit", "--draft=false", "--latest", "softprops/action-gh-release"):
            self.assertNotIn(forbidden, workflow)
        # The job that builds and runs what was fetched cannot write to the repository.
        build_job = workflow[workflow.index("  build:") : workflow.index("  draft:")]
        self.assertNotIn("contents: write", build_job)

    def test_the_workflow_installs_under_a_profile_with_a_space_and_swedish_letters(self) -> None:
        self.assertIn("'Åsa Öberg'", self.read(".github", "workflows", "installer.yml"))


class LockTests(unittest.TestCase):
    """requirements-app.lock, as tools/make_app_lock.py wrote it on a Windows machine."""

    ROOT = Path(__file__).resolve().parents[2]

    def lines(self) -> list[str]:
        text = (self.ROOT / "requirements-app.lock").read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]

    def pins(self) -> dict[str, str]:
        import re

        found: dict[str, str] = {}
        for line in self.lines():
            matched = re.fullmatch(r"([a-z0-9-]+)==(\S+) --hash=sha256:[0-9a-f]{64}", line)
            self.assertIsNotNone(matched, line)
            assert matched is not None
            self.assertNotIn(matched.group(1), found)
            found[matched.group(1)] = matched.group(2)
        return found

    def test_every_line_of_the_lock_has_a_hash(self) -> None:
        self.assertGreater(len(self.pins()), 20)

    def test_the_lock_needs_no_git(self) -> None:
        text = "\n".join(self.lines())
        for forbidden in ("git+", " @ ", "http://", "https://", "--index-url", "-e "):
            self.assertNotIn(forbidden, text)
        # diffusers is on no index. The installer's build adds the line for the wheel it makes.
        self.assertNotIn("diffusers", self.pins())

    def test_the_lock_matches_the_pins(self) -> None:
        import re

        pins = self.pins()
        self.assertEqual(pins["torch"], "2.11.0+cu128")
        self.assertEqual(pins["torchvision"], "0.26.0+cu128")
        wanted = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)$", (self.ROOT / "requirements.txt").read_text(encoding="utf-8"), re.MULTILINE)
        self.assertGreaterEqual(len(wanted), 9)
        for name, version in wanted:
            self.assertEqual(pins.get(re.sub(r"[-_.]+", "-", name).lower()), version, name)

    def test_every_download_in_the_manifest_is_pinned(self) -> None:
        import re

        manifest = json.loads(Path(app_install.__file__).with_name("app_install.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest["downloads"]), ["node"])
        for name, entry in manifest["downloads"].items():
            self.assertTrue(entry["url"].startswith("https://"), name)
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(entry["size"], 0)
        for name, size in manifest["sizes"].items():
            self.assertIsInstance(size, int, name)
            self.assertGreater(size, 0, name)
        # Measured, not guessed: the lock's wheels as the tool weighed them, the renderer's
        # packages and Chrome on the test PC, the model's files at the pinned commits.
        self.assertEqual(manifest["sizes"], {"stack": 2860862987, "renderer": 919628986, "model": 11470921791})
        del re


if __name__ == "__main__":
    unittest.main()
