"""Narrow, testable text-line extraction for line-level OCR.

Why this exists
---------------
``microsoft/trocr-base-handwritten`` is, per its model card, a model for
"optical character recognition (OCR) on **single text-line images**". Feeding it
a whole examination page would be a category error: it would emit one string
for a crop it was never trained to read, and that string would *look* like a
transcription of the page. This module therefore produces the line crops the
model actually needs — and, just as importantly, produces **nothing** when it
cannot find credible text lines, so the pipeline reports a limitation instead
of inventing geometry.

Strategy: horizontal projection profiling
-----------------------------------------
Pure Pillow (a base dependency — no numpy, no OpenCV, no ML stack), so line
extraction works in ordinary CI and in a base install:

  1. Grayscale, then Otsu-threshold into a binary ink/background image.
  2. Row ink density via a C-level ``resize((1, h), BOX)`` on the binary image
     (BOX averages, so the result is the per-row ink fraction scaled to 255).
  3. Contiguous above-threshold row runs become candidate bands; bands separated
     by a small gap are merged (a single line can have a locally blank row).
  4. For each band, column extent comes from the same trick transposed.
  5. Tiny bands are dropped: they are ruled lines, underlines, or noise, not
     handwriting. Bands are capped by ``max_regions``.

Documented limitations (this is a boundary, not a claim of layout understanding)
  * Assumes upright, roughly horizontal, single-column text. Rotated, skewed,
    multi-column, table, and diagram content is **not** handled.
  * Ruled table lines and underlines can survive as bands only if they are
    taller than ``MIN_BAND_HEIGHT_PX``; short ones are filtered.
  * Reading order is top-to-bottom by band position only; it does not reason
    about columns or question numbering.
A future layout-detection component should replace this module wholesale while
leaving :func:`map_box_to_original` and the OCR contract untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image

# Bands shorter than this (in working-image pixels) are ruled lines/noise.
MIN_BAND_HEIGHT_PX = 6
# Vertical padding added around a band to catch ascenders/descenders.
BAND_PAD_PX = 2
# Horizontal padding added around a band's ink extent.
BAND_PAD_X_PX = 4
# Rows closer than this are merged into one line band.
MERGE_GAP_PX = 12
# Columns must exceed this ink fraction to count as part of a text line.
COLUMN_INK_RATIO = 0.001


@dataclass(frozen=True)
class LineBox:
    """A detected text-line box, in **working-image** pixel coordinates."""

    left: int
    top: int
    width: int
    height: int

    @property
    def bottom(self) -> int:
        return self.top + self.height


def _otsu_threshold(histogram: list[int]) -> int:
    """Otsu's method: pick the threshold maximizing between-class variance.

    Deterministic and dependency-free. Falls back to 127 for a degenerate
    (single-value) histogram so an all-white or all-black page still yields a
    stable binary image rather than an exception.
    """
    total = sum(histogram)
    if total == 0:
        return 127
    sum_all = sum(i * count for i, count in enumerate(histogram))
    sum_b = 0.0
    weight_b = 0
    best_t = 127
    best_var = -1.0
    for t in range(256):
        weight_b += histogram[t]
        if weight_b == 0:
            continue
        weight_f = total - weight_b
        if weight_f == 0:
            break
        sum_b += t * histogram[t]
        mean_b = sum_b / weight_b
        mean_f = (sum_all - sum_b) / weight_f
        var = weight_b * weight_f * (mean_b - mean_f) ** 2
        if var > best_var:
            best_var = var
            best_t = t
    return best_t


def _binary_ink(img: Image.Image) -> Optional[Image.Image]:
    """Binarize to ink=255/background=0, or None if the page has no contrast."""
    gray = img.convert("L")
    histogram = gray.histogram()
    # Derive the occupied range from the histogram (a plain list of ints) so
    # the contrast test is both correctly typed and shared with Otsu below.
    occupied = [value for value, count in enumerate(histogram) if count > 0]
    if not occupied:
        return None
    if occupied[-1] - occupied[0] < 2:
        # Effectively uniform: nothing to segment. Caller reports blank/no text.
        return None
    threshold = _otsu_threshold(histogram)
    # point() maps ink (<= threshold) to 255, background to 0.
    lut = [255 if i <= threshold else 0 for i in range(256)]
    return gray.point(lut)


def _row_densities(ink: Image.Image, width: int, height: int) -> list[float]:
    """Per-row ink fraction in [0, 1], computed at C level via BOX resize."""
    column = ink.resize((1, height))
    return [value / 255.0 for value in column.getdata()]


def _column_densities(ink: Image.Image, box: tuple[int, int, int, int]) -> list[float]:
    """Per-column ink fraction for a cropped band ``(left, top, right, bottom)``."""
    left, top, right, bottom = box
    band = ink.crop((left, top, right, bottom))
    width = right - left
    row = band.resize((width, 1))
    return [value / 255.0 for value in row.getdata()]


def _merge_runs(starts_ends: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge row runs separated by fewer than ``MERGE_GAP_PX`` blank rows."""
    if not starts_ends:
        return []
    ordered = sorted(starts_ends)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start - prev_end <= MERGE_GAP_PX:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def extract_text_lines(
    img: Image.Image,
    *,
    min_ink_ratio: float,
    max_regions: int,
) -> list[LineBox]:
    """Detect horizontal text-line bands in a working page image.

    Returns boxes in **working-image** coordinates, ordered top-to-bottom
    (reading order for single-column text). An empty list means no credible
    text line was found — a blank page, a uniformly shaded scan, or content
    this strategy cannot segment. It never returns guessed geometry.

    ``min_ink_ratio`` is the minimum fraction of dark pixels a row must contain
    to belong to a text line; raising it filters faint noise, lowering it
    admits lighter handwriting (and more false bands).
    """
    if max_regions < 1:
        return []
    ink = _binary_ink(img)
    if ink is None:
        return []
    width, height = ink.size
    if width < 2 or height < 2:
        return []

    densities = _row_densities(ink, width, height)
    runs: list[tuple[int, int]] = []
    active_start: Optional[int] = None
    for y, density in enumerate(densities):
        if density >= min_ink_ratio:
            if active_start is None:
                active_start = y
        else:
            if active_start is not None:
                runs.append((active_start, y))
                active_start = None
    if active_start is not None:
        runs.append((active_start, height))

    merged = _merge_runs(runs)

    boxes: list[LineBox] = []
    for start, end in merged:
        if end - start < MIN_BAND_HEIGHT_PX:
            # Ruled line, underline, or speckle — not a handwriting line.
            continue
        top = max(0, start - BAND_PAD_PX)
        bottom = min(height, end + BAND_PAD_PX)
        cols = _column_densities(ink, (0, top, width, bottom))
        ink_cols = [x for x, value in enumerate(cols) if value > COLUMN_INK_RATIO]
        if not ink_cols:
            continue
        left = max(0, min(ink_cols) - BAND_PAD_X_PX)
        right = min(width, max(ink_cols) + 1 + BAND_PAD_X_PX)
        box_width = right - left
        box_height = bottom - top
        if box_width < 2 or box_height < 2:
            continue
        boxes.append(LineBox(left=left, top=top, width=box_width, height=box_height))
        if len(boxes) >= max_regions:
            break
    return boxes


