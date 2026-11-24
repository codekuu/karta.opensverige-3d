"""The app page: building a cell from a page on this machine, without a terminal."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from rutor import config
from rutor.errors import RutorError
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


if __name__ == "__main__":
    unittest.main()
