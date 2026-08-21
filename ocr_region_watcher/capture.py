"""Screen capture, built on mss.

A fresh `mss.mss()` context has real setup cost, so `ScreenGrabber` opens one
persistent capture context and reuses it every cycle instead of recreating it
per grab -- that overhead would otherwise dominate the "just crop a tiny
rectangle" work we actually want to be fast.
"""
from __future__ import annotations

import mss
import numpy as np


class ScreenGrabber:
    def __init__(self) -> None:
        self._sct = mss.mss()

    def grab(self, rect: dict) -> np.ndarray:
        """Capture `rect` (an mss-style {"left","top","width","height"} dict)
        and return it as a BGR numpy array (alpha channel dropped)."""
        shot = self._sct.grab(rect)
        return np.array(shot)[:, :, :3]

    def virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        """(left, top, width, height) spanning every monitor, not just the
        primary one -- needed so calibration can cover multi-monitor setups."""
        mon = self._sct.monitors[0]
        return mon["left"], mon["top"], mon["width"], mon["height"]

    def close(self) -> None:
        self._sct.close()
