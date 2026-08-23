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

No separate header bar or text strip any more -- both used to sit outside
the frame (stacking ~46px of extra chrome above/below it) and covered
whatever's actually on screen there. Naming/renaming already only ever
happened from the app window's own row (this floating widget's name field
was always read-only), so dropping the header cost no capability. What
used to be the strip is now a single small value chip tucked into the
frame's bottom-right corner -- see `_position_chip()`.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRegion
from PySide6.QtWidgets import QLabel, QWidget

from .style import MONO_SMALL

BORDER = 6
HANDLE = 16
MIN_SIZE = 24
CHIP_MARGIN = 10  # extra transparent space past the frame's own edge, purely so the corner chip can hang slightly outside the border without ever overlapping the click-through interior
COLOR_LOST = QColor("#ff4444")  # shared across every region, regardless of its own identity color -- see paintEvent for why
VALUE_TEXT_COLOR = QColor("#39ff14")
CHIP_BG = "#0b0d10"

# Each region gets its own identity color (cycled in creation order by
# app.py, persisted per-region in template snapshots) instead of one shared
# border color for every frame -- this is what lets the app window's row
# and the floating frame be matched up by color at a glance. Orange is
# deliberately not in here -- that's reserved for target markers, so a
# region never gets mistaken for one. COLOR_LOST above is intentionally
# separate from this list: it must stay recognizable as "something's
# wrong" even for a region whose own identity color happens to be
# red-ish -- see paintEvent's dashed-vs-solid line style for the other
# half of that.
REGION_COLORS = [
    QColor("#ff4d4d"),  # red
    QColor("#4d94ff"),  # blue
    QColor("#4dff88"),  # green
    QColor("#d94dff"),  # magenta
    QColor("#4de3ff"),  # cyan
    QColor("#e3ff4d"),  # yellow
]


def next_region_color(index: int) -> QColor:
    """1-based creation index -> a REGION_COLORS entry, cycling once more
    regions exist than colors."""
    return REGION_COLORS[(index - 1) % len(REGION_COLORS)]


