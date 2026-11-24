"""The install job: fetching the drawing tools into an app install, from the app page.

The Windows installer is small. Everything large is fetched here, after the page has said
that the machine can draw and the person has accepted the terms: the model stack (PyTorch
and the rest, with pip), Node, the map renderer's packages (with npm) and the model.

Every download is pinned. pip runs with `--require-hashes` against the lock the installer
shipped, whose own SHA-256 the installer wrote down. Node is one zip with a pinned address,
size and SHA-256, checked before a byte of it is unpacked. npm installs from the renderer's
package-lock, which carries a hash for every package. The model and the LoRA are fetched by
commit, as the redraw loads them.

This module knows nothing about HTTP or the page. It never decides that a tool is ready:
after a run the page asks `app.tools()` again, and a tool that is still missing has its
phase done again with `run(redo=...)`.
"""

from __future__ import annotations

import errno
import hashlib
import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from rutor import app_jobs, commands, config, pipeline
from rutor.localpage import Refusal
from rutor.renderer import google

# Address, target file, and a callback with the bytes the target holds so far.
Fetch = Callable[[str, Path, Callable[[int], None]], None]

PHASES = ("stack", "node", "renderer", "model")

MANIFEST = Path(__file__).with_name("app_install.json")
LOCK_NAME = "requirements-app.lock"
# Wheels the installer carries: diffusers, which is pinned by commit and is on no index.
WHEELS = "wheels"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"

# Written when a phase has ended well, beside what the phase made, so that a folder that
# is deleted or replaced takes its marker with it.
STACK_MARKER = "pixelbygget-stack.sha256"
NODE_MARKER = "pixelbygget-node-version"
RENDERER_MARKER = ".pixelbygget-lock.sha256"

# The stack's wheels unpack to more than they weigh, and pip holds a wheel twice while it
# arrives. Room for that is asked for on top of what is still to fetch.
HEADROOM_GB = 4.0
# Under this, a child that failed is taken to have failed for want of room.
FULL_DISK_GB = 0.5

LINES_KEPT = 20
MEASURE_SECONDS = 2.0
UNPACKED_MAX = 1_000_000_000
CHUNK = 1 << 16
FETCH_SECONDS = 60.0

# A redirect is followed to the address's own host, and to the one host GitHub serves
# release files from. Nowhere else.
REDIRECT_HOSTS = frozenset({"objects.githubusercontent.com"})

_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}")

# What a child prints when it ends badly, by the problem it means. Checked in this order.
_SAID = (
    ("disk", ("no space left on device", "enospc", "not enough space on the disk")),
    ("changed", ("do not match the hashes", "eintegrity")),
    (
        "network",
        (
            "connectionerror",
            "connection aborted",
            "connection reset",
            "readtimeout",
            "read timed out",
            "name resolution",
            "getaddrinfo failed",
            "max retries exceeded",
            "network is unreachable",
            "enotfound",
            "etimedout",
            "econnreset",
            "econnrefused",
            "eai_again",
        ),
    ),
)

# How the model child ends: see _MODEL_SCRIPT.
_MODEL_EXITS = {4: "network", 6: "disk"}

# Run by a child of the app's own Python once the stack is installed. It fetches the model
# and the LoRA at their pinned commits into the Hugging Face cache, where the redraw looks.
# A download that was cut off goes on from where it stopped: the hub library does that.
# Only the kind of a fault is printed, never its message.
_MODEL_SCRIPT = """\
import sys

def main() -> int:
    try:
        from huggingface_hub import snapshot_download
        pairs = sys.argv[1:]
        for repo, revision in zip(pairs[0::2], pairs[1::2]):
            snapshot_download(repo, revision=revision)
            print("fetched:", repo, flush=True)
        return 0
    except BaseException as exc:
        kind = type(exc)
        print("ended:", kind.__name__, flush=True)
        if isinstance(exc, OSError) and exc.errno == 28:
            return 6
        outside = ("httpx", "httpcore", "requests", "urllib3", "huggingface_hub", "socket", "ssl")
        if isinstance(exc, OSError) or kind.__module__.split(".")[0] in outside:
            return 4
        return 1

sys.exit(main())
"""


