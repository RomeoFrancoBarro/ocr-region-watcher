"""Runs a saved sequence of events -- each one "fire this target, after
waiting this many milliseconds" -- in order, automatically, optionally
looping back to the start when it finishes. Same model as ../events.py's
Tk version (reuses whatever `fire` does -- paste/click-only/M,W-cycling --
completely unchanged), just scheduled through Qt's QTimer instead of Tk's
`root.after`.

A real QTimer instance (not the static QTimer.singleShot) so Stop can
actually cancel a pending step -- `.stop()` on an in-flight singleShot
isn't possible since it hands back no handle.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer

if TYPE_CHECKING:
    from .target import TargetMarker


class EventSequencer(QObject):
    def __init__(self, fire, on_status, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fire = fire  # callable(TargetMarker) -> None
        self._on_status = on_status  # callable(str) -> None
        self.events: list[dict] = []  # each: {"target": TargetMarker, "delay": float} -- delay in ms
        self.loop = False
        self.running = False
        self._step_index = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire_step)

    def start(self) -> None:
        if self.running:
            return
        if not self.events:
            self._on_status("no events to run -- add at least one first")
            return
        self.running = True
        self._step_index = 0
        self._schedule_next()

    def stop(self) -> None:
        was_running = self.running
        self.running = False
        self._timer.stop()
        if was_running:
            self._on_status("stopped")

    def _schedule_next(self) -> None:
        if not self.running:
            return
        if self._step_index >= len(self.events):
            if self.loop and self.events:
                self._step_index = 0
            else:
                self.running = False
                self._on_status("finished")
                return

        event = self.events[self._step_index]
        delay_ms = max(0.0, float(event["delay"]))
        self._on_status(f"step {self._step_index + 1}/{len(self.events)}: waiting {delay_ms:g}ms...")
        self._timer.start(int(delay_ms))

    def _fire_step(self) -> None:
        if not self.running:  # Stop was pressed during the wait
            return
        event = self.events[self._step_index]
        target: "TargetMarker" = event["target"]
        # A target removed while it's still part of a saved sequence
        # shouldn't crash the run -- skip it and keep going.
        if not target.is_alive():
            self._on_status(f"step {self._step_index + 1}: that target was removed, skipping")
        else:
            self._fire(target)
        self._step_index += 1
        self._schedule_next()
