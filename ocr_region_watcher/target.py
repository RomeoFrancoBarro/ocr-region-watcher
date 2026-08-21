"""A small floating marker at a fixed screen point -- the write-back
counterpart to RegionWatcher's read side. No capture rect, no OCR, no
resize handles (a point, not an area to read) -- just a name (keys which
formula.compute() result to paste, see app.py's Send button) and a
position to click+paste into. Never fires on its own; only an explicit
Send action in app.py ever calls inject.click_and_paste() for it.
"""
from __future__ import annotations

import tkinter as tk

SIZE = 22
HEADER_H = 22
TRANSPARENT_KEY = "magenta"
COLOR = "#ff9900"


class TargetMarker:
    def __init__(
        self,
        master: tk.Misc,
        x: int,
        y: int,
        name: str,
        on_close,
        on_change=None,
    ) -> None:
        self.name = name
        self.x, self.y = x, y
        self._on_close = on_close
        self._on_change = on_change
        self._drag_origin: tuple | None = None

        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=TRANSPARENT_KEY)
        self.win.attributes("-transparentcolor", TRANSPARENT_KEY)

        header = tk.Frame(self.win, bg="black", height=HEADER_H)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        self.name_var = tk.StringVar(value=self.name)
        name_entry = tk.Entry(
            header, textvariable=self.name_var, width=6, font=("Consolas", 9),
            bg="#222222", fg="white", insertbackground="white", relief="flat",
        )
        name_entry.pack(side="left", padx=(3, 2), pady=2)
        name_entry.bind("<Return>", self.on_name_committed)
        name_entry.bind("<FocusOut>", self.on_name_committed)

        close_btn = tk.Button(
            header, text="x", command=self._close, font=("Consolas", 8, "bold"),
            fg="#ff5555", bg="black", activebackground="black", activeforeground="#ff8888",
            relief="flat", bd=0, padx=3, pady=0,
        )
        close_btn.pack(side="right", padx=(2, 3))

        self.canvas = tk.Canvas(self.win, width=SIZE, height=SIZE, bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack(side="top")
        self.canvas.create_oval(2, 2, SIZE - 2, SIZE - 2, outline=COLOR, width=2)
        self.canvas.create_line(SIZE // 2, 0, SIZE // 2, SIZE, fill=COLOR, width=1)
        self.canvas.create_line(0, SIZE // 2, SIZE, SIZE // 2, fill=COLOR, width=1)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self._apply_geometry()

    def on_name_committed(self, event: tk.Event | None = None) -> None:
        new_name = self.name_var.get().strip()
        if new_name:
            self.name = new_name
        else:
            self.name_var.set(self.name)  # reject blank, revert to the last real name

    # -- geometry: centered on (x, y), header sits above it ----------------
    def _apply_geometry(self) -> None:
        left = self.x - SIZE // 2
        top = self.y - SIZE // 2
        self.win.geometry(f"{SIZE}x{SIZE + HEADER_H}+{left}+{top - HEADER_H}")

    # -- mouse: drag to reposition, no resize (it's a point) ---------------
    def _on_press(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root, event.y_root, self.x, self.y)

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_origin:
            ox, oy, ox0, oy0 = self._drag_origin
            self.x = ox0 + (event.x_root - ox)
            self.y = oy0 + (event.y_root - oy)
            self._apply_geometry()

    def _on_release(self, event: tk.Event) -> None:
        if self._drag_origin:
            self._drag_origin = None
            if self._on_change:
                self._on_change(self)

    def _close(self) -> None:
        self.win.destroy()
        if self._on_close:
            self._on_close(self)
