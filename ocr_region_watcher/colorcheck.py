"""Cheap "is this still the region I calibrated" sanity check.

This is deliberately not a region *finder*. It doesn't scan the screen for a
color -- calibration already told us exactly where the value is. This just
samples the crop's border pixels (the background around the value, not the
text itself) each cycle and compares them to what was recorded at
calibration time. If they've drifted too far, the window moved, the page
scrolled, or the layout changed -- and the reading should not be trusted
until the region is recalibrated.
"""
from __future__ import annotations

import numpy as np

DEFAULT_TOLERANCE = 40  # per-channel; generous enough to absorb rendering/anti-aliasing noise


def sample_reference_color(image: np.ndarray) -> tuple[int, int, int]:
    """Average BGR color of a crop's border pixels."""
    border = np.concatenate([image[0, :], image[-1, :], image[:, 0], image[:, -1]])
    b, g, r = border.mean(axis=0)[:3]
    return int(b), int(g), int(r)


def still_locked(
    image: np.ndarray,
    reference_bgr: tuple[int, int, int],
    tolerance: int = DEFAULT_TOLERANCE,
) -> bool:
    sampled = sample_reference_color(image)
    return all(abs(a - b) <= tolerance for a, b in zip(sampled, reference_bgr))
