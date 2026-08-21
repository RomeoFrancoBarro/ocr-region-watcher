"""Region selection: drag a box over a value like a snipping tool.

`SnipOverlay` works both standalone (its own Tk root) and as a `Toplevel` of
an already-running app window (so `App` can trigger it from an "Add Region"
button without running two competing Tk root instances).
"""
from __future__ import annotations

import tkinter as tk

import mss


def _virtual_screen_bounds() -> tuple[int, int, int, int]:
    with mss.mss() as sct:
        mon = sct.monitors[0]  # index 0 = full virtual screen, spans every monitor
        return mon["left"], mon["top"], mon["width"], mon["height"]


def _new_overlay_window(master: tk.Misc | None, label_text: str) -> tuple[tk.Misc, tk.Canvas, bool]:
    """Shared boilerplate for a one-shot fullscreen modal overlay: a
    semi-transparent crosshair-cursor window spanning every monitor, with
    an instruction label. Returns (win, canvas, standalone) -- callers add
    their own canvas bindings and Escape-to-cancel handler."""
    standalone = master is None
    left, top, width, height = _virtual_screen_bounds()

    win = tk.Tk() if standalone else tk.Toplevel(master)
    win.overrideredirect(True)
    win.geometry(f"{width}x{height}+{left}+{top}")
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.25)
    win.config(cursor="crosshair")
    win.grab_set()  # modal: input goes to the overlay, not whatever's behind it

    canvas = tk.Canvas(win, bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    tk.Label(
        win, text=label_text, fg="white", bg="black", font=("Segoe UI", 14),
    ).place(relx=0.5, rely=0.03, anchor="n")

    return win, canvas, standalone


class SnipOverlay:
    """One-shot fullscreen drag-to-select box, like Windows' Snipping Tool.
    Spans every monitor. Pass `master` (an existing Tk/Toplevel) when calling
    this from inside a running app; omit it to run standalone."""

    def __init__(self, master: tk.Misc | None = None) -> None:
        self._win, self._canvas, self._standalone = _new_overlay_window(
            master, "Drag a box around the value. Esc to cancel."
        )
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._win.bind("<Escape>", lambda e: self._finish(None))

        self._start_local = (0, 0)
        self._start_root = (0, 0)
        self._rect_id = None
        self.result: tuple | None = None

    def _on_press(self, event: tk.Event) -> None:
        self._start_local = (event.x, event.y)
        self._start_root = (event.x_root, event.y_root)
        self._rect_id = self._canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=2
        )

    def _on_drag(self, event: tk.Event) -> None:
        x0, y0 = self._start_local
        self._canvas.coords(self._rect_id, x0, y0, event.x, event.y)

    def _on_release(self, event: tk.Event) -> None:
        x0, y0 = self._start_root
        x1, y1 = event.x_root, event.y_root
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)
        if width < 3 or height < 3:
            return  # accidental click -- keep the overlay open, try again
        self._finish((left, top, width, height))

    def _finish(self, result: tuple | None) -> None:
        self.result = result
        self._win.grab_release()
        if self._standalone:
            self._win.quit()  # let mainloop() in run() return; it destroys after
        else:
            self._win.destroy()

    def run(self) -> tuple | None:
        if self._standalone:
            self._win.mainloop()
            self._win.destroy()
        else:
            self._win.wait_window()
        return self.result


def snip_region(master: tk.Misc | None = None) -> tuple[int, int, int, int] | None:
    """Show the drag-to-select overlay; return (left, top, width, height),
    or None if cancelled with Esc."""
    return SnipOverlay(master).run()


class PointOverlay:
    """One-shot fullscreen click-to-place, for a target position -- unlike
    SnipOverlay there's nothing to read here, just a screen point to click
    (and later paste into), so a single click places it, no drag needed."""

    def __init__(self, master: tk.Misc | None = None) -> None:
        self._win, self._canvas, self._standalone = _new_overlay_window(
            master, "Click where the target should be. Esc to cancel."
        )
        self._canvas.bind("<ButtonRelease-1>", self._on_click)
        self._win.bind("<Escape>", lambda e: self._finish(None))
        self.result: tuple | None = None

    def _on_click(self, event: tk.Event) -> None:
        self._finish((event.x_root, event.y_root))

    def _finish(self, result: tuple | None) -> None:
        self.result = result
        self._win.grab_release()
        if self._standalone:
            self._win.quit()
        else:
            self._win.destroy()

    def run(self) -> tuple | None:
        if self._standalone:
            self._win.mainloop()
            self._win.destroy()
        else:
            self._win.wait_window()
        return self.result


def snip_point(master: tk.Misc | None = None) -> tuple[int, int] | None:
    """Show the click-to-place overlay; return (x, y), or None if cancelled
    with Esc."""
    return PointOverlay(master).run()
