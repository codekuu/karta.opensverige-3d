"""What the Pixelbygget shortcut starts: the app page, without a terminal window.

The shortcut runs this file with pythonw.exe, which has no console. It starts the app's own
python.exe once, with a console that has no window, and ends. Everything the app starts
later (the graphics driver's tool, Node, pip) inherits that console, so no black window
ever opens on the person's screen.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

# CREATE_NO_WINDOW: a console program gets a console that is not shown.
NO_WINDOW = 0x08000000
# CREATE_NEW_PROCESS_GROUP: the app does not end with whatever started the shortcut.
NEW_GROUP = 0x00000200


def command(app: Path) -> list[str]:
    return [str(app / "python" / "python.exe"), "-m", "rutor", "app", "--quiet"]


def environment(app: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """The person's environment, without what would point the app's Python at another one."""
    made = dict(environ)
    made.pop("PYTHONHOME", None)
    # The app's own code, and nothing the person installed for a Python of their own.
    made["PYTHONPATH"] = str(app / "cli")
    made["PYTHONNOUSERSITE"] = "1"
    return made


def _say(text: str) -> None:
    """One sentence in a box. There is no terminal to print it in."""
    if os.name != "nt":
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, text, "Pixelbygget", 0x10)  # type: ignore[attr-defined]


def main() -> int:
    app = Path(__file__).resolve().parent
    try:
        subprocess.Popen(
            command(app),
            cwd=str(app),
            env=environment(app, os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(NO_WINDOW | NEW_GROUP) if os.name == "nt" else 0,
        )
    except OSError:
        _say("Pixelbygget kunde inte starta. Installera appen igen.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