class _Stopped(Exception):
    """The person pressed stop, or the page went away."""


class _Failed(Exception):
    def __init__(self, problem: str) -> None:
        self.problem = problem
        super().__init__(problem)


@dataclass(frozen=True)
class _Download:
    version: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class _Manifest:
    node: _Download
    stack: int
    renderer: int
    model: int

    def size(self, phase: str) -> int:
        return {"stack": self.stack, "node": self.node.size, "renderer": self.renderer, "model": self.model}[phase]

    def total(self) -> int:
        return sum(self.size(phase) for phase in PHASES)


def _whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("a size must be a whole number above zero")
    return value


def load_manifest(path: Path | None = None) -> _Manifest:
    """The pinned downloads. A file of any other shape raises ValueError or OSError."""
    found = json.loads((path or MANIFEST).read_text(encoding="utf-8"))
    downloads = found["downloads"]
    sizes = found["sizes"]
    node = downloads["node"]
    version, url, digest = node["version"], node["url"], node["sha256"]
    if not (isinstance(version, str) and _VERSION.fullmatch(version)):
        raise ValueError("the manifest names no Node version")
    if not (isinstance(url, str) and url.startswith("https://")):
        raise ValueError("the manifest's address is not https")
    if not (isinstance(digest, str) and _SHA256.fullmatch(digest)):
        raise ValueError("the manifest's SHA-256 is not one")
    return _Manifest(
        node=_Download(version, url, digest, _whole(node["size"])),
        stack=_whole(sizes["stack"]),
        renderer=_whole(sizes["renderer"]),
        model=_whole(sizes["model"]),
    )


# ---- the download


class _Response(Protocol):
    status: int

    def read(self, size: int = -1) -> bytes: ...