class RegionWatcher(QWidget):
    name_changed = Signal(str)  # emitted when the name changes -- from the app window's row, the only place renaming happens now
    value_changed = Signal(str)  # emitted when the displayed (joined) value text changes
    locked_changed = Signal(bool)  # emitted when the sampled background stops/resumes matching -- lets the app window's row mirror it

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
        color: QColor | None = None,
    ) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.name = name
        self.formula_key = formula_key
        self.color = color or REGION_COLORS[0]  # callers always pass one (see app.py's next_region_color); this fallback is just defensive
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

        self._on_close = on_close
        self._on_change = on_change
        self._drag_origin = None
        self._resize_edge: str | None = None
        self._resize_origin = None
        self._moved_or_resized = False

        # Purely a value holder + change-signal source now, not shown
        # anywhere on this widget itself -- the app window's own row reads
        # this text, and the chip below mirrors it visually.
        self.value_edit = QLabel("--")

        self.value_chip = QLabel("--", self)
        self.value_chip.setFont(MONO_SMALL)
        # Informational only -- without this, the chip (sitting right over
        # the frame's own se resize-handle corner) would steal that
        # handle's clicks instead of letting them reach this widget's own
        # mousePressEvent below.
        self.value_chip.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._apply_geometry()
        self._style_chip()

    def set_name(self, name: str) -> None:
        """Rename from elsewhere (the app window's own row) -- this
        floating widget has no name display of its own to keep in sync
        any more, but still announces the change for anything else
        listening (e.g. a saved template's next snapshot)."""
        name = name.strip()
        if name and name != self.name:
            self.name = name
            self.name_changed.emit(name)

    # -- geometry -----------------------------------------------------
    def _apply_geometry(self) -> None:
        total_w = self.region_w + CHIP_MARGIN
        total_h = self.region_h + CHIP_MARGIN
        self.setGeometry(self.left, self.top, total_w, total_h)

        # The mask is everything EXCEPT the true interior (inset from the
        # capture rect's own edges, same inset capture_rect() uses) --
        # border, corner handles, and the value chip stay visible+
        # clickable; the interior is excluded from the window's hit-test
        # region entirely, which is what makes it genuinely click-through.
        # The chip's own margin lives entirely outside region_w/region_h,
        # so it never overlaps -- and never shrinks -- that interior.
        inset = max(BORDER, HANDLE // 2)
        full = QRegion(0, 0, total_w, total_h)
        interior = QRegion(
            inset, inset, max(self.region_w - 2 * inset, 0), max(self.region_h - 2 * inset, 0)
        )
        self.setMask(full.subtracted(interior))
        self._position_chip()
        self.update()

    def capture_rect(self) -> dict:
        """The interior rect to actually screenshot -- excludes the
        border and the corner resize-handles, so only real screen content
        gets read. Mirrors _apply_geometry()'s mask interior exactly, so
        what's captured is exactly what's click-through."""
        inset = max(BORDER, HANDLE // 2)
        return {
            "left": self.left + inset,
            "top": self.top + inset,
            "width": max(self.region_w - 2 * inset, 1),
            "height": max(self.region_h - 2 * inset, 1),
        }

    def _position_chip(self) -> None:
        """Bottom-right corner of the frame, nudged out into the margin
        so it visually hangs slightly past the border -- same spot the
        se resize-handle occupies, which is fine since the chip is
        transparent to mouse events (see __init__)."""
        self.value_chip.adjustSize()
        x = self.region_w - self.value_chip.width() + CHIP_MARGIN // 2
        y = self.region_h - self.value_chip.height() + CHIP_MARGIN // 2
        self.value_chip.move(max(0, x), max(0, y))

    def _style_chip(self) -> None:
        # Locked: this region's own identity color, matching its row's
        # swatch in the app window. Lost: always the shared alert color,
        # dashed -- never this region's own color, which could itself be
        # red-ish and mask the "something's wrong" signal (see paintEvent).
        border = f"1px solid {self.color.name()}" if self.locked else f"1px dashed {COLOR_LOST.name()}"
        self.value_chip.setStyleSheet(
            f"background-color: {CHIP_BG}; color: {VALUE_TEXT_COLOR.name()};"
            f" border: {border}; border-radius: 4px; padding: 1px 6px;"
        )

    # -- drawing --------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w, h = self.region_w, self.region_h

        # Same reasoning as _style_chip: locked draws in this region's own
        # color; lost always draws in the shared alert color AND switches
        # to a dashed line, so the two states stay visually distinct even
        # when a region's own color happens to be close to COLOR_LOST's.
        if self.locked:
            pen = QPen(self.color, BORDER)
        else:
            pen = QPen(COLOR_LOST, BORDER)
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(BORDER // 2, BORDER // 2, w - BORDER, h - BORDER)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color if self.locked else COLOR_LOST)
        for cx, cy in [(0, 0), (w, 0), (0, h), (w, h)]:
            painter.drawRect(cx - HANDLE // 2, cy - HANDLE // 2, HANDLE, HANDLE)

    def set_lines(self, lines: list) -> None:
        """Update the recognized text -- one region can still detect
        multiple lines internally (still feeds formula.compute() as
        separate values via _watcher_keys/_gather_readings in app.py),
        but on screen it's always shown as one joined value now, same as
        the app window's own row already did."""
        lines = lines or ["--"]
        joined = " | ".join(t or "--" for t in lines)
        self.value_edit.setText(joined)
        self.value_changed.emit(joined)
        self.value_chip.setText(joined)
        self._position_chip()

    def set_locked(self, locked: bool) -> None:
        if locked != self.locked:
            self.locked = locked
            self._style_chip()
            self.update()
            self.locked_changed.emit(locked)

    # -- mouse: move / resize / close ------------------------------------
    def _corner_at(self, x: int, y: int) -> str | None:
        w, h = self.region_w, self.region_h
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

    # Nothing but the value chip is a child widget any more, and it's
    # transparent to mouse events -- so every click anywhere on this
    # widget now reaches these handlers directly.
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
        self.deleteLater()  # schedules real C++ destruction -- close() alone only hides it,
        # which would leave is_alive() returning True forever for a target
        # still referenced by a saved event step
