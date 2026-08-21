"""Text recognition backends.

`Recognizer` is a small interface so today's engine (EasyOCR) can later be
swapped -- per region, if needed -- for a faster template-matching backend
without touching calibration, capture, or the overlay. Approach: ship with
OCR, measure real latency, only upgrade the regions that actually need it.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

import cv2
import numpy as np

# A comma is a thousands separator (part of one number) only when followed
# by exactly three digits, e.g. "1,234" or "12,345,678.90". Anywhere else --
# e.g. "12, 34", or "12,34" (only two digits after the comma) -- it's a
# separator *between* two distinct numbers, not one. First branch matches
# proper thousands-grouping as a single token; second is the plain-number
# fallback used for everything else.
_NUMERIC_RE = re.compile(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+\.?\d*")
_BASE_CHARS = "0123456789.,-+%$"


@dataclass
class Reading:
    raw_text: str
    value: object  # float | str | None -- the first number found, or a matched label
    values: list  # every number found, in order (e.g. "12, 34" -> [12.0, 34.0])
    ok: bool
    lines: list  # one entry per detected text line/row in the crop


class Recognizer:
    """Interface: anything with a `.read(image, region) -> Reading` method."""

    def read(self, image: np.ndarray, region) -> Reading:
        raise NotImplementedError


def _split_lines(image: np.ndarray, min_gap: int = 3, min_line_height: int = 4) -> list:
    """Split a crop into separate horizontal text-line bands.

    With the detector network skipped (see `EasyOCRRecognizer.read`), EasyOCR's
    recognizer expects exactly one line of text per call -- it scans strictly
    left-to-right. Feed it a crop with two lines stacked vertically and it
    doesn't error, it *hallucinates*: garbled output that looks plausible but
    isn't what's on screen. This finds each line's row-band via a horizontal
    ink-projection profile so each one can be recognized on its own.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Otsu splits pixels into two classes without knowing which one is text.
    # Assume whichever class covers *less* of the crop is the text (a value
    # is normally a small minority of pixels against a larger background),
    # regardless of whether the text itself is lighter or darker than its
    # background -- so this works for both light-on-dark and dark-on-light.
    if np.count_nonzero(thresh) > thresh.size / 2:
        thresh = 255 - thresh
    row_has_ink = thresh.sum(axis=1) > 0

    bands = []
    start = None
    for y, has_ink in enumerate(row_has_ink):
        if has_ink and start is None:
            start = y
        elif not has_ink and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, len(row_has_ink)))

    # merge bands separated by a tiny gap (anti-aliasing can split one visual
    # line into several ink bands) and drop specks too short to be real text
    merged = []
    for s, e in bands:
        if merged and s - merged[-1][1] <= min_gap:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    merged = [(s, e) for s, e in merged if e - s >= min_line_height]

    if not merged:
        return [image]  # nothing detected -- fall back to the whole crop as-is

    # Two things a CRNN recognizer is sensitive to, both handled here:
    # 1. Vertical tightness clips ascenders/descenders at a *deterministic*
    #    pixel boundary -- a real hallucinated character every time that
    #    exact crop lands on a bad edge, not random noise. Pad proportionally
    #    to each line's own height (capped against neighboring lines/bounds).
    # 2. A wide crop with only a little actual text and lots of blank
    #    background on either side turns out to be the *bigger* source of a
    #    spurious leading/trailing character -- confirmed by testing tight
    #    vertical-only crops (still hallucinated) against tight vertical+
    #    horizontal crops (clean) on identical content. So also crop
    #    horizontally to each line's own ink, not the full original width.
    h, w = image.shape[:2]
    lines = []
    for i, (s, e) in enumerate(merged):
        pad_y = max(4, int((e - s) * 0.6))
        top_limit = merged[i - 1][1] if i > 0 else 0
        bottom_limit = merged[i + 1][0] if i < len(merged) - 1 else h
        y0, y1 = max(s - pad_y, top_limit), min(e + pad_y, bottom_limit)

        cols = np.where(thresh[s:e].sum(axis=0) > 0)[0]
        if len(cols):
            x0c, x1c = cols.min(), cols.max() + 1
            pad_x = max(4, int((x1c - x0c) * 0.15))
            x0, x1 = max(x0c - pad_x, 0), min(x1c + pad_x, w)
        else:
            x0, x1 = 0, w

        lines.append(image[y0:y1, x0:x1])
    return lines