class _KnownHosts(urllib.request.HTTPRedirectHandler):
    """Follows a redirect to the first address's own host or to GitHub's file host, over https."""

    def __init__(self, host: str) -> None:
        self._host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme != "https" or (parts.hostname or "") not in {self._host, *REDIRECT_HOSTS}:
            raise urllib.error.HTTPError(req.full_url, code, "the download was sent to another host", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(
    url: str,
    target: Path,
    progress: Callable[[int], None],
    *,
    opener: Callable[..., object] | None = None,
) -> None:
    """Fetch an https address into a file, going on from what the file already holds.

    Faults are OSError: urllib's own are, and so is a full disk. What has arrived stays in
    the file, for the next try. Whether the bytes are the right ones is not judged here:
    the caller checks the SHA-256 of the whole file.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host:
        raise OSError("only https addresses are fetched")
    have = target.stat().st_size if target.is_file() else 0
    headers = {"User-Agent": "rutor", "Accept-Encoding": "identity"}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    open_url = opener if opener is not None else urllib.request.build_opener(_KnownHosts(host)).open
    try:
        response = open_url(request, timeout=FETCH_SECONDS)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have:
            # Nothing is left to send: the file is whole, or longer than it should be.
            # The SHA-256 check says which.
            progress(have)
            return
        raise
    with response as answer:  # type: ignore[attr-defined]
        status = int(getattr(answer, "status", 0))
        if status == 206 and have:
            # The part must start where the file ends, or the two do not belong together.
            said = str(answer.headers.get("Content-Range", ""))
            if not said.startswith(f"bytes {have}-"):
                raise OSError("the server sent another part of the file than was asked for")
            mode = "ab"
        elif status == 200:
            # The server sends the whole file, so what is here is written over.
            have = 0
            mode = "wb"
        else:
            raise OSError(f"the download answered {status}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open(mode) as out:
            while True:
                chunk = answer.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                have += len(chunk)
                progress(have)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as held:
        for chunk in iter(lambda: held.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def unpack(archive: Path, folder: Path) -> None:
    """Unpack a zip that holds one folder into `folder`, without that top folder.

    Each member is placed by its own name, one at a time. A name that would land outside
    `folder`, a second top folder, a link or a zip that unpacks to more than it should is
    refused with ValueError before anything is written.
    """
    with zipfile.ZipFile(archive) as held:
        members = held.infolist()
        tops: set[str] = set()
        planned: list[tuple[zipfile.ZipInfo, Path]] = []
        unpacked = 0
        for info in members:
            name = info.filename
            parts = PurePosixPath(name).parts
            if (
                not parts
                or name.startswith("/")
                or "\\" in name
                or any(part in ("..", ".") or ":" in part for part in parts)
                or _is_link(info)
            ):
                raise ValueError("the archive holds a name that is not a plain path")
            tops.add(parts[0])
            unpacked += info.file_size
            if len(parts) == 1:
                if not info.is_dir():
                    raise ValueError("the archive holds a file outside its one folder")
                continue
            planned.append((info, folder.joinpath(*parts[1:])))
        if len(tops) != 1 or unpacked > UNPACKED_MAX:
            raise ValueError("the archive is not one folder of the expected size")
        root = folder.resolve()
        for info, path in planned:
            if root not in path.resolve().parents:
                raise ValueError("the archive holds a name that leaves the folder")
        folder.mkdir(parents=True, exist_ok=True)
        for info, path in planned:
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with held.open(info) as source, path.open("wb") as out:
                shutil.copyfileobj(source, out)


# ---- the job


def _python() -> Path:
    return Path(sys.executable)


def _own_python(app: Path) -> bool:
    """Whether the Python that runs this is the one the installer put in the app's folder."""
    try:
        return (app / "python").resolve() in _python().resolve().parents
    except OSError:
        return False


def is_app() -> bool:
    """Whether tools may be fetched here: an app install, run by the Python that came with it."""
    app = config.app_dir()
    return app is not None and _own_python(app)


def _tree_bytes(folder: Path) -> int:
    """The size of everything in a folder. A file that goes away while it is counted is skipped."""
    total = 0
    stack = [folder]
    while stack:
        try:
            entries = list(stack.pop().iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    stack.append(entry)
                else:
                    total += entry.stat().st_size
            except OSError:
                continue
    return total


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _is_full(exc: OSError) -> bool:
    return exc.errno == errno.ENOSPC


class Installer:
    """Fetches what an app install lacks, one phase after the other, and says how far it is.

    `run()` works on the thread that calls it. `stop()` and `state()` are for other threads.
    One lock guards the state; nothing slow is done while it is held.
    """

    def __init__(
        self,
        start: app_jobs.Start,
        fetch: Fetch,
        free_gb: Callable[[], float],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._start = start
        self._fetch = fetch
        self._free_gb = free_gb
        self._clock = clock
        self._lock = threading.Lock()
        self._phase = "idle"
        self._problem = ""
        self._running = False
        self._stop_asked = False
        self._run: app_jobs.Run | None = None
        self._lines: list[str] = []
        # The model child said in this process that both downloads are whole.
        self._model_done = False
        self._measured: tuple[float, float, float] | None = None

    # ---- what other threads ask

    def state(self) -> dict[str, object]:
        with self._lock:
            phase, problem = self._phase, self._problem
            measured = self._measured
        app = config.app_dir()
        if app is None:
            return {"phase": "idle", "done_gb": 0.0, "total_gb": 0.0, "problem": ""}
        now = self._clock()
        # Counting files takes a moment, and the page asks every second.
        if measured is None or now - measured[0] >= MEASURE_SECONDS:
            try:
                manifest = load_manifest()
                done = sum(self._on_disk(app, manifest, name) for name in PHASES) / 1e9
                total = manifest.total() / 1e9
            except (OSError, ValueError, KeyError, TypeError):
                done, total = 0.0, 0.0
            measured = (now, done, total)
            with self._lock:
                self._measured = measured
        return {"phase": phase, "done_gb": measured[1], "total_gb": measured[2], "problem": problem}

    def running(self) -> bool:
        with self._lock:
            return self._running

    def lines(self) -> list[str]:
        """The last things the children said, for a report the person may paste into an issue."""
        with self._lock:
            return list(self._lines)

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop_asked = True
            run = self._run
        if run is not None:
            # Only a child that is being read is stopped. pip, npm and their children go with it.
            run.stop()

    # ---- the run

    def run(self, redo: Iterable[str] = ()) -> None:
        """Do the phases whose result is missing, in order. `redo` names phases to do regardless."""
        app = config.app_dir()
        if app is None or not is_app():
            # Nothing is ever installed into a clone or into the person's own Python.
            raise Refusal(409, "tools are fetched only into an app install", "not_an_app")
        again = [name for name in redo if name in PHASES]
        with self._lock:
            if self._running:
                raise Refusal(409, "the tools are being fetched", "busy")
            self._running = True
            self._stop_asked = False
            self._problem = ""
            self._phase = PHASES[0]
            self._lines = []
            self._measured = None
        end = ("failed", "failed")
        try:
            manifest = load_manifest()
            for name in again:
                self._forget(app, name)
            left = sum(manifest.size(name) - self._on_disk(app, manifest, name) for name in PHASES) / 1e9
            if left > 0 and self._free_gb() < left + HEADROOM_GB:
                raise _Failed("disk")
            for name in PHASES:
                self._enter(name)
                self._phases()[name](app, manifest)
            end = ("done", "")
        except _Stopped:
            end = ("stopped", "")
        except _Failed as failed:
            end = ("failed", failed.problem)
        except Exception as exc:  # noqa: BLE001 - the last stop on this thread
            # The kind of fault for the log, never its message: a path or a line from
            # outside may be in it.
            print(f"app: {type(exc).__name__} while fetching the tools", file=sys.stderr)
            end = ("failed", "disk" if isinstance(exc, OSError) and _is_full(exc) else "failed")
        finally:
            with self._lock:
                if self._stop_asked and end[0] != "done":
                    end = ("stopped", "")
                self._phase, self._problem = end
                self._running = False
                self._run = None
                self._measured = None

    def _phases(self) -> dict[str, Callable[[Path, _Manifest], None]]:
        return {"stack": self._stack, "node": self._node, "renderer": self._renderer, "model": self._model}

    def _enter(self, name: str) -> None:
        with self._lock:
            if self._stop_asked:
                raise _Stopped
            self._phase = name
            self._measured = None

    def _child(self, argv: list[str], env: dict[str, str]) -> tuple[int, str]:
        """Run one child to its end. Returns its exit code and the problem its words name."""
        try:
            run = self._start(argv, env)
        except OSError as exc:
            raise _Failed("failed") from exc
        with self._lock:
            self._run = run
            stop_now = self._stop_asked
        if stop_now:
            # The stop came while the child was starting and found nothing to stop.
            run.stop()
        # The place in _SAID of the gravest thing the child has said. A full disk also
        # looks like a failed download, so the earlier problem wins.
        rank = len(_SAID)
        try:
            for line in run.lines():
                shown = f"{line.kind}: {line.text}" if line.kind else line.text
                lowered = shown.lower()
                for index, (_problem, words) in enumerate(_SAID[:rank]):
                    if any(word in lowered for word in words):
                        rank = index
                        break
                with self._lock:
                    self._lines.append(shown)
                    if len(self._lines) > LINES_KEPT:
                        del self._lines[: len(self._lines) - LINES_KEPT]
            code = run.wait()
        except Exception:
            # A child whose output breaks is still up.
            run.stop()
            raise
        finally:
            with self._lock:
                if self._run is run:
                    self._run = None
        with self._lock:
            if self._stop_asked:
                raise _Stopped
        return code, _SAID[rank][0] if rank < len(_SAID) else ""

    def _ended_badly(self, said: str, by_exit: str = "") -> _Failed:
        try:
            full = self._free_gb() < FULL_DISK_GB
        except OSError:
            full = False
        if full:
            # Whatever the child made of it, a disk with nothing left is the reason.
            return _Failed("disk")
        return _Failed(said or by_exit or "failed")

    # ---- the four phases

    def _stack(self, app: Path, manifest: _Manifest) -> None:
        lock = app / LOCK_NAME
        try:
            digest = _sha256(lock)
        except OSError as exc:
            raise _Failed("failed") from exc
        install = config.app_install() or {}
        if digest != install.get("lock_sha256"):
            # Not the list the installer shipped. Nothing from it is installed.
            raise _Failed("changed")
        marker = app / "python" / STACK_MARKER
        if _text(marker) == digest:
            return
        cache, temp = app / "pip-cache", app / "tmp"
        _remove(temp)
        temp.mkdir(parents=True, exist_ok=True)
        env = {
            # In the app's folder, so that it is counted, found again after a failure,
            # and gone when the phase is done.
            "PIP_CACHE_DIR": str(cache),
            "TEMP": str(temp),
            "TMP": str(temp),
            "TMPDIR": str(temp),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            # Packages the person installed for another Python of theirs are not this one's.
            "PYTHONNOUSERSITE": "1",
        }
        argv = [
            str(_python()), "-m", "pip", "install", "--require-hashes", "--no-input",
            # Wheels only. A package built here would fetch its build tools from the index,
            # and no hash of ours covers those.
            "--only-binary", ":all:",
            "-r", str(lock),
            "--extra-index-url", TORCH_INDEX,
        ]  # fmt: skip
        wheels = app / WHEELS
        if wheels.is_dir():
            # What no index has: the installer brought it, and the lock names its hash.
            argv += ["--find-links", str(wheels)]
        code, said = self._child(argv, env)
        if code != 0:
            raise self._ended_badly(said)
        marker.write_text(digest, encoding="utf-8")
        _remove(cache)
        _remove(temp)

    def _node(self, app: Path, manifest: _Manifest) -> None:
        node = manifest.node
        folder = app / "node"
        if _text(folder / NODE_MARKER) == node.version and (folder / "node.exe").is_file():
            return
        downloads = app / "downloads"
        archive = downloads / "node.zip"

        def arrived(count: int) -> None:
            with self._lock:
                stop = self._stop_asked
                self._measured = None
            if stop:
                raise _Stopped
            if count > node.size:
                # More than the pinned file holds. It is not that file.
                raise _Failed("changed")

        try:
            if not archive.is_file() or archive.stat().st_size != node.size:
                self._fetch(node.url, archive, arrived)
        except _Failed:
            archive.unlink(missing_ok=True)
            raise
        except OSError as exc:
            # What has arrived stays, and the next try goes on from there.
            raise _Failed("disk" if _is_full(exc) else "network") from exc
        try:
            matches = archive.is_file() and _sha256(archive) == node.sha256
        except OSError as exc:
            raise _Failed("failed") from exc
        if not matches:
            # Refused before anything of it is unpacked, and not kept.
            archive.unlink(missing_ok=True)
            raise _Failed("changed")
        partial = app / "node.partial"
        _remove(partial)
        try:
            unpack(archive, partial)
        except (ValueError, zipfile.BadZipFile) as exc:
            _remove(partial)
            archive.unlink(missing_ok=True)
            raise _Failed("failed") from exc
        except OSError as exc:
            _remove(partial)
            raise _Failed("disk" if _is_full(exc) else "failed") from exc
        _remove(folder)
        partial.rename(folder)
        (folder / NODE_MARKER).write_text(node.version, encoding="utf-8")
        _remove(downloads)

    def _renderer(self, app: Path, manifest: _Manifest) -> None:
        root = google.default_render_dir()
        try:
            digest = _sha256(root / "package-lock.json")
        except OSError as exc:
            raise _Failed("failed") from exc
        marker = root / "node_modules" / RENDERER_MARKER
        if _text(marker) == digest and google.renderer_missing(root) is None:
            return
        node = app / "node"
        code, said = self._child(
            [
                # npm's own program, run by the app's Node. npm.cmd would go through cmd.exe,
                # which a folder name with a space in it does not survive unquoted.
                str(node / "node.exe"),
                str(node / "node_modules" / "npm" / "bin" / "npm-cli.js"),
                "ci",
                "--prefix",
                str(root),
                "--no-audit",
                "--no-fund",
            ],
            # The packages' own install steps start `node`, and must find this one.
            config.tool_env(),
        )
        if code != 0:
            raise self._ended_badly(said)
        if marker.parent.is_dir():
            marker.write_text(digest, encoding="utf-8")

    def _model(self, app: Path, manifest: _Manifest) -> None:
        model_revision = pipeline.PINS.get("model_revision", "")
        lora_revision = pipeline.PINS.get("lora_revision", "")
        if not model_revision or not lora_revision:
            # Without a commit the newest files would be fetched, whatever they are.
            raise _Failed("failed")
        code, said = self._child(
            [
                str(_python()), "-c", _MODEL_SCRIPT,
                pipeline.MODEL_ID, model_revision, pipeline.LORA_ID, lora_revision,
            ],  # fmt: skip
            {"HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1", "PYTHONNOUSERSITE": "1"},
        )
        if code != 0:
            raise self._ended_badly(said, _MODEL_EXITS.get(code, ""))
        with self._lock:
            self._model_done = True

    # ---- what is on the disk

    def _forget(self, app: Path, name: str) -> None:
        """Drop a phase's marker, so that the phase is done again."""
        if name == "stack":
            (app / "python" / STACK_MARKER).unlink(missing_ok=True)
        elif name == "node":
            (app / "node" / NODE_MARKER).unlink(missing_ok=True)
        elif name == "renderer":
            (google.default_render_dir() / "node_modules" / RENDERER_MARKER).unlink(missing_ok=True)

    def _on_disk(self, app: Path, manifest: _Manifest, name: str) -> int:
        """How much of a phase is on the disk, in bytes, and never more than the phase holds."""
        size = manifest.size(name)
        if name == "stack":
            marked = _text(app / "python" / STACK_MARKER)
            if marked and marked == _file_sha256(app / LOCK_NAME):
                return size
            # pip holds what it has fetched in its temp folder, and keeps finished wheels
            # in its cache across a failure.
            return min(size, max(_tree_bytes(app / "tmp"), _tree_bytes(app / "pip-cache")))
        if name == "node":
            folder = app / "node"
            if _text(folder / NODE_MARKER) == manifest.node.version and (folder / "node.exe").is_file():
                return size
            archive = app / "downloads" / "node.zip"
            try:
                return min(size, archive.stat().st_size)
            except OSError:
                return 0
        if name == "renderer":
            root = google.default_render_dir()
            if _text(root / "node_modules" / RENDERER_MARKER) and google.renderer_missing(root) is None:
                return size
            return min(size, _tree_bytes(root / "node_modules"))
        with self._lock:
            if self._model_done:
                return size
        cache = commands._hub_cache()
        held = 0
        for repo in (pipeline.MODEL_ID, pipeline.LORA_ID):
            held += _tree_bytes(cache / ("models--" + repo.replace("/", "--")) / "blobs")
        return min(size, held)


def _file_sha256(path: Path) -> str:
    try:
        return _sha256(path)
    except OSError:
        return ""