def map_box_to_original(
    box: LineBox,
    *,
    working_scale: float,
    original_width: int,
    original_height: int,
) -> Optional[tuple[int, int, int, int]]:
    """Map a working-space box to original-image pixels.

    ``working_scale`` is ``working/original`` (always <= 1: preprocessing only
    downscales), so the inverse is a division. Rounding is outward-safe on the
    far edges via an explicit clamp, guaranteeing the result lies inside the
    original page bounds — a mapped box is never allowed to hang off the page.

    Returns ``(left, top, width, height)`` or ``None`` if the box collapses to
    zero area after mapping (degenerate input).
    """
    if working_scale <= 0:
        return None
    if original_width < 1 or original_height < 1:
        return None

    left = int(box.left / working_scale + 0.5)
    top = int(box.top / working_scale + 0.5)
    right = int((box.left + box.width) / working_scale + 0.5)
    bottom = int((box.top + box.height) / working_scale + 0.5)

    left = max(0, min(left, original_width - 1))
    top = max(0, min(top, original_height - 1))
    right = max(left + 1, min(right, original_width))
    bottom = max(top + 1, min(bottom, original_height))

    mapped_width = right - left
    mapped_height = bottom - top
    if mapped_width < 1 or mapped_height < 1:
        return None
    return left, top, mapped_width, mapped_height