def _has_wide_gap(image: np.ndarray, gap_factor: float = 2.5) -> bool:
    """Detect a wide blank horizontal gap within what's otherwise a single
    text line -- e.g. a label on the left and a value far to the right.
    The fast single-line recognizer has no positional awareness -- it
    collapses any gap, big or small, to whatever spacing its own language
    model feels like (usually one space). This flags that case so `read()`
    can route it through the detector path instead, which reports real box
    positions and can reflect the actual gap.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.count_nonzero(thresh) > thresh.size / 2:
        thresh = 255 - thresh
    col_has_ink = thresh.sum(axis=0) > 0

    gaps = []
    gap_start = None
    for x, has_ink in enumerate(col_has_ink):
        if not has_ink and gap_start is None:
            gap_start = x
        elif has_ink and gap_start is not None:
            gaps.append(x - gap_start)
            gap_start = None
    if not gaps:
        return False

    char_w = max(4.0, image.shape[0] * 0.5)  # rough estimate from the crop's own height
    return max(gaps) > char_w * gap_factor


def _join_row(ordered_items: list) -> str:
    """Join same-row (bbox, text) items left-to-right with spacing
    proportional to each item's actual pixel gap from the previous one,
    instead of always inserting exactly one space -- so a label far to the
    left of a value stays visibly far apart, matching the real layout."""
    if not ordered_items:
        return ""

    heights = [max(pt[1] for pt in bbox) - min(pt[1] for pt in bbox) for bbox, _ in ordered_items]
    char_w = max(4.0, (sum(heights) / len(heights)) * 0.5)

    parts = [ordered_items[0][1]]
    prev_end = max(pt[0] for pt in ordered_items[0][0])
    for bbox, text in ordered_items[1:]:
        x0 = min(pt[0] for pt in bbox)
        gap = max(0, x0 - prev_end)
        parts.append(" " * max(1, round(gap / char_w)))
        parts.append(text)
        prev_end = max(pt[0] for pt in bbox)
    return "".join(parts)


def _preprocess(image: np.ndarray) -> np.ndarray:
    """Upscale + binarize a small crop so OCR has more to work with.

    Small crops (a handful of characters) are exactly what general OCR models
    are worst at; upscaling and thresholding closes most of that gap cheaply,
    before any recognition model even runs.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def _parse_numbers(text: str) -> list:
    """Extract every number in the text, in order -- not just the first.
    A single value's raw_text/lines already show comma-separated numbers
    correctly (that's plain text, untouched here); this is specifically
    about not silently dropping the rest when something needs actual
    numbers, e.g. a future formula reading two values out of one region."""
    values = []
    for match in _NUMERIC_RE.finditer(text):
        try:
            values.append(float(match.group().replace(",", "")))
        except ValueError:
            pass
    return values


def _parse(raw_text: str, labels: list[str]) -> tuple[object, bool, list]:
    """Try to read the text as one or more numbers first, then as one of
    the known fixed labels (fuzzy-matched, since OCR on a tiny crop is noisy)."""
    cleaned = raw_text.strip()
    numbers = _parse_numbers(cleaned)
    if numbers:
        return numbers[0], True, numbers
    if labels:
        best = difflib.get_close_matches(cleaned.upper(), [l.upper() for l in labels], n=1, cutoff=0.6)
        if best:
            return best[0], True, []
    return None, False, []


