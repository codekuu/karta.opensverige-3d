"""Only the parts a redraw phase uses sit on the GPU, so a 24 GB card is enough."""

from __future__ import annotations

import unittest

from rutor.redraw_core import _place


class _Part:
    def __init__(self) -> None:
        self.device = "cuda"

    def to(self, device: str) -> "_Part":
        self.device = device
        return self


class _Pipe:
    def __init__(self) -> None:
        self.transformer = _Part()
        self.text_encoder = _Part()
        self.vae = _Part()


class PlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipe = _Pipe()
        self.cleared = 0

    def clear(self) -> None:
        self.cleared += 1

    def devices(self) -> tuple[str, str, str]:
        return self.pipe.text_encoder.device, self.pipe.transformer.device, self.pipe.vae.device

    def test_encode_keeps_the_transformer_off_the_gpu(self) -> None:
        _place(self.pipe, "encode", empty_cache=self.clear)
        self.assertEqual(self.devices(), ("cuda", "cpu", "cuda"))
        self.assertEqual(self.cleared, 1)

    def test_denoise_keeps_the_text_encoder_off_the_gpu(self) -> None:
        _place(self.pipe, "denoise", empty_cache=self.clear)
        self.assertEqual(self.devices(), ("cpu", "cuda", "cuda"))

    def test_decode_keeps_only_the_vae(self) -> None:
        _place(self.pipe, "decode", empty_cache=self.clear)
        self.assertEqual(self.devices(), ("cpu", "cpu", "cuda"))

    def test_unknown_phase_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _place(self.pipe, "train", empty_cache=self.clear)


if __name__ == "__main__":
    unittest.main()
