"""Runs a saved sequence of events -- each one "fire this target, after
waiting this many seconds" -- in order, automatically, optionally looping
back to the start when it finishes. This is the automated counterpart to
manually clicking each target's own Send button in sequence: it reuses
that exact same per-target logic (paste/click-only/M,W-cycling all
untouched), just triggers it on a timer instead of a click.

Scheduled entirely through Tk's own `root.after` -- no thread -- so Start
returns immediately (the app stays responsive) and Stop takes effect the
moment it's pressed, not after some in-flight action finishes.
"""
from __future__ import annotations

import tkinter as tk

from .target import TargetMarker


class EventSequencer:
    def __init__(self, root: tk.Misc, fire, on_status) -> None:
        self._root = root
        self._fire = fire  # callable(TargetMarker) -> None -- reuses App._on_send_target
        self._on_status = on_status  # callable(str) -> None
        self.events: list[dict] = []  # each: {"target": TargetMarker, "delay": float}
        self.loop = False
        self.running = False
        self._step_index = 0
        self._after_id: str | None = None

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
        if self._after_id is not None:
            self._root.after_cancel(self._after_id)
            self._after_id = None
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
        delay = max(0.0, float(event["delay"]))
        self._on_status(f"step {self._step_index + 1}/{len(self.events)}: waiting {delay:g}s...")
        self._after_id = self._root.after(int(delay * 1000), self._fire_step)

    def _fire_step(self) -> None:
        if not self.running:  # Stop was pressed during the wait
            return
        event = self.events[self._step_index]
        target: TargetMarker = event["target"]
        # A target removed (its "x" clicked) while it's still part of a
        # saved sequence shouldn't crash the run -- skip it and keep going,
        # same "report, don't stop the app" spirit as everywhere else here.
        if not target.win.winfo_exists():
            self._on_status(f"step {self._step_index + 1}: that target was removed, skipping")
        else:
            self._fire(target)
        self._step_index += 1
        self._schedule_next()
