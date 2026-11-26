"""Pipeline pins, the prompt, and the pure redraw geometry."""

from __future__ import annotations

import unittest

from unittest.mock import patch

from rutor.pipeline import PINS, check_pins, prompt_for
from rutor.redraw_core import get_views, pad_size, window_token_indices

PROMPT = (
    "Repaint this 3D map render as mndfstyle isometric pixel-art map of "
    "Göteborg, Sweden, keeping every road, building and tree in place"
)


class PipelineTests(unittest.TestCase):
    def test_prompt_for_matches_old_script(self) -> None:
        self.assertEqual(prompt_for("Göteborg"), PROMPT)

    def test_check_pins_ignores_empty_pins(self) -> None:
        with patch.dict(PINS, {"model_revision": "", "lora_revision": "", "diffusers_version": ""}, clear=True):
            self.assertEqual(
                check_pins({"model_revision": "abc", "lora_revision": "def", "diffusers_version": "fff"}),
                [],
            )

    def test_check_pins_reports_mismatch(self) -> None:
        with patch.dict(PINS, {"model_revision": "pinned-hash", "lora_revision": "", "diffusers_version": ""}, clear=True):
            self.assertEqual(
                check_pins({"model_revision": "other", "lora_revision": "", "diffusers_version": ""}),
                ["model_revision"],
            )

    def test_redraw_core_pure_functions(self) -> None:
        self.assertEqual(pad_size(1536), 1536)
        self.assertEqual(len(get_views(1536, 1536)), 4)
        self.assertEqual(len(window_token_indices(1536, 1536, 0, 0, window=1024, scale=16)), 4096)


class LoadingBarTests(unittest.TestCase):
    """Read through a pipe, the bars for loading the model are hundreds of lines."""

    def _run(self, terminal: bool) -> list[str]:
        import sys
        import types
        from unittest.mock import patch

        from rutor import redraw_core

        asked: list[str] = []
        fakes = {}
        for name in ("diffusers.utils.logging", "transformers.utils.logging"):
            module = types.ModuleType(name)
            setattr(module, "disable_progress_bar", lambda name=name: asked.append(name))
            fakes[name] = module

        class _Stream:
            def isatty(self) -> bool:
                return terminal

        with patch.dict(sys.modules, fakes), patch.object(redraw_core.sys, "stderr", _Stream()):
            redraw_core._quiet_loading_bars()
        return asked

    def test_a_pipe_gets_no_loading_bars(self) -> None:
        self.assertEqual(self._run(terminal=False), ["diffusers.utils.logging", "transformers.utils.logging"])

    def test_a_person_at_a_terminal_keeps_them(self) -> None:
        self.assertEqual(self._run(terminal=True), [])
