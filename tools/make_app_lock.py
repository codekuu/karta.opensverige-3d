"""Write requirements-app.lock: every wheel the model stack needs on Windows, with its hash.

The app page installs the stack with `pip install --require-hashes --only-binary :all:`
against that file. Run this on a Windows machine with Python 3.11, where pip resolves the
stack exactly as it will on a volunteer's machine:

    py -3.11 tools/make_app_lock.py --folder <an empty folder> --out requirements-app.lock

It runs `pip download`, which fetches about 4 GB into the folder and installs nothing.
Remove the folder afterwards.

diffusers is pinned by commit in requirements.txt and is on no index. pip downloads its
source here, and this tool leaves it out: the installer's build makes a wheel of that
commit and adds its line to the lock it ships (see installer/build.ps1).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

# What the redraw is known to run with on Windows (the test PC, 2026-11-24). CUDA builds of
# PyTorch are on PyTorch's own index only.
TORCH = "torch==2.11.0+cu128"
TORCHVISION = "torchvision==0.26.0+cu128"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"

# name-version(-build)?-python-abi-platform.whl
_WHEEL = re.compile(r"(?P<name>[A-Za-z0-9_.]+)-(?P<version>[A-Za-z0-9_.!+]+)(?:-\d[A-Za-z0-9_.]*)?-[^-]+-[^-]+-[^-]+\.whl")
_SOURCE_LEFT_OUT = re.compile(r"diffusers-.+\.(?:zip|tar\.gz)")


def wheel_pin(filename: str) -> tuple[str, str]:
    """The package and version a wheel file is, as a requirement names them."""
    matched = _WHEEL.fullmatch(filename)
    if matched is None:
        raise ValueError(f"{filename} is not a wheel")
    name = re.sub(r"[-_.]+", "-", matched.group("name")).lower()
    return name, matched.group("version")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as held:
        for chunk in iter(lambda: held.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lock_lines(folder: Path) -> tuple[list[str], int, list[str]]:
    """One requirement line for each wheel in the folder, their size in bytes, and what was left out."""
    lines: dict[str, str] = {}
    size = 0
    left_out: list[str] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if _SOURCE_LEFT_OUT.fullmatch(path.name):
            left_out.append(path.name)
            continue
        try:
            name, version = wheel_pin(path.name)
        except ValueError as exc:
            # A package with no wheel would be built on the volunteer's machine, with
            # build tools no hash covers. It has to be dealt with by hand first.
            raise ValueError(f"{path.name} is not a wheel, and the lock takes wheels only") from exc
        if name in lines:
            raise ValueError(f"two versions of {name} in {folder}")
        lines[name] = f"{name}=={version} --hash=sha256:{_sha256(path)}"
        size += path.stat().st_size
    return [lines[name] for name in sorted(lines)], size, left_out


def download_command(folder: Path, requirements: Path) -> list[str]:
    return [
        sys.executable, "-m", "pip", "download",
        "--dest", str(folder),
        "-r", str(requirements),
        TORCH, TORCHVISION,
        "--extra-index-url", TORCH_INDEX,
    ]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--folder", type=Path, required=True, help="an empty folder for the downloaded files")
    parser.add_argument("--out", type=Path, default=root / "requirements-app.lock")
    parser.add_argument("--requirements", type=Path, default=root / "requirements.txt")
    parser.add_argument("--no-download", action="store_true", help="the folder is filled already; only write the lock")
    args = parser.parse_args(argv)
    if sys.platform != "win32" or sys.version_info[:2] != (3, 11):
        print("error: run this with Python 3.11 on Windows, where the stack is installed", file=sys.stderr)
        return 1
    args.folder.mkdir(parents=True, exist_ok=True)
    if not args.no_download:
        if any(args.folder.iterdir()):
            print(f"error: {args.folder} is not empty", file=sys.stderr)
            return 1
        done = subprocess.run(download_command(args.folder, args.requirements), check=False)
        if done.returncode != 0:
            return done.returncode
    try:
        lines, size, left_out = lock_lines(args.folder)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    header = [
        "# Every wheel the model stack needs on Windows with Python 3.11, each with its SHA-256.",
        "# Written by tools/make_app_lock.py from requirements.txt and the two PyTorch pins.",
        "# Do not edit by hand. The app page installs from it with:",
        "#   pip install --require-hashes --only-binary :all: -r requirements-app.lock",
        "# diffusers is not listed: the installer carries a wheel of its pinned commit and adds",
        "# that wheel's line to the copy it ships (installer/build.ps1).",
    ]
    args.out.write_text("\n".join([*header, *lines]) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote: {args.out.name} with {len(lines)} wheels")
    print(f"size: {size} bytes")
    for name in left_out:
        print(f"left out: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
