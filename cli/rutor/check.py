"""Quality check for a redrawn cell, run before anything is uploaded.

A redraw passes when it has the source's size, is not blank, black, white or
posterised, differs from the source (it was actually redrawn), and lines up with
the source. Alignment is skipped for a source with almost no edges (open water). Every limit is a named constant so calibration changes one line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image

SIDE = 1536
CORE = 1024
MIN_BRIGHTNESS_SPREAD = 8.0
MAX_NEAR_BLACK = 0.05
MAX_NEAR_WHITE = 0.05
NEAR_BLACK = 10
NEAR_WHITE = 245
MIN_COLOURS = 2000
MIN_MEAN_DIFFERENCE = 6.0
ALIGN_SCALE = 512
MAX_SHIFT_PX = 4
# Calibrated 2026-11-19 on 100 L4 redraws: correct pairs 0.088 and up, the wrong
# cell 0.011 to 0.018. The limit sits in the gap.
MIN_MATCH_PEAK = 0.04
# Share of the source core with a clear edge (Sobel above EDGE at 512 px). Open water
# has almost none, so there is nothing to align against: 0.020 for a harbour cell,
# 0.039 and up for every cell where the alignment test held.
EDGE = 60.0
MIN_STRUCTURE = 0.03


def _rgb(image: "Image.Image") -> "np.ndarray":
    import numpy as np

    return np.asarray(image.convert("RGB"))


def _core(array: "np.ndarray") -> "np.ndarray":
    start = (SIDE - CORE) // 2
    return array[start : start + CORE, start : start + CORE]


def _luminance(array: "np.ndarray") -> "np.ndarray":
    import numpy as np

    return array.astype(np.float64) @ np.array((0.299, 0.587, 0.114))


def _gradient(array: "np.ndarray") -> "np.ndarray":
    """Sobel gradient magnitude of the core, downscaled for the alignment test."""
    import numpy as np
    from PIL import Image

    small = Image.fromarray(_core(array)).resize((ALIGN_SCALE, ALIGN_SCALE), Image.Resampling.BOX)
    lum = _luminance(np.asarray(small))
    padded = np.pad(lum, 1, mode="edge")
    gx = (
        padded[:-2, 2:] + 2 * padded[1:-1, 2:] + padded[2:, 2:]
        - padded[:-2, :-2] - 2 * padded[1:-1, :-2] - padded[2:, :-2]
    )
    gy = (
        padded[2:, :-2] + 2 * padded[2:, 1:-1] + padded[2:, 2:]
        - padded[:-2, :-2] - 2 * padded[:-2, 1:-1] - padded[:-2, 2:]
    )
    return np.hypot(gx, gy)


def _shift(source: "np.ndarray", redrawn: "np.ndarray") -> tuple[int, int, float]:
    """Phase correlation: how far the redraw sits from the source, at full size, and the peak height."""
    import numpy as np

    window = np.outer(np.hanning(ALIGN_SCALE), np.hanning(ALIGN_SCALE))
    a = np.fft.fft2((_gradient(source) - _gradient(source).mean()) * window)
    b = np.fft.fft2((_gradient(redrawn) - _gradient(redrawn).mean()) * window)
    cross = b * np.conj(a)
    magnitude = np.abs(cross)
    if not np.any(magnitude > 1e-12):
        return 0, 0, 0.0
    surface = np.real(np.fft.ifft2(cross / np.maximum(magnitude, 1e-12)))
    peak_y, peak_x = np.unravel_index(int(np.argmax(surface)), surface.shape)
    dy = peak_y - ALIGN_SCALE if peak_y > ALIGN_SCALE // 2 else peak_y
    dx = peak_x - ALIGN_SCALE if peak_x > ALIGN_SCALE // 2 else peak_x
    factor = CORE // ALIGN_SCALE
    return int(dx) * factor, int(dy) * factor, float(surface[peak_y, peak_x])


def measure(source: "Image.Image", redrawn: "Image.Image") -> dict[str, float]:
    """Every value the check looks at. Sizes must already match."""
    import numpy as np

    src = _rgb(source)
    out = _rgb(redrawn)
    lum = _luminance(out)
    core = _core(out)
    packed = (core[..., 0].astype(np.int64) << 16) | (core[..., 1].astype(np.int64) << 8) | core[..., 2]
    dx, dy, peak = _shift(src, out)
    structure = float((_gradient(src) > EDGE).mean())
    return {
        "structure": structure,
        "brightness_spread": float(lum.std()),
        "near_black": float((lum < NEAR_BLACK).mean()),
        "near_white": float((lum > NEAR_WHITE).mean()),
        "colours": float(np.unique(packed).size),
        "mean_difference": float(np.abs(core.astype(np.int16) - _core(src).astype(np.int16)).mean()),
        "shift_x": float(dx),
        "shift_y": float(dy),
        "match_peak": peak,
    }


def problems(source: "Image.Image", redrawn: "Image.Image") -> list[str]:
    """One sentence per failed check. An empty list means the redraw may be uploaded."""
    if source.size != (SIDE, SIDE) or redrawn.size != source.size:
        return [
            f"wrong size: the redraw is {redrawn.size[0]}x{redrawn.size[1]} and the source "
            f"{source.size[0]}x{source.size[1]}, both must be {SIDE}x{SIDE}"
        ]
    values = measure(source, redrawn)
    found: list[str] = []
    if values["brightness_spread"] < MIN_BRIGHTNESS_SPREAD:
        found.append(f"blank: brightness spread {values['brightness_spread']:.1f} (limit {MIN_BRIGHTNESS_SPREAD:.0f})")
    if values["near_black"] >= MAX_NEAR_BLACK:
        found.append(f"too dark: {values['near_black']:.0%} of pixels are near black (limit {MAX_NEAR_BLACK:.0%})")
    if values["near_white"] >= MAX_NEAR_WHITE:
        found.append(f"too bright: {values['near_white']:.0%} of pixels are near white (limit {MAX_NEAR_WHITE:.0%})")
    if values["colours"] < MIN_COLOURS:
        found.append(f"too few colours: {values['colours']:.0f} in the centre (limit {MIN_COLOURS})")
    if values["mean_difference"] < MIN_MEAN_DIFFERENCE:
        found.append(
            f"not redrawn: mean difference {values['mean_difference']:.1f} from the source "
            f"(limit {MIN_MEAN_DIFFERENCE:.0f})"
        )
    if values["structure"] < MIN_STRUCTURE:
        # Mostly water: alignment cannot be measured, the other checks still apply.
        return found
    if abs(values["shift_x"]) > MAX_SHIFT_PX or abs(values["shift_y"]) > MAX_SHIFT_PX:
        found.append(
            f"shifted: {abs(values['shift_x']):.0f} px east-west and {abs(values['shift_y']):.0f} px "
            f"north-south against the source (limit {MAX_SHIFT_PX} px)"
        )
    if values["match_peak"] < MIN_MATCH_PEAK:
        found.append(f"no match with the source: peak {values['match_peak']:.3f} (limit {MIN_MATCH_PEAK})")
    return found
