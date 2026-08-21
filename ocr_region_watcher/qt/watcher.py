"""Persistent, movable, resizable floating frame sitting over a screen
region.

Real click-through: `setMask()` on the actual top-level window carves the
interior out of its hit-test region entirely, so the OS treats that area
as if this window plain isn't there -- clicks and the visible content
underneath both pass straight through. That's a stronger guarantee than a
single-color-key transparency trick would give, which is what let the
target-marker's own crosshair intercept its own click earlier in this
project (a solid pixel drawn in the "wrong" place broke it; a masked-out
region has no pixels to accidentally get wrong).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRegion
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from .style import MONO

BORDER = 6
HANDLE = 16
STRIP_H = 22
HEADER_H = 24
MIN_SIZE = 24
COLOR_LOCKED = QColor("#00e5ff")
COLOR_LOST = QColor("#ff4444")
STRIP_TEXT_COLOR = QColor("#39ff14")


class RegionWatcher(QWidget):
    name_changed = Signal(str)  # emitted when the name changes, from either the floating header or elsewhere
    value_changed = Signal(str)  # emitted when the displayed (joined) value text changes

    def __init__(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        name: str,
        on_close,
        on_change=None,
        formula_key: str | None = None,
    ) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.name = name
        self.formula_key = formula_key
        self.left, self.top = left, top
        # NOT self.width/self.height -- QWidget already defines width()/
        # height() as methods; shadowing them with plain int attributes
        # would silently break anything that calls them as methods.
        self.region_w, self.region_h = max(width, MIN_SIZE), max(height, MIN_SIZE)
        self.locked = True
        self.labels: list = []  # kept for Recognizer's interface; unused for now
        self.ref_color: tuple | None = None  # set by the app after each move/resize
        self.last_hash = None  # set by the app; used to skip re-recognizing unchanged crops
        self.last_value: object = None  # set by the app; most recent parsed value, for formula.compute()
        self.last_values: list = []  # set by the app; every number parsed from this region's crop, in order
        self.strip_lines = 1
        self._lines_text: list[str] = ["--"]

        self._on_close = on_close
        self._on_change = on_change
        self._drag_origin = None
        self._resize_edge: str | None = None
        self._resize_origin = None
        self._moved_or_resized = False

        self.header = QWidget(self)
        self.header.setStyleSheet("background-color: black;")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)  # zero -- the gray name box should fill the black bar exactly, not float inset within it
        header_layout.setSpacing(0)

        # Balances the close button's width on the opposite side -- without
        # this, name_edit's stretch fills everything *except* the close
        # button (which only sits on the right), so the gray box itself
        # ends up shifted left within the black bar even with its own text
        # centered inside it.
        left_spacer = QWidget()
        left_spacer.setFixedWidth(20)
        left_spacer.setStyleSheet("background-color: black;")  # explicit -- a bare QWidget isn't guaranteed to show the parent's background through it
        header_layout.addWidget(left_spacer)

        self.name_edit = QLineEdit(name)
        self.name_edit.setFont(MONO)
        self.name_edit.setAlignment(Qt.AlignCenter)
        self.name_edit.setFixedHeight(HEADER_H)  # QLineEdit's own vertical size policy is Fixed -- zero layout margins alone don't stretch it to fill the header
        self.name_edit.setReadOnly(True)  # rename from the app window's own row instead -- set_name() still updates this
        self.name_edit.setStyleSheet("background-color: #222222; color: #aaaaaa; border: none; padding: 1px;")
        self.name_edit.setToolTip("Read-only here -- rename this region from its row in the app window")
        header_layout.addWidget(self.name_edit, 1)  # fills the space value_edit used to share with it

        # Not shown in the header -- it'd duplicate the strip already
        # showing this same value below the frame. Kept as a plain (never
        # added to any layout) QLineEdit purely so set_lines() below has
        # somewhere to hold the joined text and fire value_changed from,
        # which the app window's own row displays instead.
        self.value_edit = QLineEdit("--")
        self.value_edit.setReadOnly(True)

        close_btn = QPushButton("x")
        close_btn.setFixedWidth(20)
        close_btn.setToolTip("Remove this region")
        close_btn.setStyleSheet("color: #ff5555; background: black; border: none; font-weight: bold;")
        close_btn.clicked.connect(self._close)
        header_layout.addWidget(close_btn)

        self._apply_geometry()

    def set_name(self, name: str) -> None:
        """Rename from elsewhere (e.g. the app window's own row) -- keeps
        the floating header in sync."""
        name = name.strip()
        if name and name != self.name:
            self.name = name
            self.name_edit.setText(name)
            self.name_changed.emit(name)

    # -- geometry -----------------------------------------------------
    def _apply_geometry(self) -> None:
        strip_h = STRIP_H * self.strip_lines
        total_h = HEADER_H + self.region_h + strip_h
        self.setGeometry(self.left, self.top - HEADER_H, self.region_w, total_h)
        self.header.setGeometry(0, 0, self.region_w, HEADER_H)

        # The mask is everything EXCEPT the true interior (inset from the
        # capture rect's own edges, same inset capture_rect() uses) --
        # header/border/handles/strip stay visible+clickable; the
        # interior is excluded from the window's hit-test region
        # entirely, which is what makes it genuinely click-through.
        inset = max(BORDER, HANDLE // 2)
        full = QRegion(0, 0, self.region_w, total_h)
        interior = QRegion(
            inset, HEADER_H + inset, max(self.region_w - 2 * inset, 0), max(self.region_h - 2 * inset, 0)
        )
        self.setMask(full.subtracted(interior))
        self.update()

    def capture_rect(self) -> dict:
        """The interior rect to actually screenshot -- excludes the
        border, the corner resize-handles, and the text strip, so only
        real screen content gets read. Mirrors _apply_geometry()'s mask
        interior exactly, so what's captured is exactly what's
        click-through."""
        inset = max(BORDER, HANDLE // 2)
        return {
            "left": self.left + inset,
            "top": self.top + inset,
            "width": max(self.region_w - 2 * inset, 1),
            "height": max(self.region_h - 2 * inset, 1),
        }

    # -- drawing --------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        color = COLOR_LOCKED if self.locked else COLOR_LOST
        w, h, y0 = self.region_w, self.region_h, HEADER_H

        painter.setPen(QPen(color, BORDER))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(BORDER // 2, y0 + BORDER // 2, w - BORDER, h - BORDER)

        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for cx, cy in [(0, y0), (w, y0), (0, y0 + h), (w, y0 + h)]:
            painter.drawRect(cx - HANDLE // 2, cy - HANDLE // 2, HANDLE, HANDLE)

        strip_h = STRIP_H * self.strip_lines
        painter.setBrush(QColor("black"))
        painter.drawRect(0, y0 + h, w, strip_h)

        painter.setPen(STRIP_TEXT_COLOR)
        painter.setFont(MONO)
        for i, text in enumerate(self._lines_text):
            row_top = y0 + h + i * STRIP_H
            painter.drawText(6, row_top + STRIP_H // 2 + 4, text or "--")

    def set_lines(self, lines: list) -> None:
        """Update the recognized text, one row per detected line in the
        captured image -- growing/shrinking the strip to match. Also
        mirrors into the header's read-only value field."""
        lines = lines or ["--"]
        self._lines_text = lines
        if len(lines) != self.strip_lines:
            self.strip_lines = len(lines)
            self._apply_geometry()
        else:
            self.update()
        joined = " | ".join(t or "--" for t in lines)
        self.value_edit.setText(joined)
        self.value_changed.emit(joined)

    def set_locked(self, locked: bool) -> None:
        if locked != self.locked:
            self.locked = locked
            self.update()

    # -- mouse: move / resize / close ------------------------------------
    def _corner_at(self, x: int, y: int) -> str | None:
        # x, y here are already local to *this widget*; the capture area
        # itself starts at local y == HEADER_H, so offset before checking.
        w, h = self.region_w, self.region_h
        ly = y - HEADER_H
        near_left, near_right = 0 <= x <= HANDLE, w - HANDLE <= x <= w
        near_top, near_bottom = 0 <= ly <= HANDLE, h - HANDLE <= ly <= h
        if near_top and near_left:
            return "nw"
        if near_top and near_right:
            return "ne"
        if near_bottom and near_left:
            return "sw"
        if near_bottom and near_right:
            return "se"
        return None

    # Events landing on the header (or its child controls) never reach
    # these handlers at all -- the header is a real child widget covering
    # that area, and Qt routes mouse events to the topmost/innermost
    # widget under the cursor. Only the border/handle/strip area (which
    # has no child widget, just painted content) reaches the top-level
    # widget's own handlers -- exactly mirroring the original's single
    # canvas that drew and handled all of that together.
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        corner = self._corner_at(pos.x(), pos.y())
        if corner:
            self._resize_edge = corner
            self._resize_origin = (event.globalPosition().toPoint(), self.left, self.top, self.region_w, self.region_h)
            self._moved_or_resized = False
            return
        self._drag_origin = (event.globalPosition().toPoint(), self.left, self.top)
        self._moved_or_resized = False

    def mouseMoveEvent(self, event) -> None:
        if self._resize_edge:
            gpos = event.globalPosition().toPoint()
            origin, oleft, otop, ow, oh = self._resize_origin
            dx, dy = gpos.x() - origin.x(), gpos.y() - origin.y()
            left, top, rw, rh = oleft, otop, ow, oh
            edge = self._resize_edge
            if "n" in edge:
                top, rh = otop + dy, oh - dy
            if "s" in edge:
                rh = oh + dy
            if "w" in edge:
                left, rw = oleft + dx, ow - dx
            if "e" in edge:
                rw = ow + dx
            self.left, self.top = left, top
            self.region_w, self.region_h = max(rw, MIN_SIZE), max(rh, MIN_SIZE)
            self._apply_geometry()
            self._moved_or_resized = True
        elif self._drag_origin:
            gpos = event.globalPosition().toPoint()
            origin, oleft, otop = self._drag_origin
            dx, dy = gpos.x() - origin.x(), gpos.y() - origin.y()
            self.left, self.top = oleft + dx, otop + dy
            self._apply_geometry()
            self._moved_or_resized = True

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        self._resize_edge = None
        self._resize_origin = None
        if self._moved_or_resized:
            self._moved_or_resized = False
            if self._on_change:
                self._on_change(self)

    def _close(self) -> None:
        if self._on_close:
            self._on_close(self)
        self.close()
        self.deleteLater()  # actually destroy it, not just hide -- see target.py's _close for why