class EasyOCRRecognizer(Recognizer):
    def __init__(self, gpu: bool = False, extra_labels: list[str] | None = None):
        import easyocr  # deferred: this is the slow-loading dependency

        self._reader = easyocr.Reader(["en"], gpu=gpu, verbose=False)
        self._global_labels = extra_labels or []

    def _recognize_one_line(self, line_image: np.ndarray, labels: list) -> str:
        prepped = _preprocess(line_image)
        # `.recognize()` instead of `.readtext()`: skips EasyOCR's text-*detection*
        # network entirely and runs only the recognizer on this one line.
        # Calibration already tells us exactly where the text is, so the detector
        # pass is pure overhead here -- measured ~7.4x faster on this machine
        # (138ms -> 19ms per region) with no accuracy cost on a pre-cropped region.
        if labels:
            # A known, small vocabulary was configured for this region -- safe
            # to restrict recognition to just that (better numeric accuracy).
            allowlist = _BASE_CHARS + "".join(sorted({c for l in labels for c in l.upper()}))
            results = self._reader.recognize(prepped, allowlist=allowlist)
        else:
            # No vocabulary configured (the default right now) -- an allowlist
            # here would make EasyOCR physically unable to output a letter, so
            # any real text gets force-mapped onto the nearest allowed digit/
            # symbol instead of read correctly. Stay unrestricted instead.
            results = self._reader.recognize(prepped)
        return " ".join(text for _, text, _ in results).strip()

    def _recognize_multiline(self, image: np.ndarray, labels: list) -> list:
        """Full detector+recognizer pipeline: the detector is specifically
        built to find and crop multiple distinct text regions correctly.
        Our own fast line-splitting (tight per-line crops fed to the
        detector-skip recognizer) got most cases right but still had rare
        one-character hallucinations on some content -- worth the latency
        here since this path only runs when multiple lines were detected,
        not on every region every cycle."""
        prepped = _preprocess(image)
        if labels:
            allowlist = _BASE_CHARS + "".join(sorted({c for l in labels for c in l.upper()}))
            results = self._reader.readtext(prepped, allowlist=allowlist, detail=1, paragraph=False)
        else:
            results = self._reader.readtext(prepped, detail=1, paragraph=False)
        if not results:
            return [""]

        # The detector finds per-word/text-instance boxes, not full merged
        # lines -- e.g. "UP" and "12" on the same visual row can come back
        # as two separate boxes. Group boxes into rows by vertical-center
        # proximity ourselves, then sort each row left-to-right.
        def y_center(bbox: list) -> float:
            ys = [pt[1] for pt in bbox]
            return (min(ys) + max(ys)) / 2

        def box_height(bbox: list) -> float:
            ys = [pt[1] for pt in bbox]
            return max(ys) - min(ys)

        items = sorted(results, key=lambda r: y_center(r[0]))
        rows: list = []  # each: {"yc": float, "items": [(bbox, text), ...]}
        for bbox, text, _conf in items:
            yc = y_center(bbox)
            h = box_height(bbox)
            if rows and abs(yc - rows[-1]["yc"]) < max(h, 1) * 0.6:
                rows[-1]["items"].append((bbox, text))
                n = len(rows[-1]["items"])
                rows[-1]["yc"] += (yc - rows[-1]["yc"]) / n  # running average
            else:
                rows.append({"yc": yc, "items": [(bbox, text)]})

        lines = []
        for row in rows:
            ordered = sorted(row["items"], key=lambda it: min(pt[0] for pt in it[0]))  # left-to-right
            lines.append(_join_row(ordered))
        return lines

    def read(self, image: np.ndarray, region) -> Reading:
        labels = getattr(region, "labels", None) or self._global_labels
        line_images = _split_lines(image)

        if len(line_images) <= 1 and not _has_wide_gap(image):
            # Use the tightly-cropped version, not the raw full crop -- same
            # "excess blank space confuses the recognizer" issue found and
            # fixed for the multi-line case applies here too.
            single = line_images[0] if line_images else image
            lines = [self._recognize_one_line(single, labels)]
        else:
            # Either genuinely multiple lines, or a single line with a wide
            # internal gap (e.g. a label far from a value) -- either way the
            # fast single-shot path can't report real positions, so use the
            # detector, which can.
            lines = self._recognize_multiline(image, labels)

        raw = "\n".join(lines)
        value, ok, values = _parse(raw.replace("\n", " "), labels)
        return Reading(raw_text=raw, value=value, values=values, ok=ok, lines=lines)
