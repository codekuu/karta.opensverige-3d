"""The quality check that every redrawn cell passes before upload."""

from __future__ import annotations

import unittest


def _available() -> bool:
    try:
        import numpy
        from PIL import Image
    except ImportError:
        return False
    del numpy, Image
    return True


AVAILABLE = _available()


def _scene(seed: int = 3) -> "object":
    """A 1536 px source with blocks, streets and texture, like a city render."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    image = np.full((1536, 1536, 3), 120, dtype=np.int16)
    for _ in range(140):
        x, y = rng.integers(0, 1440, 2)
        w, h = rng.integers(30, 160, 2)
        image[y : y + h, x : x + w] = rng.integers(40, 230, 3)
    image += rng.integers(-12, 13, image.shape)
    return Image.fromarray(np.clip(image, 0, 255).astype(np.uint8))


def _redrawn(source: "object") -> "object":
    import numpy as np
    from PIL import Image, ImageFilter

    shifted = np.asarray(source).astype(np.int16) + np.array((25, -15, 20), dtype=np.int16)
    image = Image.fromarray(np.clip(shifted, 0, 255).astype(np.uint8))
    return image.filter(ImageFilter.GaussianBlur(1.2))


@unittest.skipUnless(AVAILABLE, "pillow and numpy are not installed")
class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _scene()
        self.good = _redrawn(self.source)

    def problems(self, redrawn: "object") -> list[str]:
        from rutor.check import problems

        return problems(self.source, redrawn)

    def test_good_redraw_passes(self) -> None:
        self.assertEqual(self.problems(self.good), [])

    def test_black_redraw_fails_blank_and_black(self) -> None:
        from PIL import Image

        found = " ".join(self.problems(Image.new("RGB", (1536, 1536), (0, 0, 0))))
        self.assertIn("blank", found)
        self.assertIn("near black", found)

    def test_flat_grey_fails_blank(self) -> None:
        from PIL import Image

        found = " ".join(self.problems(Image.new("RGB", (1536, 1536), (128, 128, 128))))
        self.assertIn("blank", found)

    def test_shifted_redraw_fails_alignment(self) -> None:
        import numpy as np
        from PIL import Image

        rolled = Image.fromarray(np.roll(np.asarray(self.good), 20, axis=1))
        found = " ".join(self.problems(rolled))
        self.assertIn("shifted", found)
        self.assertIn("20", found)

    def test_source_passed_through_fails(self) -> None:
        found = " ".join(self.problems(self.source))
        self.assertIn("not redrawn", found)

    def test_wrong_size_fails(self) -> None:
        found = " ".join(self.problems(self.good.resize((1024, 1024))))
        self.assertIn("size", found)

    def test_redraw_of_another_cell_fails_match(self) -> None:
        found = " ".join(self.problems(_redrawn(_scene(seed=11))))
        self.assertIn("no match", found)

    def test_open_water_without_structure_skips_the_alignment_test(self) -> None:
        import numpy as np
        from PIL import Image

        from rutor.check import problems

        rng = np.random.default_rng(5)
        water = np.clip(np.full((1536, 1536, 3), (30, 45, 35)) + rng.integers(-3, 4, (1536, 1536, 3)), 0, 255)
        source = Image.fromarray(water.astype(np.uint8))
        blue = np.clip(np.full((1536, 1536, 3), (20, 90, 140)) + rng.integers(-30, 31, (1536, 1536, 3)), 0, 255)
        self.assertEqual(problems(source, Image.fromarray(blue.astype(np.uint8))), [])

    def test_posterised_redraw_fails(self) -> None:
        found = " ".join(self.problems(self.good.quantize(16).convert("RGB")))
        self.assertIn("colours", found)


if __name__ == "__main__":
    unittest.main()
