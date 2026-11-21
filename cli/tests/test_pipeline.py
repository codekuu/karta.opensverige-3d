"""Pipeline pins, the prompt, and the pure redraw geometry."""

from __future__ import annotations

import unittest

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
        self.assertEqual(
            check_pins({"model_revision": "abc", "lora_sha256": "def", "diffusers_commit": "fff"}),
            [],
        )

    def test_check_pins_reports_mismatch(self) -> None:
        previous = PINS["model_revision"]
        PINS["model_revision"] = "pinned-hash"
        try:
            self.assertEqual(
                check_pins({"model_revision": "other", "lora_sha256": "", "diffusers_commit": ""}),
                ["model_revision"],
            )
        finally:
            PINS["model_revision"] = previous

    def test_redraw_core_pure_functions(self) -> None:
        self.assertEqual(pad_size(1536), 1536)
        self.assertEqual(len(get_views(1536, 1536)), 4)
        self.assertEqual(len(window_token_indices(1536, 1536, 0, 0, window=1024, scale=16)), 4096)
