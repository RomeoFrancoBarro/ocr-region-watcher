"""A persistent, movable, resizable floating frame sitting directly over a
region of the screen -- a colored border with a fully transparent (and
click-through) interior, so the real content underneath stays visible and
capturable, with a text strip below showing the live-recognized value.

A header above the frame holds the region's name (editable directly --
this is what a formula will later key off of, e.g. "M" or "W") and a
read-only mirror of the recognized value (a real Entry, so it's selectable/
copyable, not just the canvas display).

Drag the border or the text strip to move the frame. Drag a corner handle to
resize it. Click the x to remove it.
"""
from __future__ import annotations

import tkinter as tk

BORDER = 6
HANDLE = 16
STRIP_H = 22
HEADER_H = 24
MIN_SIZE = 24
TRANSPARENT_KEY = "magenta"
COLOR_LOCKED = "#00e5ff"
COLOR_LOST = "#ff4444"


class RegionWatcher:
    def __init__(
        self,
        master: tk.Misc,
        left: int,
        top: int,
        width: int,
        height: int,
        name: str,
        on_close,
        on_change=None,
        formula_key: str | None = None,
    ) -> None:
        self.name = name
        # When set, this is the fixed key this region's value is stored
        # under in formula.compute()'s readings dict -- independent of
        # `name`/`name_var`, which stays purely a display label the user
        # can freely rename without touching the wiring. None (the default)
        # means "wire off the name field itself", today's behavior.
        self.formula_key = formula_key
        self.left, self.top = left, top
        self.width, self.height = max(width, MIN_SIZE), max(height, MIN_SIZE)
        self.locked = True
        self.labels: list = []  # kept for Recognizer's interface; unused for now
        self.ref_color: tuple | None = None  # set by the app after each move/resize
        self.last_hash = None  # set by the app; used to skip re-recognizing unchanged crops
        self.last_value: object = None  # set by the app; most recent parsed value, for formula.compute()
        self.last_values: list = []  # set by the app; every number parsed from this region's crop, in
        # order -- lets a name like "M,PM" split one region into two keyed readings
        self.strip_lines = 1  # how many text rows the strip currently shows
        self._line_text_ids: list = []
        self._on_close = on_close
        self._on_change = on_change
        self._drag_origin: tuple | None = None
        self._resize_edge: str | None = None
        self._resize_origin: tuple | None = None
        self._moved_or_resized = False

        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=TRANSPARENT_KEY)
        self.win.attributes("-transparentcolor", TRANSPARENT_KEY)

        # Header is packed *above* the canvas, so the canvas's own top-left
        # still lands exactly at (self.left, self.top) once the window is
        # shifted up by HEADER_H in _apply_geometry -- capture_rect() and all
        # the existing canvas-local hit-testing math stay unchanged.
        header = tk.Frame(self.win, bg="black", height=HEADER_H)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        self.name_var = tk.StringVar(value=self.name)
        name_entry = tk.Entry(
            header, textvariable=self.name_var, width=6, font=("Consolas", 9),
            bg="#222222", fg="white", insertbackground="white", relief="flat",
        )
        name_entry.pack(side="left", padx=(3, 2), pady=3)
        name_entry.bind("<Return>", self.on_name_committed)
        name_entry.bind("<FocusOut>", self.on_name_committed)

        self.value_var = tk.StringVar(value="--")
        value_entry = tk.Entry(
            header, textvariable=self.value_var, font=("Consolas", 9), state="readonly",
            fg="#39ff14", readonlybackground="#222222", relief="flat", justify="right",
        )
        value_entry.pack(side="left", fill="x", expand=True, padx=(0, 2), pady=3)

        close_btn = tk.Button(
            header, text="x", command=self._close, font=("Consolas", 8, "bold"),
            fg="#ff5555", bg="black", activebackground="black", activeforeground="#ff8888",
            relief="flat", bd=0, padx=3, pady=0,
        )
        close_btn.pack(side="right", padx=(2, 3))

        self.canvas = tk.Canvas(self.win, bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self._apply_geometry()
        self._redraw()

    def on_name_committed(self, event: tk.Event | None = None) -> None:
        new_name = self.name_var.get().strip()
        if new_name:
            self.name = new_name
        else:
            self.name_var.set(self.name)  # reject blank, revert to the last real name

    # -- geometry -----------------------------------------------------
    def _apply_geometry(self) -> None:
        strip_h = STRIP_H * self.strip_lines
        total_h = HEADER_H + self.height + strip_h
        self.win.geometry(f"{self.width}x{total_h}+{self.left}+{self.top - HEADER_H}")

    def capture_rect(self) -> dict:
        """The interior rect to actually screenshot -- excludes the border,
        the corner resize-handles, and the text strip, so only real screen
        content gets read. The handles are drawn slightly larger than the
        border (HANDLE > BORDER), so insetting by BORDER alone would leave
        a sliver of opaque handle color inside the capture at each corner --
        exactly where left-aligned text tends to start."""
        inset = max(BORDER, HANDLE // 2)
        return {
            "left": self.left + inset,
            "top": self.top + inset,
            "width": max(self.width - 2 * inset, 1),
            "height": max(self.height - 2 * inset, 1),
        }

    # -- drawing --------------------------------------------------------
    def _redraw(self) -> None:
        self.canvas.delete("all")
        w, h = self.width, self.height
        color = COLOR_LOCKED if self.locked else COLOR_LOST

        self._border_id = self.canvas.create_rectangle(
            BORDER // 2, BORDER // 2, w - BORDER // 2, h - BORDER // 2, outline=color, width=BORDER
        )
        self._handle_ids = []
        for cx, cy in [(0, 0), (w, 0), (0, h), (w, h)]:
            hid = self.canvas.create_rectangle(
                cx - HANDLE // 2, cy - HANDLE // 2, cx + HANDLE // 2, cy + HANDLE // 2, fill=color, outline=""
            )
            self._handle_ids.append(hid)

        strip_h = STRIP_H * self.strip_lines
        self.canvas.create_rectangle(0, h, w, h + strip_h, fill="black", outline="")

        self._line_text_ids = []
        for i in range(self.strip_lines):
            row_mid = h + i * STRIP_H + STRIP_H // 2
            text_id = self.canvas.create_text(6, row_mid, anchor="w", fill="#39ff14", font=("Consolas", 10), text="--")
            self._line_text_ids.append(text_id)

    def set_lines(self, lines: list) -> None:
        """Update the recognized text, one row per detected line in the
        captured image -- growing/shrinking the strip to match, so a
        multi-line value shows as multiple lines instead of being forced
        into one. Also mirrors into the header's read-only value entry."""
        lines = lines or ["--"]
        if len(lines) != self.strip_lines:
            self.strip_lines = len(lines)
            self._apply_geometry()
            self._redraw()
        for text_id, text in zip(self._line_text_ids, lines):
            self.canvas.itemconfig(text_id, text=text or "--")
        self.value_var.set(" | ".join(t or "--" for t in lines))

    def set_locked(self, locked: bool) -> None:
        # Recolor only -- must NOT go through _redraw(), which deletes and
        # recreates every canvas item (including the text rows, reset to
        # their "--" placeholder). A lock state that flickers even briefly
        # would otherwise silently wipe whatever value was just displayed,
        # with nothing to repopulate it until the crop actually changes again.
        if locked != self.locked:
            self.locked = locked
            color = COLOR_LOCKED if locked else COLOR_LOST
            self.canvas.itemconfig(self._border_id, outline=color)
            for hid in self._handle_ids:
                self.canvas.itemconfig(hid, fill=color)

    # -- mouse: move / resize / close ------------------------------------
    def _corner_at(self, x: int, y: int) -> str | None:
        # Bounded on both sides so a click *below* the frame (in the strip,
        # or off the window entirely) never misreads as "near the bottom
        # edge" just because y is large -- it must be within the frame.
        w, h = self.width, self.height
        near_left, near_right = 0 <= x <= HANDLE, w - HANDLE <= x <= w
        near_top, near_bottom = 0 <= y <= HANDLE, h - HANDLE <= y <= h
        if near_top and near_left:
            return "nw"
        if near_top and near_right:
            return "ne"
        if near_bottom and near_left:
            return "sw"
        if near_bottom and near_right:
            return "se"
        return None

    def _on_press(self, event: tk.Event) -> None:
        x, y = event.x, event.y

        corner = self._corner_at(x, y)
        if corner:
            self._resize_edge = corner
            self._resize_origin = (event.x_root, event.y_root, self.left, self.top, self.width, self.height)
            self._moved_or_resized = False
            return

        # Not a corner -> anywhere else on the canvas (border or the value
        # strip) is a move-drag. Closing is a real Button in the header now,
        # not a hand-rolled hit zone here.
        self._drag_origin = (event.x_root, event.y_root, self.left, self.top)
        self._moved_or_resized = False

    def _on_drag(self, event: tk.Event) -> None:
        if self._resize_edge:
            ox, oy, oleft, otop, ow, oh = self._resize_origin
            dx, dy = event.x_root - ox, event.y_root - oy
            left, top, width, height = oleft, otop, ow, oh
            edge = self._resize_edge
            if "n" in edge:
                top = otop + dy
                height = oh - dy
            if "s" in edge:
                height = oh + dy
            if "w" in edge:
                left = oleft + dx
                width = ow - dx
            if "e" in edge:
                width = ow + dx
            self.left, self.top = left, top
            self.width, self.height = max(width, MIN_SIZE), max(height, MIN_SIZE)
            self._apply_geometry()
            self._redraw()
            self._moved_or_resized = True
        elif self._drag_origin:
            ox, oy, oleft, otop = self._drag_origin
            dx, dy = event.x_root - ox, event.y_root - oy
            self.left, self.top = oleft + dx, otop + dy
            self._apply_geometry()
            self._moved_or_resized = True

    def _on_release(self, event: tk.Event) -> None:
        self._drag_origin = None
        self._resize_edge = None
        self._resize_origin = None
        if self._moved_or_resized:
            self._moved_or_resized = False
            if self._on_change:
                self._on_change(self)

    def _close(self) -> None:
        self.win.destroy()
        if self._on_close:
            self._on_close(self)
